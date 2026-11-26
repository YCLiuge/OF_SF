"""Three private roles exercise the PC social UI's search, request, moderation and rift-sharing messages."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService,MsgId,pb
from local_identity import LocalIdentity
from test_game_realm import join,login,next_msg
from test_observed_initialization import call


def run(root,resources):
    ident=LocalIdentity(root/"identity.sqlite3")
    people=[ident.create_profile(n) for n in ("队长一","好友二","第三名")]
    host=LocalGameService(ident,root,resources,port=0)

    def connect(person):
        sock,_,pid=login(host.port,ident.issue_ticket(*people[person]))
        join(sock)
        return sock,pid

    def roster(sock,kind):
        return [f.info.player_id for f in call(sock,"Friend",type=kind).info]

    def notice(sock,other,kind):
        event=next_msg(sock,MsgId.FriendHandleNotice,pb.FriendHandleNotice)
        assert event.status==pb.StatusCode_OK and event.target_player_id==other and event.type==kind,event

    host.start()
    try:
        a,owner=connect(0)
        b,friend=connect(1)
        c,other=connect(2)
        with a,b,c:
            from utils import db
            fill = list(range(8000000,8000200))
            with host.lock,db.conn:
                db.conn.executemany("INSERT INTO players(player_id) VALUES(?)", [(key,) for key in fill])
                db.conn.executemany("INSERT INTO friend(player_id,friend_id,friend_status) VALUES(?,?,?)",
                                    [(owner,key,pb.FriendStatus_FRIEND) for key in fill])
            assert call(a,"FriendAdd",player_id=friend).status==pb.StatusCode_FRIEND_LIMIT
            with host.lock,db.conn:
                db.conn.execute("UPDATE friend SET friend_status=? WHERE player_id=?", (pb.FriendStatus_APPLY,owner))
            assert call(b,"FriendAdd",player_id=owner).status==pb.StatusCode_FRIEND_APPLY_LIMIT
            with host.lock,db.conn:
                db.conn.executemany("DELETE FROM players WHERE player_id=?", [(key,) for key in fill])
            found=call(a,"FriendSearch",search_args="好友二")
            assert found.status==pb.StatusCode_OK and found.data.player_id==friend and found.data.is_online
            assert found.friend_status==pb.FriendStatus_NONE
            assert call(a,"FriendSearch",search_args=str(friend)).data.nick_name=="好友二"
            for key in ("","没有这个角色","4294967296","9"*600):
                assert call(a,"FriendSearch",search_args=key).status==pb.StatusCode_FRIEND_SEARCH_FAIL
            assert call(a,"FriendAdd",player_id=owner).status==pb.StatusCode_FRIEND_ADD_FAIL
            assert call(a,"FriendAdd",player_id=0xffffffff).status==pb.StatusCode_FRIEND_ADD_FAIL
            assert call(a,"FriendAdd",player_id=friend).status==pb.StatusCode_OK
            notice(b,owner,pb.FriendHandleType_APPLY)
            assert roster(a,pb.FriendListType_NONE)==[]
            assert roster(b,pb.FriendListType_APPLY)==[owner]
            assert call(a,"FriendHandle",player_id=friend,is_agree=True).status==pb.StatusCode_FRIEND_NOT_APPLY
            assert call(c,"FriendHandle",player_id=owner,is_agree=True).status==pb.StatusCode_FRIEND_NOT_APPLY
            assert call(b,"FriendAdd",player_id=owner).status==pb.StatusCode_FRIEND_APPLY_EXIST
            assert call(a,"FriendAdd",player_id=friend).status==pb.StatusCode_FRIEND_APPLY_EXIST
            assert call(b,"FriendHandle",player_id=owner,is_agree=False).status==pb.StatusCode_OK
            assert not roster(a,0) and not roster(b,0)
            assert call(a,"FriendAdd",player_id=friend).status==pb.StatusCode_OK
        host.stop()
        host.start()
        a,again=connect(0)
        b,_,=connect(1)
        c,_,=connect(2)
        with a,b,c:
            assert again==owner and roster(b,pb.FriendListType_APPLY)==[owner]
            assert call(b,"FriendHandle",player_id=owner,is_agree=True).status==pb.StatusCode_OK
            notice(a,friend,pb.FriendHandleType_ADD)
            assert roster(a,pb.FriendListType_FRIEND)==[friend] and roster(b,pb.FriendListType_FRIEND)==[owner]
            assert not roster(c,0)
            assert call(a,"FriendAdd",player_id=friend).status==pb.StatusCode_FRIEND_EXIST
            assert call(a,"FriendSearch",search_args="好友二").friend_status==pb.FriendStatus_FRIEND
            assert call(a,"SelfIntervalInit").interval_id==101 and call(a,"IntervalStart").status==pb.StatusCode_OK
            shared=call(b,"FriendIntervalInit").friend_infos[0]
            assert shared.player_id==owner and shared.create_time>0
            assert call(b,"IntervalJoin",friend_id=owner,interval_param=str(shared.create_time)).status==pb.StatusCode_OK
            assert call(c,"FriendDel",player_id=owner).status==pb.StatusCode_FRIEND_NOT_EXIST
            assert call(a,"FriendDel",player_id=friend).status==pb.StatusCode_OK
            notice(b,owner,pb.FriendHandleType_DEL)
            assert not roster(a,0) and not roster(b,0)
            assert not call(b,"FriendIntervalInit").friend_infos
            assert call(b,"FriendIntervalInit").join_infos[0].player_id==owner
            assert call(b,"FriendBlack",player_id=owner).status==pb.StatusCode_OK
            assert roster(b,pb.FriendListType_BLACK)==[owner]
            assert call(a,"FriendAdd",player_id=friend).status==pb.StatusCode_FRIEND_BLACK
            assert call(a,"FriendBlack",player_id=friend,is_remove=True).status==pb.StatusCode_OK
            assert call(a,"FriendAdd",player_id=friend).status==pb.StatusCode_FRIEND_BLACK
            assert call(b,"FriendBlack",player_id=owner,is_remove=True).status==pb.StatusCode_OK
            assert call(a,"FriendAdd",player_id=friend).status==pb.StatusCode_OK
            assert call(b,"FriendHandle",player_id=owner,is_agree=True).status==pb.StatusCode_OK
        host.stop()
        host.start()
        a,_=connect(0)
        with a:
            found=call(a,"FriendSearch",search_args="好友二")
            assert found.friend_status==pb.FriendStatus_FRIEND and not found.data.is_online
            assert roster(a,pb.FriendListType_FRIEND)==[friend]
    finally:
        host.stop()
    print("PASS native friend search by nickname/UID, notices, add/decline/accept, third-party isolation, delete/block, restart and shared rift join.")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as path:
        run(Path(path),ap.parse_args().resources)
