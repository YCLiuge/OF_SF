"""National encounter gift markers before battle, settlement, old saves and replay."""
import argparse
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_shop import now
from realm_rules import configure
from test_game_realm import login, join
from test_native_gacha import call
from test_native_ground_loot import encounter, kill, travel
from utils import db

OK=pb.StatusCode_OK
BOX=300000


def marks(sock,seq,keys):
    reply=call(sock,"BattleEncounterInfo",seq,encounter_ids=keys)
    assert reply.status==OK,reply
    return {r.battle_id:r.box_id for r in reply.encounters}


def open_chest(sock,seq,idx):
    return call(sock,"TreasureBoxOpen",seq,box_type=1,treasure_box_index=idx,loc_type=0)


def claim(sock,seq,idx,entry=-1):
    return call(sock,"TreasureBoxPickup",seq,box_type=1,box_index=idx,pick_index=entry,loc_type=0)


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    accounts=[identity.create_profile(n) for n in ("标记甲","标记乙")]
    configure(root,{"monster_gift_percent":50,"ordinary_ground_drop_percent":100})
    host=LocalGameService(identity,root,resources,port=0)
    def connect(i):
        sock,_,pid=login(host.port,identity.issue_ticket(*accounts[i]))
        return sock,pid,join(sock)[1].data
    host.start()
    try:
        a,pid,_=connect(0)
        b,_,_=connect(1)
        with a,b,patch("private_encounter_gifts.random",Mock()) as rng:
            rng.randrange.side_effect=[0,99,99,0]
            travel(a,10)
            travel(b,10)
            assert marks(a,11,[1001,1002])=={1001:BOX,1002:0}
            assert marks(a,12,[1002,1001])=={1001:BOX,1002:0}
            assert marks(b,11,[1001,1002])=={1001:0,1002:BOX}
            assert rng.randrange.call_count==4
            assert encounter(a,13).encounter.box_id==BOX
            died=kill(a,14)
            assert died.status==OK and not died.HasField("drop_item")
            won=encounter(a,15,pb.Finish)
            assert won.encounter.box_id==won.dynamic_treasure_box_base_info.box_id==BOX
            idx=won.dynamic_treasure_box_base_info.index
            assert idx and encounter(a,16,pb.Finish)==won
            assert not open_chest(b,17,idx).items
            travel(a,18,channel=2)
            assert marks(a,19,[1001])=={1001:0}
            assert open_chest(a,20,idx).status!=OK and claim(a,21,idx).status!=OK
            travel(a,22,channel=1)
            assert marks(a,23,[1001])=={1001:BOX}
            with patch("private_field_activities.now",return_value=now()+timedelta(seconds=181)):
                assert marks(a,50,[1001])=={1001:0}
                assert open_chest(a,51,idx).status!=OK and claim(a,52,idx).status!=OK
            # An old server already promised every active fight its win chest.
            with host.lock,db.conn:
                db.conn.execute("INSERT INTO realm_field_activities VALUES(?,?,?,?,?,?,?,?,?)",
                                (pid,1,2,1003,pb.Start,0,0,int(now().timestamp()),0))
            assert marks(a,24,[1003])=={1003:BOX}
            assert rng.randrange.call_count==4
        host.stop()
        host=LocalGameService(identity,root,resources,port=0)
        host.start()
        a,_,_=connect(0)
        with a,patch("private_encounter_gifts.random",Mock()) as rng:
            rng.randrange.side_effect=[99]
            assert marks(a,25,[1001,1002])=={1001:BOX,1002:0}
            opened=open_chest(a,26,idx)
            assert opened.status==OK and len(opened.items)>=2
            first=claim(a,27,idx,1)
            assert first.status==OK and len(first.items)==1 and claim(a,27,idx,1)==first
            rest=claim(a,28,idx)
            assert len(rest.items)==len(opened.items)-1 and claim(a,28,idx)==rest
            assert not claim(a,29,idx).items and marks(a,30,[1001])=={1001:0}
            assert encounter(a,31,key=1002).encounter.box_id==0
            won=encounter(a,32,pb.Finish,key=1002)
            assert won.status==OK and not won.HasField("dynamic_treasure_box_base_info")
            assert rng.randrange.call_count==0
            with patch("private_field_activities.now",return_value=now()+timedelta(minutes=11)):
                assert marks(a,33,[1001])=={1001:0}
                assert marks(a,34,[1001])=={1001:0}
                assert rng.randrange.call_count==1
        print("PASS: pre-battle per-role marker, no re-roll on query/restart, marked and unmarked "
              "victories, one scoped gift, legacy active fight, cooldown rotation and loot replay.")
    finally:
        host.stop()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources.resolve())
