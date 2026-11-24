"""Exercise native invitation and recall codes across configured editions and real TCP sessions."""
import argparse
from contextlib import ExitStack
from datetime import datetime,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService,MsgId,pb
from local_identity import LocalIdentity
from private_shop import CN
from test_game_realm import login
from test_native_events import rpc,NOTICES
from realm_events import configure
from utils import db


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    profiles=[identity.create_profile(n) for n in ("引路人","归来者","新队长")]
    host=LocalGameService(identity,root,resources,port=0)
    host.start()
    def enter(index):
        sock,_,uid=login(host.port,identity.issue_ticket(*profiles[index]))
        _,first=rpc(sock,"PlayerMainData",3)
        _,rest=rpc(sock,"PlayerPing",4)
        rows={e.base.activity_id:e for m in first+rest if m.DESCRIPTOR.name in ("ActivityRecallNotice","ActivityInviteNotice") for e in m.info}
        return sock,uid,rows
    def claim(sock,mode,aid,goal,packet):
        return rpc(sock,"Activity"+mode+"RewardClaim",packet,activity_id=aid,condition_id=goal)[0]
    def use(sock,mode,code,packet):
        return rpc(sock,mode+"CodeUse",packet,code=code)[0]
    def earned(rsp):
        return {i.main_item.item_id:i.main_item.base_item.num for i in rsp.rewards}
    registered={getattr(MsgId,k):getattr(pb,k) for k in ("ActivityInviteNotice","ActivityRecallNotice","ActivityInviteCountUpdateNotice")}
    try:
        with patch.dict(NOTICES,registered),patch("private_shop.now",return_value=datetime(2025,5,2,12,tzinfo=CN)) as clock:
            with ExitStack() as stack:
                a,first,events=enter(0);stack.enter_context(a)
                assert set(events)=={6001}
                code=events[6001].invite_code
                b,other,_=enter(1);stack.enter_context(b)
                with host.lock,db.conn:
                    db.set_players_info(other,"level",1)
                assert claim(a,"Invite",6001,80601001,10).status==pb.StatusCode_ACTIVITY_INVITE_TASK_NOT_COMPLETED
                assert use(a,"Invite",code,11).status==pb.StatusCode_INVITE_CODE_NOT_EXIST
                assert use(b,"Invite","1234567890",11).status==pb.StatusCode_INVITE_CODE_NOT_EXIST
                reply=use(b,"Invite",code,12)
                assert reply.status==pb.StatusCode_OK and earned(reply)=={104:1,102:30,101:5000}
                assert use(b,"Invite",code,12)==reply
                assert use(b,"Invite",code,13).status==pb.StatusCode_INVITE_CODE_ALREADY_FILLED
                award=claim(a,"Invite",6001,80601001,14)
                assert award.status==pb.StatusCode_OK and earned(award)=={101:1000}
                assert claim(a,"Invite",6001,80601001,15).status==pb.StatusCode_ACTIVITY_INVITE_TASK_CLAIMED
                assert claim(a,"Invite",6001,80601002,16).status==pb.StatusCode_ACTIVITY_INVITE_TASK_NOT_COMPLETED
                assert claim(a,"Invite",6001,80601101,17).status==pb.StatusCode_ACTIVITY_INVITE_TASK_NOT_COMPLETED
                with host.lock,db.conn:
                    db.set_players_info(other,"level",3)
                assert earned(claim(a,"Invite",6001,80601101,18))=={102:10}
                assert claim(a,"Invite",6001,80601201,19).status==pb.StatusCode_ACTIVITY_INVITE_TASK_NOT_COMPLETED
                from private_gifts import claim as virtual_claim
                with host.lock,db.conn:
                    assert virtual_claim(other,201,1,"virtual-first-order")[0]==pb.StatusCode_OK
                assert earned(claim(a,"Invite",6001,80601201,20))=={118:1}
                configure(root,{"events":[{"id":6001,"enabled":False,"opens_at":None,"closes_at":None}]})
                assert claim(a,"Invite",6001,80601001,21).status==pb.StatusCode_ACTIVITY_INVITE_NOT_OPEN
                clock.return_value=datetime(2026,1,15,12,tzinfo=CN)
                rpc(a,"PlayerPing",22)
                _,notice=rpc(a,"PlayerPing",23)
                second=next(e for m in notice if m.DESCRIPTOR.name=="ActivityInviteNotice" for e in m.info if e.base.activity_id==6002)
                assert second.invite_code!=code and not second.rewarded_tasks and second.invited_total==1
            host.stop()
            clock.return_value=datetime(2026,3,30,12,tzinfo=CN)
            host.start()
            with ExitStack() as stack:
                a,_,events=enter(0);stack.enter_context(a)
                promoter,ret=260326160,260326170
                assert promoter in events and ret in events
                code=events[promoter].invite_code
                b,_,events=enter(1);stack.enter_context(b)
                assert ret in events
                c,_,events=enter(2);stack.enter_context(c)
                assert ret not in events
                assert use(c,"Recall",code,25).status==pb.StatusCode_ACTIVITY_INVITE_NOT_OPEN
                award=use(b,"Recall",code,26)
                assert award.status==pb.StatusCode_OK and earned(award)=={102:500,125:50}
                assert use(b,"Recall",code,27).status==pb.StatusCode_INVITE_CODE_ALREADY_FILLED
                assert earned(claim(a,"Recall",promoter,80509001,28))=={5173:1}
                assert claim(a,"Recall",promoter,80509004,29).status==pb.StatusCode_ACTIVITY_INVITE_TASK_NOT_COMPLETED
                assert claim(b,"Recall",ret,80509011,30).status==pb.StatusCode_ACTIVITY_INVITE_TASK_NOT_COMPLETED
                assert rpc(b,"ChangeSceneChannel",35,scene_id=9999,channel_label=1)[0].status==pb.StatusCode_OK
                assert rpc(b,"ActivitySandbagHit",36,activity_id=260326150,npc_id=10301,hit_count=10)[0].status==pb.StatusCode_OK
                assert earned(claim(b,"Recall",ret,80509011,37))=={109:50000}
                assert earned(claim(b,"Recall",ret,80509014,38))=={1502:10}
            host.stop()
            host.start()
            for i in range(1,7):
                clock.return_value+=timedelta(days=1)
                b,_,_=enter(1)
                b.close()
            a,_,events=enter(0)
            with a:
                assert events[promoter].invite_code==code
                assert earned(claim(a,"Recall",promoter,80509004,31))=={118:3}
                assert claim(a,"Recall",promoter,80509004,32).status==pb.StatusCode_ACTIVITY_INVITE_TASK_CLAIMED
    finally:
        host.stop()
    print("PASS two authored invitation editions, stable realm-issued codes, verified referrals, level and virtual receipt goals, 15-day return rules, offline restart, distinct days and atomic grants.")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--resources",required=True,type=Path)
    args=p.parse_args()
    with TemporaryDirectory() as tmp:
        run(Path(tmp),args.resources)
