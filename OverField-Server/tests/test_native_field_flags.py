"""Original field battle and scenic requests and their persistent per-role results."""
import argparse
from collections import Counter
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from private_dungeons import _table, reward_items
from test_game_realm import join_after_intro, login, next_msg
from test_native_gacha import call
from utils import db
from utils.pb_create import make_Quest


def run(root, resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    realm=LocalGameService(identity,root,resources,port=0)
    requests=count(500)
    roles=[identity.create_profile(name) for name in ("观景记录","独立地图")]
    realm.start()
    def connect(role):
        client,_,pid=login(realm.port,identity.issue_ticket(*role))
        join_after_intro(client)
        return client,pid
    def scene(sock,key):
        assert call(sock,"ChangeSceneChannel",next(requests),scene_id=key).status==pb.StatusCode_OK
        return next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice).data
    def flag(sock,kind,key,state,packet=None):
        return call(sock,"FlagBattleStateUpdate",next(requests) if packet is None else packet,
                    mission_type=kind,battle_id=key,battle_state=state)
    def stack(pid,item):
        raw=db.get_item_detail(pid,item)
        return pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0
    def stock(pid,ids):
        return Counter({key:stack(pid,key) for key in ids})
    def saved(pid,key):
        return pb.Quest.FromString(db.get_quest(pid,key))
    normal,scenic=pb.EMissionType_NormalBattle,pb.EMissionType_ScenicSpot
    try:
        published={}
        for row in _table("Flag","flag").values():
            for part in row["flag_group_info"]:
                kind=part.get("new_mission_type",normal)
                if (row.get("icon_i_d"),kind) in ((1,normal),(3,scenic)) and part["index"]==1 and part.get("is_item_unlock"):
                    published.setdefault(row["scene_i_d"],[]).append((kind,part["relation_i_d"]))
        assert Counter(k for entries in published.values() for k,_ in entries)=={normal:58,scenic:42}
        c,pid=connect(roles[0])
        with c:
            assert flag(c,normal,110210,pb.Start).status==pb.StatusCode_BAD_REQ
            assert flag(c,scenic,1002,pb.Start).status==pb.StatusCode_BAD_REQ
            for map_id,entries in sorted(published.items()):
                assert not scene(c,map_id).flags
                for kind,key in entries:
                    if kind==normal:
                        assert flag(c,kind,key,pb.Finish).status==pb.StatusCode_BAD_REQ
                        assert flag(c,kind,key,pb.Start).status==pb.StatusCode_OK
                        serial=next(requests)
                        settled=flag(c,kind,key,pb.Finish,serial)
                        assert settled.battle_data.finish_times==1
                        assert flag(c,kind,key,pb.Start).status==pb.StatusCode_OK
                        assert flag(c,kind,key,pb.Finish,serial)==settled
                        assert flag(c,kind,key,pb.Fail).battle_data.finish_times==1
                        continue
                    assert flag(c,kind,key,pb.Finish).status==pb.StatusCode_BAD_REQ
                    cfg=_table("Scenic","scenic")[key]
                    expected=reward_items(pid,cfg["first_reward_i_d"])
                    assert len(expected)>0
                    before=stock(pid,expected)
                    opened=flag(c,kind,key,pb.Start)
                    assert opened.status==pb.StatusCode_OK and opened.battle_data.finish_times==1
                    assert opened.battle_data.voice_id==cfg.get("guide_voice",0)
                    assert flag(c,kind,key,pb.Start)==opened
                    assert stock(pid,expected)-before==Counter(expected),(key,expected)
            scene(c,1)
            assert flag(c,normal,110210,pb.Start).status==pb.StatusCode_OK
            scene(c,9999)
            scene(c,1)
            assert flag(c,normal,110210,pb.Finish).status==pb.StatusCode_BAD_REQ
            assert flag(c,2,110210,pb.Start).status==pb.StatusCode_MODULE_CLOSE
            # A player can open the permanent waterwheel before accepting its story.
            # Seed only the preceding step, then complete three native valve interactions.
            with realm.lock,db.conn:
                db.set_quest(pid,100073,make_Quest(pid,100073).SerializeToString())
            from local_game_wire import encode
            for event,x,y,z in ((1000731,4540,702,-7383),(1000732,-107,549,7776),(1000733,-7066,709,7312)):
                record=pb.PlayerSceneRecordReq()
                point=record.data.char_recorder_data_lst.add(char_id=101001).pos
                point.x,point.y,point.z=x,y,z
                c.sendall(encode(MsgId.PlayerSceneRecordReq,record.SerializeToString(),next(requests)))
                next_msg(c,MsgId.PlayerSceneSyncDataNotice,pb.PlayerSceneSyncDataNotice)
                assert call(c,"NpcTalk",next(requests),id=event,talk_type=pb.NpcTalkReq.NpcTalkType_Mechanism).status==pb.StatusCode_OK
            assert saved(pid,100074).status==pb.QuestStatus_Finish and saved(pid,100075).status==pb.QuestStatus_InProgress
        realm.stop()
        realm.start()
        c,pid=connect(roles[0])
        with c:
            for map_id,entries in published.items():
                restored=scene(c,map_id).flags
                assert {(i.type,i.battle_id,i.finish_times) for i in restored}=={(k,v,1) for k,v in entries}
        c,other=connect(roles[1])
        with c:
            assert other!=pid and not scene(c,1).flags
            assert not any(stack(other,k) for k in range(10701005,10701009))
    finally:
        realm.stop()
    print("PASS all 58 published normal flags and 42 scenic spots, reward deltas, retry after a new match, prior completion, role isolation and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:
        run(Path(state),parser.parse_args().resources)
