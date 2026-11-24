"""Exercise national shop, native map instance, field locations, shovel, dig and retired saves via TCP."""
import argparse
from itertools import count
from pathlib import Path
import random
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService,MsgId,pb
from local_game_wire import encode
from local_identity import LocalIdentity
from private_gameplay_transaction import Rejected
from private_treasure_dig import field_points
from test_game_realm import login,join_after_intro,next_msg
from test_native_events import NOTICES,rpc
from test_native_gacha_atomicity import set_balance
from utils import db
from utils.pb_create import make_item


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    first=identity.create_profile("考古实测")
    second=identity.create_profile("考古隔离")
    host=LocalGameService(identity,root,resources,port=0)
    packets=count(400)
    NOTICES[MsgId.PackNotice]=pb.PackNotice

    def enter(profile):
        sock,_,pid=login(host.port,identity.issue_ticket(*profile))
        join_after_intro(sock)
        return sock,pid

    def ask(sock,name,**values):
        return rpc(sock,name,next(packets),**values)

    def buy(sock,grid,qty=1):
        rsp,_=ask(sock,"ShopBuy",shop_id=15000,grid_id=grid,buy_times=qty)
        assert rsp.status==pb.StatusCode_OK,rsp
        return rsp.items

    def travel(sock,sid):
        rsp,_=ask(sock,"ChangeSceneChannel",scene_id=sid)
        assert rsp.status==pb.StatusCode_OK,rsp
        assert next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice).data.scene_id==sid

    def move(sock,x,z):
        req=pb.PlayerSceneRecordReq()
        pose=req.data.char_recorder_data_lst.add(char_id=101001).pos
        pose.x,pose.z=round(x*100),round(z*100)
        sock.sendall(encode(MsgId.PlayerSceneRecordReq,req.SerializeToString(),next(packets)))
        next_msg(sock,MsgId.PlayerSceneSyncDataNotice,pb.PlayerSceneSyncDataNotice)

    def equip(sock,tool):
        result,_=ask(sock,"UpdateCharacterAppearance",char_id=101001,appearance=pb.CharacterAppearance(shovel_instance_id=tool))
        assert result.status==pb.StatusCode_OK,result

    def approach(sock,t):
        x,z,size=field_points()[t.scene_id][1][t.gather_point_index,t.life_gather_point_index]
        move(sock,x+size/2,z+size/2)

    host.start()
    try:
        sock,pid=enter(first)
        peer,other=enter(second)
        with sock,peer:
            with host.lock,db.conn:
                set_balance(pid,101,3000)
                set_balance(pid,102,1000)
            shovel=buy(sock,35)[0].main_item.weapon.instance_id
            normal=list(buy(sock,36,2))
            n,bad=(row.main_item.treasure for row in normal)
            advanced=buy(sock,37)[0].main_item.treasure
            assert n.instance_id!=bad.instance_id and advanced.instance_id not in (bad.instance_id,n.instance_id)
            assert all((t.gather_point_index,t.life_gather_point_index) in field_points()[t.scene_id][1] for t in (n,bad,advanced))
            extra,_=ask(sock,"TreasureIllustrationReward",archaeology_type=pb.EArchaeologyType_TreasureMap,id=5070002)
            assert extra.status==pb.StatusCode_OK and any(r.main_item.treasure.item_id==5070001 for r in extra.rewards)
            assert ask(peer,"TreasureDig",treasure_id=n.instance_id)[0].status==pb.StatusCode_ITEM_NOT_ENOUGH
            assert ask(sock,"TreasureDig",treasure_id=n.instance_id)[0].status==pb.StatusCode_BAD_REQ
            assert ask(sock,"TreasureDig",treasure_id=5070001)[0].status==pb.StatusCode_ITEM_NOT_ENOUGH
            travel(sock,n.scene_id)
            move(sock,3000,3000)
            assert ask(sock,"TreasureDig",treasure_id=n.instance_id)[0].status==pb.StatusCode_BAD_REQ
            approach(sock,n)
            assert ask(sock,"TreasureDig",treasure_id=n.instance_id)[0].status==pb.StatusCode_ITEM_NOT_ENOUGH
            equip(sock,shovel)
            original=random.randint
            fail=lambda low,high: 1000 if (low,high)==(1,1000) else original(low,high)
            with patch("private_treasure_dig.random.randint",side_effect=fail):
                tag=next(packets)
                result,frames=rpc(sock,"TreasureDig",tag,treasure_id=n.instance_id)
            assert result.status==pb.StatusCode_OK and not result.success and result.treasure_id==n.instance_id
            assert result.rewards[0].main_item.item_id==9000001 and result.rewards[0].main_item.base_item.num==1
            assert any(isinstance(msg,pb.PackNotice) for msg in frames)
            assert rpc(sock,"TreasureDig",tag,treasure_id=n.instance_id)[0]==result
            assert ask(sock,"TreasureDig",treasure_id=n.instance_id)[0].status==pb.StatusCode_ITEM_NOT_ENOUGH
            with host.lock,db.conn:
                top=make_item(1007003,1,pid)
                key=top.main_item.weapon.instance_id
                db.set_item_detail(pid,top.SerializeToString(),instance_id=key)
                old=pb.ItemDetail.FromString(db.get_item_detail(pid,instance_id=bad.instance_id))
                old.main_item.treasure.ClearField("life_gather_point_index")
                old.main_item.treasure.ClearField("gather_point_index")
                db.set_item_detail(pid,old.SerializeToString(),instance_id=bad.instance_id)
                saved=bytes(db.get_item_detail(pid,instance_id=advanced.instance_id))
            equip(sock,key)
            approach(sock,advanced)
            with patch("private_gameplay_transaction.GameplayWrite.grant",side_effect=Rejected(pb.StatusCode_CFG_NOT_FOUND)):
                result,_=ask(sock,"TreasureDig",treasure_id=advanced.instance_id)
                assert result.status==pb.StatusCode_CFG_NOT_FOUND and not result.rewards
        host.stop()
        host.start()
        sock,again=enter(first)
        with sock:
            assert pid==again
            with host.lock:
                migrated=pb.ItemDetail.FromString(db.get_item_detail(pid,instance_id=bad.instance_id)).main_item.treasure
                assert migrated.instance_id==bad.instance_id and (migrated.gather_point_index,migrated.life_gather_point_index) in field_points()[migrated.scene_id][1]
                assert bytes(db.get_item_detail(pid,instance_id=advanced.instance_id))==saved
                assert not db.get_item_detail(pid,instance_id=n.instance_id)
        host.stop()
        host.start()
        sock,_=enter(first)
        with sock:
            with host.lock:
                assert pb.ItemDetail.FromString(db.get_item_detail(pid,instance_id=bad.instance_id)).main_item.treasure==migrated
            travel(sock,advanced.scene_id)
            approach(sock,advanced)
            result,_=ask(sock,"TreasureDig",treasure_id=advanced.instance_id)
            assert result.status==pb.StatusCode_OK and result.success and result.treasure_id==advanced.instance_id and len(result.rewards)==1,result
            with host.lock:
                assert db.get_item_detail(pid,instance_id=key) and not db.get_item_detail(pid,instance_id=advanced.instance_id)
                assert db.get_item_detail(pid,result.rewards[0].main_item.item_id)
    finally:
        host.stop()
    print("PASS national shop maps and shovel, genuine point pairs, UUID-only dig, scene/area/ownership checks, success/failure, rollback, retries, migration and restart")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    args=parser.parse_args()
    with TemporaryDirectory() as tmp:
        run(Path(tmp),args.resources)
