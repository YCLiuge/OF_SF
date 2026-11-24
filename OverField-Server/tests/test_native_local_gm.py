"""End-to-end local operator grants, revocations, native and chat GM entry points."""
import argparse
from contextlib import ExitStack
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService,MsgId,pb
from local_identity import LocalIdentity
import realm_accounts
from realm_gm import operator
from test_game_realm import join,login
from test_native_events import NOTICES,rpc
from utils import db


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    service=LocalGameService(identity,root,resources,port=0)
    seq=count(40)
    NOTICES.update({getattr(MsgId,n):getattr(pb,n) for n in ("PackNotice","ChatMsgNotice","GmNotice","ReceiveMailNotice")})
    def ask(sock,name,**fields):
        return rpc(sock,name,next(seq),**fields)
    def filtered(frames,kind):
        return [m for m in frames if isinstance(m,kind)]
    def gm(sock,cmd):
        return ask(sock,"GmCode",param=cmd)
    def observe(sock):
        return ask(sock,"PlayerPing",client_time_ms=771)[1]
    def amount(uid):
        with service.lock:
            return pb.ItemDetail.FromString(db.get_item_detail(uid,101)).main_item.base_item.num
    service.start()
    try:
        a_auth=realm_accounts.register(identity,{"username":"Alice.GM","password":"demo-N4-show","nickname":"服管测试"},False)
        b_auth=realm_accounts.register(identity,{"username":"Bob.User","password":"demo-N8-show","nickname":"普通测试"},False)
        assert operator(root,"grant",account="ALICE.gm")["uid"] is None
        with ExitStack() as stack:
            a,_,aid=login(service.port,a_auth["ticket"])
            b,_,bid=login(service.port,b_auth["ticket"])
            for sock in (a,b):
                stack.enter_context(sock)
                join(sock)
            assert operator(root,"list")=={"administrators":[{"account":"alice.gm","uid":aid}]}
            assert not operator(root,"grant",uid=aid)["changed"]
            assert gm(a,"whoami")[0].result.endswith(str(aid))
            assert gm(a,"online")[0].result=="本服在线 2 人"
            assert gm(b,"give 101 9")[0].status==pb.StatusCode_FAIL
            assert gm(b,"announce unauthorized text")[0].status==pb.StatusCode_FAIL
            assert gm(a,"grant 1000002")[0].status==pb.StatusCode_BAD_REQ
            assert gm(a,"give 101 1001")[0].status==pb.StatusCode_BAD_REQ
            assert gm(a,"mail 999999 unknown")[0].status==pb.StatusCode_BAD_REQ
            assert gm(a,"announce token ofk_123456789012345678901234567890")[0].status==pb.StatusCode_BAD_REQ
            before=amount(aid)
            packet=next(seq)
            reply,_=rpc(a,"GmCode",packet,param="give 101 17")
            assert reply.status==pb.StatusCode_OK and reply.reward_infos[0].num==17 and amount(aid)==before+17
            assert rpc(a,"GmCode",packet,param="give 101 17")[0]==reply and amount(aid)==before+17
            for kind in (pb.ChatChannel_Default,pb.ChatChannel_ChatRoom,pb.ChatChannel_Private):
                rsp,_=ask(a,"SendChatMsg",type=kind,player_id=bid,text=" /GM whoami ")
                assert rsp.status==pb.StatusCode_OK and rsp.text.endswith(str(aid))
                denied,_=ask(b,"SendChatMsg",type=kind,player_id=aid,text="/gm announce private")
                assert denied.status==pb.StatusCode_FAIL
                assert not filtered(observe(b),pb.ChatMsgNotice)
                assert not filtered(observe(a),pb.ChatMsgNotice)
            announce=next(seq)
            reply,frames=rpc(a,"SendChatMsg",announce,type=pb.ChatChannel_ChatRoom,text="/gm announce 今晚八点举行活动")
            assert reply.status==pb.StatusCode_OK
            assert [list(m.notice) for m in filtered(frames,pb.GmNotice)]==[["今晚八点举行活动"]]
            peer=observe(b)
            assert [list(m.notice) for m in filtered(peer,pb.GmNotice)]==[["今晚八点举行活动"]]
            assert not filtered(peer,pb.ChatMsgNotice)
            assert rpc(a,"SendChatMsg",announce,type=pb.ChatChannel_ChatRoom,text="/gm announce 今晚八点举行活动")[0]==reply
            assert not filtered(observe(b),pb.GmNotice)
            mail_msg=f"mail {bid} 活动通知"
            mail=next(seq)
            assert rpc(a,"GmCode",mail,param=mail_msg)[0].status==pb.StatusCode_OK
            assert rpc(a,"GmCode",mail,param=mail_msg)[0].status==pb.StatusCode_OK
            assert [m.mail.content for m in filtered(observe(b),pb.ReceiveMailNotice)]==["活动通知"]
            assert [m.content for m in ask(b,"GetMails")[0].mails if m.sender=="区服管理"]==["活动通知"]
            with service.lock:
                assert not db.conn.execute("SELECT 1 FROM private_chat_messages").fetchone()
                assert not db.conn.execute("SELECT 1 FROM private_chat_contacts").fetchone()
            assert operator(root,"revoke",uid=aid)["changed"]
            assert rpc(a,"GmCode",packet,param="give 101 17")[0].status==pb.StatusCode_FAIL
            assert amount(aid)==before+17
            assert ask(a,"SendChatMsg",type=pb.ChatChannel_Default,text="/gm whoami")[0].status==pb.StatusCode_FAIL
            assert operator(root,"grant",uid=bid)["changed"] and gm(b,"whoami")[0].status==pb.StatusCode_OK
            history=operator(root,"audit",limit=100)
            commands=[r for r in history["records"] if r["source"] in ("chat","native")]
            assert sum(r["command"]=="mail" and r["outcome"]=="ok" for r in commands)==1
            assert sum(r["command"]=="announce" and r["outcome"]=="ok" for r in commands)==1
            assert history["denied"] and "ofk_" not in str(history) and "unauthorized text" not in str(history)
            b.close()
            # Grants and actor binding remain valid without an online moderator or recipient.
            operator(root,"grant",uid=aid)
            assert gm(a,f"mail {bid} 离线通知")[0].status==pb.StatusCode_OK
    finally:
        service.stop()
    service.start()
    try:
        user=realm_accounts.login(identity,{"username":"BOB.user","password":"demo-N8-show"})
        sock,_,uid=login(service.port,user["ticket"])
        with sock:
            join(sock)
            assert gm(sock,"whoami")[0].result.endswith(str(uid))
            inbox=ask(sock,"GetMails")[0]
            assert sorted(m.content for m in inbox.mails if m.sender=="区服管理")==sorted(["活动通知","离线通知"])
            assert operator(root,"revoke",account="bob.user")["changed"]
            assert gm(sock,"whoami")[0].status==pb.StatusCode_FAIL
    finally:
        service.stop()
    other=root/"other-realm"
    other.mkdir()
    other_identity=LocalIdentity(other/"identity.sqlite3")
    other_service=LocalGameService(other_identity,other,resources,port=0)
    other_service.start()
    try:
        credentials=realm_accounts.register(other_identity,{"username":"Alice.GM","password":"other-test-pass","nickname":"另一个服"},False)
        sock,_,uid=login(other_service.port,credentials["ticket"])
        with sock:
            join(sock)
            assert uid==aid and gm(sock,"whoami")[0].status==pb.StatusCode_FAIL
            assert operator(other,"list")=={"administrators":[]}
        assert operator(root,"list")["administrators"]==[{"account":"alice.gm","uid":aid}]
    finally:
        other_service.stop()
    print("PASS two local password accounts, scoped GM grants, native + all three private command paths, "
          "live revoke and replay protection, notifications, offline mail, audit and restart")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),p.parse_args().resources)
