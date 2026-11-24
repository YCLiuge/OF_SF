"""Three native TCP players finish both national PC team courses with a fourth player on another channel."""
import argparse
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode,receive
from local_identity import LocalIdentity
from private_arcade_wallet import wallet
import private_mp_rooms as rooms
from test_game_realm import join_after_intro,login
from utils import db
from utils.pb_create import make_item


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    host=LocalGameService(identity,root,resources,port=0)
    profiles=[identity.create_profile("联机"+str(i)) for i in range(4)]
    host.start()
    seq=100
    tracked=("MPPlayRoomEventNotice","MPRoomEnterNotice","MPPlayRoomSettlementNotice","SceneMPBeaconNotice",
             "MPBeaconCanStartNotice","MPRoomAssignHostNotice","MPRoomDismissNotice","MPPlayRoomExitNotice",
             "SceneDataNotice","PlayerMPGameNotice")
    classes={getattr(MsgId,n):getattr(pb,n) for n in tracked}
    collected={}
    def call(s,name,**kwargs):
        nonlocal seq
        seq+=1
        s.sendall(encode(getattr(MsgId,name+"Req"),getattr(pb,name+"Req")(**kwargs).SerializeToString(),seq))
        while True:
            head,data=receive(s)
            if head.msg_id==getattr(MsgId,name+"Rsp") and head.packet_id==seq:
                return getattr(pb,name+"Rsp").FromString(data)
            if head.msg_id in classes:collected[s].append(classes[head.msg_id].FromString(data))
    def history(s,key):
        call(s,"PlayerPing")
        return [msg for msg in collected[s] if msg.DESCRIPTOR.name==key]
    def ok(s,name,**kwargs):
        result=call(s,name,**kwargs)
        assert result.status==pb.StatusCode_OK,(name,pb.StatusCode.Name(result.status))
        return result
    def open_course(a,b,c,outsider,cuids):
        team=rooms_team
        captain=next(p for p in host.active.values() if p.player_id==cuids[0])
        pos=captain.scene_player.team.char1.pos
        assert call(b,"MPBeaconAction",action=1,position=pos).status==pb.StatusCode_MP_TEAM_NOT_LEADER
        place=ok(a,"MPBeaconAction",action=1,position=pos)
        assert set(place.beacon.members)==set(cuids) and not place.beacon.can_start
        assert call(a,"MPBeaconAction",action=1,position=pos).status==pb.StatusCode_MP_BEACON_DUPLICATED
        assert not history(outsider,"SceneMPBeaconNotice")
        assert call(a,"MPGameStart").status==pb.StatusCode_MP_TEAM_NOT_READY
        ok(a,"MPBeaconAction",action=4)
        for sock in (b,c):ok(sock,"MPBeaconPlayerReady",team_id=team,is_ready=True)
        assert history(a,"MPBeaconCanStartNotice")[-1].can_start
        coins=wallet(cuids[0],117)
        room=ok(a,"MPGameStart")
        assert wallet(cuids[0],117)==coins-1 and room.team_id==team
        assert call(a,"MPGameStart").status==pb.StatusCode_MP_TEAM_GAME_STARTED and wallet(cuids[0],117)==coins-1
        assert call(b,"MPTeamQuit").status==pb.StatusCode_MP_TEAM_GAME_STARTED
        assert call(outsider,"MPPlayRoomReady",ready=True).status==pb.StatusCode_PLAYER_NOT_IN_MP_ROOM
        for sock in (a,b,c):ok(sock,"MPPlayRoomReady",ready=True)
        return team,history(b,"MPRoomEnterNotice")[-1],rooms.round_for(team)["start"]
    def connect(stack,i):
        s,_,uid=login(host.port,identity.issue_ticket(*profiles[i]))
        stack.enter_context(s)
        join_after_intro(s)
        collected[s]=[]
        return s,uid
    try:
        with ExitStack() as stack:
            (a,aa),(b,bb),(c,cc),(o,oo)=[connect(stack,i) for i in range(4)]
            ok(o,"ChangeSceneChannel",scene_id=9999,channel_label=2)
            r=ok(a,"MPTeamCreate",team_name="广场游戏",mode_id=1001,sub_mode_id=100101,character_id=101001)
            rooms_team=r.team_detail.base.team_id
            captain=next(p for p in host.active.values() if p.player_id==aa)
            pos=captain.scene_player.team.char1.pos
            ok(a,"MPBeaconAction",action=1,position=pos)
            assert call(o,"MPTeamJoin",team_id=rooms_team,character_id=101001).status==pb.StatusCode_MP_TEAM_CANNOT_JOIN
            assert call(a,"MPGameStart").status==pb.StatusCode_MP_TEAM_MEMBER_NOT_ENOUGH
            for sock in (b,c):ok(sock,"MPTeamJoin",team_id=rooms_team,character_id=101001)
            ok(a,"MPBeaconAction",action=3)
            assert call(b,"MPSwapCharacter",character_id=999).status==pb.StatusCode_MP_CHARACTER_INVALID
            assert call(b,"MPSwapCharacter",character_id=101001).status==pb.StatusCode_MP_CHARACTER_UNCHANGED
            with host.lock,db.conn:
                for uid in (aa,bb,cc):
                    db.set_item_detail(uid,make_item(117,6,uid).SerializeToString(),117)
            # The largest native course has more objects than one connection\'s 128-frame outbox.
            with patch("private_mp_rooms.random.choice",return_value=10010106):
                team,enter,started=open_course(a,b,c,o,(aa,bb,cc))
            count=rooms.round_for(team)["entities"]
            assert count==135
            assert list(enter.room_info.destroy_object.remain_object_list)==list(range(1,count+1))
            assert 10010101<=enter.game_mode_info.gameplay_id<=10010110
            assert [e.event_type for n in history(a,"MPPlayRoomEventNotice") for e in n.events][-2:]==[1,2]
            def smash(index):return pb.MPPlayRoomEvent(event_type=4,id=index)
            before={uid:wallet(uid,121) for uid in (aa,bb,cc,oo)}
            with patch("private_mp_rooms.millis",return_value=started+20):
                assert call(a,"MPPlayRoomEvent",events=[smash(1),smash(count+1)]).status==pb.StatusCode_BAD_REQ
                assert not rooms.round_for(team)["objects"]
                ok(b,"MPPlayRoomEvent",events=[smash(1)])
                ok(b,"MPPlayRoomEvent",events=[smash(1)])
                ok(c,"MPPlayRoomExit")
                ok(a,"MPPlayRoomEvent",events=[smash(i) for i in range(2,count+1)])
                settle=history(b,"MPPlayRoomSettlementNotice")[-1]
                for client in (a,b):
                    batches=[list(msg.events) for msg in history(client,"MPPlayRoomEventNotice")
                             if msg.events and msg.events[0].event_type==4]
                    assert [len(batch) for batch in batches]==[1,count-1]
                    assert [(ev.id,ev.count,ev.player_id) for ev in batches[-1]]==[(i,i,aa) for i in range(2,count+1)]
                assert settle.rank.star==3 and {q.player_id:q.count for q in settle.rank.destroy_object}=={aa:count-1,bb:1,cc:0}
                assert settle.rewards[0].main_item.base_item.num==1500 and wallet(aa,121)==before[aa]+1500
                assert wallet(bb,121)==before[bb]+1500 and wallet(cc,121)==before[cc] and wallet(oo,121)==before[oo]
                assert call(a,"MPPlayRoomEvent",events=[smash(1)]).status==pb.StatusCode_MP_NOT_STARTING
                assert ok(a,"MPPlayRoomRewardInfo").score_week_remain==5000000-1500
            for sock in (a,b):ok(sock,"MPPlayRoomExit")
            assert not rooms.in_game(team)
            ok(a,"MPTeamGameModeChange",mode_id=1002,sub_mode_id=100201)
            team,enter,started=open_course(a,b,c,o,(aa,bb,cc))
            assert 10020101<=enter.game_mode_info.gameplay_id<=10020110
            assert list(enter.room_info.magic_platform.show_platform_list)==[1]
            count=rooms.round_for(team)["entities"]
            with patch("private_mp_rooms.millis",return_value=started+100):
                for sock in (a,b,c):
                    ok(sock,"MPPlayRoomEvent",events=[pb.MPPlayRoomEvent(event_type=7,id=i) for i in range(1,count+1)])
                ranks=history(b,"MPPlayRoomSettlementNotice")[-1].rank
                assert ranks.star==3 and {r.player_id for r in ranks.magic_platform}=={aa,bb,cc}
            for sock in (a,b,c):ok(sock,"MPPlayRoomExit")
            ok(a,"ChangeSceneChannel",scene_id=9999,channel_label=2)
            assert rooms.beacon_for(team) is None
            assert ok(b,"MPTeamInfo").team_detail.base.team_id==team
    finally:
        host.stop()
    print("PASS two national PC team courses, broadcast isolation, stateful readiness, once-only host charge, object IDs, standings, forfeits, score caps and rewards.")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as t:
        run(Path(t),p.parse_args().resources)
