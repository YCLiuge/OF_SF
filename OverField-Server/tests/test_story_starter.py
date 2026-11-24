"""Replay QuestSystem.AcceptFirstQuest's native home-scene 1451 followed by journal acceptance."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService,pb
from local_identity import LocalIdentity
from test_game_realm import join_after_intro,login
from test_observed_initialization import call


def run(root,resources):
    ident=LocalIdentity(root/"identity.sqlite3")
    users=[ident.create_profile(n) for n in ("主线甲","主线乙","已核对的旧角色")]
    with patch.dict(os.environ,{"OF_STAGE_DIAGNOSTICS":"1"}):
        host=LocalGameService(ident,root,resources,port=0)
    def connect(i):
        sock,_,pid=login(host.port,ident.issue_ticket(*users[i]))
        profile,_=join_after_intro(sock)
        return sock,pid,profile
    def count(player,template):
        with closing(sqlite3.connect(root/"game.sqlite")) as dbc:
            row=dbc.execute("SELECT item_detail_blob FROM items WHERE player_id=? AND item_id=?",(player,template)).fetchone()
            return pb.ItemDetail.FromString(row[0]).main_item.base_item.num if row else 0
    def fixture(player,template,amount):
        from utils import db
        from utils.pb_create import make_item
        with host.lock,db.conn:
            db.set_item_detail(player,make_item(template,amount,player).SerializeToString(),template)
    def request(sock,template=1003,qty=1):
        return call(sock,"GamePlayReward",reward_id=template,num=qty)
    host.start()
    try:
        one,owner,_=connect(0)
        other,peer,_=connect(1)
        legacy,old,_=connect(2)
        with one,other,legacy:
            assert not count(owner,100000021) and not count(peer,100000021)
            fixture(owner,4050,5)
            assert request(one,qty=0).status==pb.StatusCode_BAD_REQ
            assert request(one,qty=0xffffffff).status==pb.StatusCode_BAD_REQ
            assert request(one,0xffffffff).status==pb.StatusCode_CFG_NOT_FOUND
            got=request(one)
            assert got.status==pb.StatusCode_OK and not got.HasField("dynamic_treasure_box_info")
            received={d.main_item.item_id:d.main_item.base_item.num for d in got.items}
            assert len(received)==16 and received[100000021]==1 and 4050 not in received
            assert not request(one).items and count(owner,4050)==5 and count(owner,100000021)==1
            assert not count(peer,100000021)
            # The existing two canonical combat targets remain dynamic boxes.
            assert request(other,1001).dynamic_treasure_box_info.box_id==3001
            assert request(other,1002).dynamic_treasure_box_info.box_id==3002
            # An imported save with a physical journal marker already received its original starter contents.
            fixture(old,100000021,1)
            assert not request(legacy).items and not count(old,4050)
            assert call(other,"ChangeSceneChannel",scene_id=100,channel_label=1).status==pb.StatusCode_OK
            assert request(other).status==pb.StatusCode_BAD_REQ
        host.stop()
        host.start()
        again,player,profile=connect(0)
        with again:
            assert player==owner and count(player,100000021)==1 and count(player,4050)==5
            assert any(q.quest_id==100011 for q in profile.quest_detail.quests)
            assert call(again,"AcceptQuest",quest_id=1000001,quest_type=pb.EQuestType_MainQuest).quest.quest_id==100011
            assert not request(again).items
        rows=[]
        for f in (host.stages.previous,host.stages.path):
            if f.exists():
                rows.extend(json.loads(line).get("play_reward") for line in f.read_bytes().splitlines())
        assert [1003,1] in rows and [1003,"other"] in rows and ["other",1] in rows
    finally:
        host.stop()
    print("PASS original quest journal request 1003 x1, authored initial rewards, old-save migration, repeat/restart protection, scene guard and redacted templates.")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),ap.parse_args().resources)
