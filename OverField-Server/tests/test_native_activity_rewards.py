"""Native current/expired sign-in windows, login-day gates and saved achievement claims."""
import argparse
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService,MsgId,pb
from local_game_wire import encode,receive
from local_identity import LocalIdentity
from private_dungeons import _table
from private_shop import CN,balance
from test_game_realm import login
from test_observed_initialization import call
from utils import db


def run(state,resources):
    identity=LocalIdentity(state/"identity.sqlite3")
    slots=[identity.create_profile(s) for s in ("签到甲","签到乙")]
    game=LocalGameService(identity,state,resources,port=0)
    clock=[datetime(2025,4,3,3,59,tzinfo=CN)]
    def connect(index):
        sock,_,pid=login(game.port,identity.issue_ticket(*slots[index]))
        sock.sendall(encode(MsgId.PlayerMainDataReq,pb.PlayerMainDataReq().SerializeToString(),3))
        for _ in range(130):
            h,body=receive(sock)
            if h.msg_id==MsgId.ActivitySignInDataNotice:
                return sock,pid,{r.activity_id:r for r in pb.ActivitySignInDataNotice.FromString(body).info}
        raise AssertionError("Missing sign-in data")
    def count(pid,item):
        with game.lock:return balance(pid,item).main_item.base_item.num
    def complete(pid,key):
        with game.lock,db.conn:
            goal=_table("Achieve","achieve")[key]["count_param"]
            db.set_achieve(pid,key,pb.Achieve(achieve_id=key,count=goal).SerializeToString())
    with patch("private_shop.now",side_effect=lambda:clock[0]):
        game.start()
        try:
            a,pid,status=connect(0)
            b,bid,_=connect(1)
            with a,b:
                assert status[2001].login_day==1 and status[2002].open_time>0
                assert 5014 not in status
                start=count(pid,104)
                assert call(a,"ActivitySignIn",activity_id=2001,day=2).status!=pb.StatusCode_OK
                first=call(a,"ActivitySignIn",activity_id=2001,day=1)
                assert first.status==pb.StatusCode_OK and first.reward_id==2 and count(pid,104)==start+2
                assert call(a,"ActivitySignIn",activity_id=2001,day=1).status==pb.StatusCode_REPEAT_GET_REWARD
                assert count(bid,104)==start
                clock[0]=clock[0].replace(hour=4,minute=0)
                second=call(a,"ActivitySignIn",activity_id=2001,day=2)
                assert second.status==pb.StatusCode_OK and second.reward_id==6 and count(pid,104)==start+5
                assert call(a,"ActivitySignIn",activity_id=2001,day=3).status!=pb.StatusCode_OK
                bonus=call(a,"ActivitySignIn",activity_id=2003,day=1)
                assert bonus.status==pb.StatusCode_OK and {r.main_item.item_id for r in bonus.rewards}=={102,104,9004,482,6020170,4200242,4200243,492}
                group=60010000;key=60010011
                assert call(a,"GetAchieveGroupAward",group_id=group).status!=pb.StatusCode_OK
                assert call(a,"GetOneAchieveAward",achieve_id=key).status!=pb.StatusCode_OK
                complete(pid,key)
                stars=count(pid,102)
                personal=call(a,"GetOneAchieveAward",achieve_id=key)
                assert personal.status==pb.StatusCode_OK and count(pid,102)==stars+5
                assert call(a,"GetOneAchieveAward",achieve_id=key).status==pb.StatusCode_REPEAT_GET_REWARD
                assert call(a,"GetAchieveGroupAward",group_id=group).status!=pb.StatusCode_OK
                assert call(b,"GetOneAchieveAward",achieve_id=key).status!=pb.StatusCode_OK
                for row in _table("AchieveQuest","achieve_quest_group")[group]["achieve_quest_group_info"]:
                    complete(pid,row["achieve_condition_i_d"])
                coupons=count(pid,104)
                assert call(a,"GetAchieveGroupAward",group_id=group).status==pb.StatusCode_OK and count(pid,104)==coupons+1
                assert call(a,"GetAchieveGroupAward",group_id=group).status==pb.StatusCode_REPEAT_GET_REWARD
        finally:game.stop()
        game.start()
        try:
            clock[0]=datetime(2025,5,1,10,0,tzinfo=CN)
            a,_,status=connect(0)
            b,_,other=connect(1)
            with a,b:
                assert status[2001].login_day==3 and status[2001].reward_id==6
                assert other[2001].login_day==2 and other[2001].reward_id==0
                assert 2002 not in status and call(a,"ActivitySignIn",activity_id=2002,day=1).status==pb.StatusCode_Activity_Close
                assert list(call(a,"GetAchieveGroupList").rewarded_group_id_lst)==[group]
                view=call(a,"GetAchieveOneGroup",group_id=group)
                assert view.is_reward and list(view.current_group_achieve_info.rewarded_achieve_id_lst)==[key]
                assert not call(b,"GetAchieveGroupList").rewarded_group_id_lst
        finally:game.stop()
    print("PASS native 04:00 UTC+8 sign-in rollover and rewards, active windows, achievement completion gates, per-role balances, claim deduplication and relog.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:run(Path(tmp),parser.parse_args().resources)
