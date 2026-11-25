"""Native private-realm tests for GM wish beacons and nearby player action learning."""
import argparse
from contextlib import ExitStack
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
import private_actions_study as study
import private_moon_spots as beacons
from realm_gm import operator
from realm_rules import configure
from test_game_realm import join_after_intro, login, next_msg
from test_native_events import NOTICES, rpc
from utils import db


def run(state, resources):
    identity = LocalIdentity(state/"identity.sqlite3")
    admin, host_b, student, stranger = [identity.create_profile("对照"+str(i)) for i in range(4)]
    seq = count(200)
    for name in ("PlaceMoonSpotNotice","SendActionStudyNotice","ActionStudyNotice","AchieveActionUnLockNotice","SendActionNotice"):
        NOTICES[getattr(MsgId,name)] = getattr(pb,name)
    def ask(sock, name, **fields):
        return rpc(sock,name,next(seq),**fields)
    def notices(frames,kind):
        return [f for f in frames if isinstance(f,kind)]
    def poll(sock,kind):
        return notices(ask(sock,"PlayerPing",client_time_ms=5)[1],kind)
    def join(stack,profile):
        sock,_,uid = login(service.port,identity.issue_ticket(*profile))
        stack.enter_context(sock)
        _, data = join_after_intro(sock)
        return sock,uid,data.data
    def move(sock, channel):
        assert ask(sock,"ChangeSceneChannel",scene_id=9999,channel_label=channel)[0].status==pb.StatusCode_OK
        return next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice).data
    def expire():
        with service.lock,db.conn:
            beacons.expire(service.scene_members)
            service._publish_pending()
    ok,bad,fail = pb.StatusCode_OK,pb.StatusCode_BAD_REQ,pb.StatusCode_FAIL
    pose = pb.MoonSpotData(id=1,pos=pb.Vector3(x=100,y=123),rot=pb.Vector3(y=12))
    stamp = 1777000000000
    service = LocalGameService(identity,state,resources,port=0)
    service.start()
    try:
        with ExitStack() as stack, patch.object(beacons,"now_ms",return_value=stamp):
            a,aid,_ = join(stack,admin)
            b,bid,_ = join(stack,host_b)
            assert ask(a,"PlaceMoonSpot",operate=1,data=pose)[0].status==fail
            assert operator(state,"grant",uid=aid)["changed"]
            assert ask(a,"PlaceMoonSpot",operate=1,data=pb.MoonSpotData(id=999,pos=pose.pos,rot=pose.rot))[0].status==bad
            packet = next(seq)
            ans,frames=rpc(a,"PlaceMoonSpot",packet,operate=1,data=pose)
            m=notices(frames,pb.PlaceMoonSpotNotice)[0]
            assert ans.status==ok and m.player_id==aid and m.data.create_time==stamp and m.data.pos==pose.pos
            spot=m.data.instance_id
            assert poll(b,pb.PlaceMoonSpotNotice)==[m]
            assert rpc(a,"PlaceMoonSpot",packet,operate=1,data=pose)[0].status==ok and not poll(b,pb.PlaceMoonSpotNotice)
            assert operator(state,"grant",uid=bid)["changed"]
            moved=pb.MoonSpotData(instance_id=spot,pos=pb.Vector3(z=424),rot=pb.Vector3(y=86))
            assert ask(b,"PlaceMoonSpot",operate=3,data=moved)[0].status==bad
            assert not move(b,2).moon_spots
            assert ask(a,"PlaceMoonSpot",operate=3,data=moved)[0].status==ok
            assert not poll(b,pb.PlaceMoonSpotNotice)
            visible = move(b,1).moon_spots
            assert len(visible)==1 and visible[0].id==1 and visible[0].pos==moved.pos and visible[0].create_time==stamp
            assert ask(b,"PlaceMoonSpot",operate=2,data=pb.MoonSpotData(instance_id=spot))[0].status==bad
            assert operator(state,"revoke",uid=aid)["changed"]
            assert rpc(a,"PlaceMoonSpot",packet,operate=1,data=pose)[0].status==fail
            assert ask(a,"PlaceMoonSpot",operate=2,data=pb.MoonSpotData(instance_id=spot))[0].status==fail
            assert operator(state,"grant",uid=aid)["changed"]
            assert ask(a,"SendChatMsg",type=pb.ChatChannel_Default,text="/gm moonspot place")[0].status==ok
            command_spot=poll(b,pb.PlaceMoonSpotNotice)[0].data.instance_id
            assert command_spot>spot
            assert ask(a,"SendChatMsg",type=pb.ChatChannel_Default,text=f"/gm moonspot remove {command_spot}")[0].status==ok
            assert poll(b,pb.PlaceMoonSpotNotice)[0].operate==pb.OperateType_Remove
    finally:
        service.stop()
    # New accounts on this test realm only use original manual ownership. Earlier permanent awards must survive.
    configure(state,{"starter_teachable_actions":0})
    service = LocalGameService(identity,state,resources,port=0)
    service.start()
    try:
        with ExitStack() as stack, patch.object(beacons,"now_ms",return_value=stamp), patch.object(study,"now_ms",return_value=stamp):
            a,aid,scene = join(stack,admin)
            assert len(scene.moon_spots)==1 and scene.moon_spots[0].pos==moved.pos
            b,bid,_ = join(stack,host_b)
            c,cid,_ = join(stack,student)
            d,did,_ = join(stack,stranger)
            move(d,2)
            with patch.object(beacons,"now_ms",return_value=stamp+1209600000):
                expire()
                for peer in (a,b,c):
                    removed=poll(peer,pb.PlaceMoonSpotNotice)
                    assert len(removed)==1 and removed[0].data.instance_id==spot and removed[0].operate==pb.OperateType_Remove
                assert not poll(d,pb.PlaceMoonSpotNotice)
                assert not move(d,1).moon_spots
                move(d,2)
            cid_clip=10043
            def learn(client,teacher):
                return ask(client,"ActionStudy",action_id=cid_clip,player_id=teacher)
            assert learn(c,aid)[0].status==pb.StatusCode_ACTION_STUDY_TIME_OUT
            assert ask(c,"SendAction",action_id=cid_clip)[0].status==pb.StatusCode_ACTION_NOT_UNLOCK
            assert ask(a,"SendAction",action_id=cid_clip)[0].status==ok
            offer=poll(c,pb.SendActionStudyNotice)
            assert len(offer)==1 and offer[0].from_player_id==aid and offer[0].action_id==cid_clip and offer[0].end_time>stamp
            assert not poll(d,pb.SendActionStudyNotice)
            assert learn(b,aid)[0].status==pb.StatusCode_HasUnlocked
            assert learn(d,aid)[0].status==pb.StatusCode_ACTION_STUDY_TIME_OUT
            assert learn(c,bid)[0].status==pb.StatusCode_ACTION_STUDY_TIME_OUT
            with patch.object(study,"now_ms",return_value=offer[0].end_time):
                assert learn(c,aid)[0].status==pb.StatusCode_ACTION_STUDY_TIME_OUT
            with service.lock:
                from server.scene_data import online_session
                teacher_pos=online_session(aid).scene_player.team.char1.pos
                x=teacher_pos.x
                teacher_pos.x=x+601
            try:
                assert learn(c,aid)[0].status==pb.StatusCode_ACTION_STUDY_TIME_OUT
            finally:
                with service.lock: teacher_pos.x=x
            chosen=next(seq)
            ans,frames=rpc(c,"ActionStudy",chosen,action_id=cid_clip,player_id=aid)
            assert ans.status==ok
            expiration=notices(frames,pb.AchieveActionUnLockNotice)[0].study_action[cid_clip]
            assert expiration==stamp+study.LEARNED_MS
            assert [m.player_id for m in notices(frames,pb.ActionStudyNotice)]==[cid]
            assert [m.player_id for m in poll(b,pb.ActionStudyNotice)]==[cid] and not poll(d,pb.ActionStudyNotice)
            assert rpc(c,"ActionStudy",chosen,action_id=cid_clip,player_id=aid)[0].status==ok
            assert learn(c,aid)[0].status==pb.StatusCode_HasUnlocked and not poll(b,pb.ActionStudyNotice)
            assert ask(c,"SendAction",action_id=cid_clip)[0].status==ok
            assert any(m.is_study and m.from_player_id==cid for m in poll(a,pb.SendActionNotice))
            assert not poll(b,pb.SendActionStudyNotice)
            assert learn(b,cid)[0].status==pb.StatusCode_ACTION_STUDY_TIME_OUT
    finally:
        service.stop()
    service=LocalGameService(identity,state,resources,port=0)
    service.start()
    try:
        with ExitStack() as stack, patch.object(study,"now_ms",return_value=stamp+1):
            from test_game_realm import join as read_main_scene
            c,_,_=login(service.port,identity.issue_ticket(*student))
            stack.enter_context(c)
            read_main_scene(c)
            restored=next_msg(c,MsgId.AchieveActionUnLockNotice,pb.AchieveActionUnLockNotice)
            assert restored.study_action[10043]==expiration and 10700 not in restored.action_id_lst
            assert ask(c,"SendAction",action_id=10043)[0].status==ok
            with patch.object(study,"now_ms",return_value=expiration):
                assert ask(c,"SendAction",action_id=10043)[0].status==pb.StatusCode_ACTION_NOT_UNLOCK
    finally:
        service.stop()
    print("PASS authenticated GM beacon placement/move/removal/revocation, scene snapshots, restart and 14-day expiry; permanent and temporary native action ownership, near/far, teaching, replay, channel separation, reconnect and expiry.")


if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--resources",type=Path,required=True)
    args=ap.parse_args()
    with TemporaryDirectory(prefix="of-ref-") as tmp:
        run(Path(tmp),args.resources.resolve())
