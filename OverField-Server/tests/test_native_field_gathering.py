"""National 1751/1955 gathering, daily allowances, actual loot, personal tools and relogin."""
import argparse
from datetime import datetime
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService,MsgId,pb
from local_identity import LocalIdentity
from private_dungeons import _table
from private_field_gathering import points
from private_inventory import capacity,replace_slots,slots
from private_shop import CN
from test_game_realm import login,join_after_intro,next_msg
from test_native_events import NOTICES,rpc
from test_native_gacha_atomicity import balance
from utils import db
from utils.pb_create import make_item


def run(root,resources):
    profiles=LocalIdentity(root/"identity.sqlite3")
    roles=[profiles.create_profile(n) for n in ("采集甲","采集乙")]
    realm=LocalGameService(profiles,root,resources,port=0)
    date=[datetime(2026,11,24,4,58,tzinfo=CN)]
    seq=count(300)
    NOTICES.update({MsgId.GatherSceneLimitRecoveryNotice:pb.GatherSceneLimitRecoveryNotice,
                    MsgId.LifeAchieveNotice:pb.LifeAchieveNotice})
    def call(sock,name,**kwargs):return rpc(sock,name,next(seq),**kwargs)[0]
    def open_role(role):
        sock,_,pid=login(realm.port,profiles.issue_ticket(*role))
        info,scene=join_after_intro(sock)
        lead=next(a for a in info.characters if a.character_id==info.team.char1)
        return sock,pid,lead,scene.data
    def equip(sock,lead,slot):
        return call(sock,"CharacterGatherWeaponUpdate",character_id=lead.character_id,
                    weapon_id=getattr(lead.character_appearance,slot))
    def pick(world,group):
        index=next(i for i,g in points()[world].items() if g==group)
        row=_table("Gather","gather")[group]["gather_group_info"][0]
        return {"index":index,"reward":row["reward"]}
    def gather(sock,point,packet=None):
        return rpc(sock,"Gather",packet if packet is not None else next(seq),gather_item=point)[0]
    def counts(sock):
        result=call(sock,"GetOneTypeLifePictorialBookCount",life_type=pb.LIFE_TYPE_Gathering)
        return {item.id:item.count for item in result.life_achieve_count_lst}
    with patch("private_shop.now",side_effect=lambda:date[0]):
        realm.start()
        try:
            a,pid,actor,_=open_role(roles[0])
            b,other,other_actor,_=open_role(roles[1])
            city=("Scenes",9999)
            flower,butterfly,wood=[pick(city,g) for g in (100003,400001,200003)]
            assert sum(len(v) for v in points().values())==11886
            with a,b:
                assert gather(a,flower).status==pb.StatusCode_GATHER_WEAPON_NOT_WEAR
                assert equip(a,actor,"insect_net_instance_id").status==pb.StatusCode_OK
                assert gather(a,flower).status==pb.StatusCode_GATHER_WEAPON_TYPE_DIFFER
                assert gather(a,{**butterfly,"index":flower["index"]}).status==pb.StatusCode_BAD_REQ
                assert gather(a,{**butterfly,"index":0xffffffff}).status==pb.StatusCode_BAD_REQ
                assert gather(a,{**butterfly,"index":next(i for i in points()[("Scenes",1)] if i not in points()[city])}).status==pb.StatusCode_BAD_REQ
                key=next(seq)
                result=gather(a,{**butterfly,"is_lucky":True},key)
                assert result.status==pb.StatusCode_OK and result.index==butterfly["index"]
                item=result.items[0].main_item
                assert item.base_item.num==3 and result.group_gather_limit.gather_group_id==0
                c=result.scene_gather_limit.gather_limits[0]
                assert (c.gather_type,c.gather_num,c.gather_limit_num,c.lucky_gather_limit_num)==(3,1,1,1)
                assert result.group_gather_limit.gather_limit==c
                assert gather(a,{**butterfly,"is_lucky":True},key)==result
                assert counts(a)[item.item_id]==3 and not counts(b)
                goal=_table("LifeGather","life_gather_item")[item.item_id]["life_gather_achieve_rewards"][0]["achieve_condition_i_d"]
                assert call(a,"GetLifeAchievementReward",life_type=6,achieve_id=goal).status==pb.StatusCode_OK
                assert call(a,"GetLifeAchievementReward",life_type=6,achieve_id=goal).status==pb.StatusCode_REPEAT_GET_REWARD
                assert call(b,"GetLifeAchievementReward",life_type=6,achieve_id=goal).status!=pb.StatusCode_OK
                assert equip(a,actor,"collection_gloves_instance_id").status==pb.StatusCode_OK
                assert gather(a,flower).status==pb.StatusCode_OK
                assert equip(a,actor,"logging_axe_instance_id").status==pb.StatusCode_OK
                for lucky,quantity in ((False,1),(True,3)):
                    got=gather(a,{**wood,"is_lucky":lucky})
                    assert got.status==pb.StatusCode_OK and len(got.items)==2
                    assert [i.main_item.base_item.num for i in got.items]==[quantity,quantity]
                assert equip(b,other_actor,"insect_net_instance_id").status==pb.StatusCode_OK
                assert gather(b,butterfly).scene_gather_limit.gather_limits[0].gather_num==1
                with realm.lock,db.conn:
                    db.conn.execute("UPDATE field_gather_counts SET used=100 WHERE player=? AND scope=0 AND place=9999 AND kind=3",(pid,))
                    old=balance(pid,item.item_id)
                assert equip(a,actor,"insect_net_instance_id").status==pb.StatusCode_OK
                assert gather(a,butterfly).status==pb.StatusCode_GATHER_LIMIT
                with realm.lock:assert balance(pid,item.item_id)==old
                date[0]=datetime(2026,11,24,5,0,tzinfo=CN)
                frames=rpc(a,"PlayerPing",next(seq))[1]+rpc(a,"PlayerPing",next(seq))[1]
                notice=next(m for m in frames if isinstance(m,pb.GatherSceneLimitRecoveryNotice))
                c=next(v for v in notice.scene_gather_limits[0].gather_limits if v.gather_type==3)
                assert c.gather_num==1 and not c.gather_limit_num and not c.lucky_gather_limit_num
                c=gather(a,butterfly).scene_gather_limit.gather_limits[0]
                assert (c.gather_num,c.gather_limit_num,c.lucky_gather_limit_num)==(2,1,0)
                did=9710301
                enter=call(a,"DungeonEnter",dungeon_id=did,char1=actor.character_id)
                assert enter.status==pb.StatusCode_OK
                assert gather(a,butterfly).status==pb.StatusCode_BAD_REQ
                dungeon=("Dungeons",did)
                group=next(v for v in points()[dungeon].values() if _table("Gather","gather")[v]["gather_group_info"][0].get("new_gather_type")==1)
                point=pick(dungeon,group)
                assert equip(a,actor,"logging_axe_instance_id").status==pb.StatusCode_OK
                with realm.lock,db.conn:
                    filler=make_item(20200002,99999,pid)
                    replace_slots(pid,{i:filler for i in range(1,capacity()+1)})
                assert gather(a,point).status==pb.StatusCode_Temp_Pack_Is_Full
                with realm.lock,db.conn:
                    assert not db.conn.execute("SELECT 1 FROM field_gather_counts WHERE player=? AND scope=0 AND place=10301",(pid,)).fetchone()
                    replace_slots(pid,{})
                got=gather(a,point)
                assert got.status==pb.StatusCode_OK and got.scene_gather_limit.scene_id==10301 and got.scene_gather_limit.gather_limits[0].gather_num==1
                with realm.lock:assert slots(pid)
                assert call(a,"DungeonExit").status==pb.StatusCode_OK
            realm.stop()
            realm.start()
            a,_,again,scene=open_role(roles[0])
            with a:
                saved={c.gather_type:c for c in scene.gather_limits}
                assert (saved[3].gather_num,saved[3].gather_limit_num)==(2,1)
                assert saved[1].gather_num==2 and saved[0].gather_num==1
                assert actor.character_appearance==again.character_appearance
                assert counts(a)[item.item_id]==4
        finally:realm.stop()
    print("PASS 11886 authored gather points, owned tools, ordinary/lucky/double drops, independent roles, native replays, atlas reward, daily cutoff, dungeon bag rollback and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    args=parser.parse_args()
    with TemporaryDirectory() as target:run(Path(target),args.resources)
