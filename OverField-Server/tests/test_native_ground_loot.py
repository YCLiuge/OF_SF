"""Native kill, gift-box and bag regression using two accounts, two channels and a restart."""
import argparse
from pathlib import Path
from random import Random
from datetime import timedelta
from private_shop import now
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from private_inventory import capacity, replace_slots, slots
from private_ground_loot import equipment_level
from realm_rules import configure
from test_game_realm import join, login, next_msg
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item

OK = pb.StatusCode_OK


def travel(sock,seq,scene=1,channel=1):
    result = call(sock,"ChangeSceneChannel",seq,scene_id=scene,channel_label=channel)
    assert result.status==OK,result
    return next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice).data


def kill(sock,seq,unit=1205,index=0):
    return call(sock,"MonsterDead",seq,monster_id=unit,monster_index=index)


def pick(sock,seq,index,entry=-1):
    return call(sock,"Pickup",seq,drop_item_index=index,pick_index=entry)


def encounter(sock,seq,phase=pb.Start,key=1001):
    return call(sock,"BattleEncounterStateUpdate",seq,encounter_id=key,battle_state=phase)


def run(root,resources):
    identity = LocalIdentity(root/"identity.sqlite3")
    accounts = [identity.create_profile(name) for name in ("掉落甲","掉落乙")]
    configure(root,{"monster_gift_percent":0,"ordinary_ground_drop_percent":100})
    host = LocalGameService(identity,root,resources,port=0)
    def connect(i):
        sock,_,pid = login(host.port,identity.issue_ticket(*accounts[i]))
        main,scene = join(sock)
        assert (main.player_drop_rate_info.kill_drop_rate,main.player_drop_rate_info.treasure_drop_rate)==(100,100)
        return sock,pid,scene.data
    host.start()
    try:
        assert equipment_level(1101100,2,100)==1
        a,pid,_ = connect(0)
        b,_,_ = connect(1)
        with a,b:
            travel(a,10)
            travel(b,10)
            assert kill(a,11).status!=OK
            assert encounter(a,12).status==OK
            assert kill(a,13,9007).status!=OK
            assert kill(a,14,index=1).status!=OK
            assert kill(b,14).status!=OK
            with patch("private_ground_loot.random",Random(0)):
                response = kill(a,15)
            assert response.status==OK,response
            gift = response.drop_item
            assert gift.index and len(gift.items)==2,gift
            assert all(x.pack_type==pb.ItemDetail.PackType_TempStorageArea for x in gift.items)
            assert kill(a,15)==response
            assert not kill(a,16).HasField("drop_item")
            assert encounter(a,17).status==OK and not kill(a,18).HasField("drop_item")
            assert list(travel(a,19).drop_items)==[gift]
            assert pick(b,20,gift.index).status!=OK
            assert not travel(a,21,channel=2).drop_items
            assert pick(a,22,gift.index).status!=OK
            assert not travel(a,23,scene=2).drop_items
            assert list(travel(a,24).drop_items)==[gift]
            for seq,index in ((25,0),(26,3),(27,-2)):
                assert pick(a,seq,gift.index,index).status!=OK
            with host.lock,db.conn:
                assert not slots(pid)
                maximum = capacity()
                replace_slots(pid,{slot:make_item(1101104,1,pid) for slot in range(1,maximum+1)})
            blocked = pick(a,28,gift.index)
            assert blocked.status==pb.StatusCode_Temp_Pack_Is_Full and not blocked.items
            assert list(travel(a,29).drop_items)==[gift]
            with host.lock,db.conn:
                assert len(slots(pid))==maximum
                replace_slots(pid,{})
            first = pick(a,30,gift.index,1)
            assert first.status==OK and len(first.items)==1
            assert pick(a,30,gift.index,1)==first
            assert list(kill(a,15).drop_item.items)==[gift.items[1]]
            assert len(travel(a,31).drop_items[0].items)==1
            with host.lock:
                assert len(slots(pid))==1
        host.stop()
        host = LocalGameService(identity,root,resources,port=0)
        host.start()
        a,_,scene = connect(0)
        with a:
            assert len(scene.drop_items)==1 and scene.drop_items[0].index==gift.index
            assert list(scene.drop_items[0].items)==[gift.items[1]]
            last = pick(a,32,gift.index)
            assert last.status==OK and len(last.items)==1
            assert pick(a,33,gift.index).status!=OK
            assert not travel(a,34).drop_items
            stored = call(a,"TempPackItemStore",35)
            assert stored.status==OK and len(stored.items)==2,stored
            assert not call(a,"TempPackItemStore",36).items
            assert encounter(a,37).status==OK
            assert not kill(a,38).HasField("drop_item")
            assert not encounter(a,39,pb.Finish).HasField("dynamic_treasure_box_base_info")
            assert kill(a,40).status!=OK
            with patch("private_field_activities.now",return_value=now()+timedelta(minutes=11)):
                assert encounter(a,41).status==OK
                with patch("private_ground_loot.random",Random(1)):
                    next_gift = kill(a,42).drop_item
                assert next_gift.index>gift.index
        print("PASS: 1001/1205 kill, 2 generated and transferred, full-bag rollback, "
              "cross-player/channel/scene isolation, replay, restart and distinct unmarked encounter reward policy.")
    finally:
        host.stop()


if __name__=="__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),p.parse_args().resources.resolve())
