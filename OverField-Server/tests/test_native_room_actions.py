"""Match plaza social actions across original client messages on a three-player realm."""
import argparse
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode,receive
from local_identity import LocalIdentity
from private_friends import set_state
from private_shop import balance
from test_game_realm import join_after_intro,login,next_msg
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def drain(sock):
    sock.sendall(encode(MsgId.PlayerPingReq,pb.PlayerPingReq().SerializeToString(),400))
    result=[]
    while True:
        header,data=receive(sock)
        if header.msg_id==MsgId.PlayerPingRsp and header.packet_id==400:
            return result
        for key in ("ClapResultNotice","ThrowDiceNotice","ThrowDiceResultNotice","SendMultipleActionCompleteNotice","SendActionNotice"):
            if header.msg_id==getattr(MsgId,key):
                result.append(getattr(pb,key).FromString(data))


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    host=LocalGameService(identity,root,resources,port=0)
    host.start()
    def clap(sock,packet,x,forward,cancel=False):
        return call(sock,"Clap",packet,movement_id=1061,is_cancel=cancel,
                    position=pb.Vector3(x=x),forward=pb.Vector3(z=forward))
    def roll(sock,packet,die,points,x=0):
        return call(sock,"ThrowDice",packet,dice_id=die,pips=points,position=pb.Vector3(x=x))
    def swap(sock,channel):
        assert call(sock,"ChangeSceneChannel",channel+70,scene_id=9999,channel_label=channel).status==pb.StatusCode_OK
        next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice)
    def accept(sock,src,action,packet):
        return call(sock,"MultipleActionAccept",packet,action_id=action,from_player_id=src,status=pb.StatusCode_OK)
    try:
        with ExitStack() as stack:
            players=[]
            for i in range(3):
                sock,_,uid=login(host.port,identity.issue_ticket(*identity.create_profile("互动"+str(i))))
                stack.enter_context(sock)
                join_after_intro(sock)
                players.append((sock,uid))
            (a,aa),(b,bb),(c,cc)=players
            swap(c,2)
            assert clap(a,11,0,100).status==pb.StatusCode_OK and not drain(a)
            assert clap(b,12,201,-100).status==pb.StatusCode_OK and not drain(a)
            assert clap(b,13,100,100).status==pb.StatusCode_OK and not drain(a)
            assert clap(a,14,0,100,True).status==pb.StatusCode_OK
            assert clap(b,15,100,-100).status==pb.StatusCode_OK and not drain(a)
            assert clap(a,16,0,100).status==pb.StatusCode_OK
            assert [list(n.matched_player_id_lst) for n in drain(a)]==[[bb,aa]]
            assert len(drain(b))==1 and not drain(c)
            assert clap(a,16,0,100).status==pb.StatusCode_OK and not drain(b)
            with host.lock,db.conn:
                for uid in (aa,bb):
                    db.set_item_detail(uid,make_item(2020,3,uid).SerializeToString(),2020)
            assert roll(a,24,31,7).status==pb.StatusCode_BAD_REQ
            assert roll(a,25,31,3).status==pb.StatusCode_OK
            first=drain(a)
            assert len(first)==2 and first[0].dice_id==31
            assert first[1].zone_result_lst[0].result_lst[0].player_id==aa
            assert len(drain(b))==2 and not drain(c)
            assert roll(a,25,31,3).status==pb.StatusCode_OK and not drain(b)
            assert balance(aa,2020).main_item.base_item.num==2
            assert roll(b,26,32,6,1000).status==pb.StatusCode_OK
            result=drain(a)[1].zone_result_lst[0].result_lst
            assert [(n.player_id,n.dice_id,n.won_result) for n in result]==[(aa,31,pb.ThrowDiceResult.Loss),(bb,32,pb.ThrowDiceResult.Win)]
            assert roll(a,27,33,6,1).status==pb.StatusCode_OK
            assert all(r.won_result==pb.ThrowDiceResult.Draw for r in drain(a)[1].zone_result_lst[0].result_lst)
            assert roll(a,28,34,1,2001).status==pb.StatusCode_OK
            assert [r.player_id for r in drain(a)[1].zone_result_lst[0].result_lst]==[aa]
            assert roll(a,29,35,3).status==pb.StatusCode_ITEM_NOT_ENOUGH
            drain(b)
            assert not drain(c)
            assert accept(b,aa,10000,40).status==pb.StatusCode_MULTIPLE_ACTION_INVALID
            assert call(a,"SendAction",41,action_id=10000).status==pb.StatusCode_OK
            invite=drain(b)[0]
            assert invite.multiple_need_count==2 and invite.end_time>0
            assert accept(c,aa,10000,42).status==pb.StatusCode_MULTIPLE_ACTION_INVALID
            assert accept(b,aa,10000,43).status==pb.StatusCode_FRIEND_INTIMACY_NOT_ENOUGH
            with host.lock,db.conn:
                for owner,peer in ((aa,bb),(bb,aa)):
                    set_state(owner,peer,pb.FriendStatus_FRIEND)
                    db.set_friend_info(owner,peer,"friend_intimacy",100)
            assert accept(b,aa,10000,44).status==pb.StatusCode_OK
            assert list(drain(a)[-1].action_player_ids)==[aa,bb]
            assert accept(b,aa,10000,45).status==pb.StatusCode_MULTIPLE_ACTION_INVALID
            call(a,"SendAction",46,action_id=10010)
            drain(b)
            with patch("private_room_actions.time.time",return_value=invite.end_time+120):
                assert accept(b,aa,10010,47).status==pb.StatusCode_MULTIPLE_ACTION_INVALID
            swap(a,2)
            swap(a,1)
            assert accept(b,aa,10010,48).status==pb.StatusCode_MULTIPLE_ACTION_INVALID
            assert not drain(c)
    finally:
        host.stop()
    print("PASS native facing/proximity high-five, scoped dice with materials and ties, timed two-player invitations, retries and channel-change cleanup.")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as t:
        run(Path(t),p.parse_args().resources)
