"""Exercise native password accounts, existing-role binding and one-use game tickets."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_identity import LocalIdentity, InvalidTicket
from local_game_service import LocalGameService, pb
from native_account_seats import NativeAccountSeats
from realm_accounts import AccountError
from test_private_profiles import serve,request
from test_game_realm import login,join
from test_observed_initialization import call


def check_login_rename(root):
    from realm_accounts import initialize,register,login as password_login,rename_login,reserve_native,username_spelling
    from realm_gm import resolve
    authority=LocalIdentity(root/"mixed-logins.sqlite3")
    initialize(authority)
    old={"username":"owner.old","password":"local-fixture-password"}
    wanted="云海OwO"
    created=register(authority,dict(old,nickname=wanted),False)
    slot=created["slot"]
    present=reserve_native(authority,{"slot":slot,"ticket":created["ticket"]})
    other=register(authority,dict(old,username="隔壁UP",nickname="隔壁玩家"),False)
    def snapshot(db):
        data=db.execute("SELECT slot,salt,verifier,n,r,p FROM realm_accounts WHERE slot=?",(slot,)).fetchone()
        return (data,db.execute("SELECT * FROM profiles WHERE slot=?",(slot,)).fetchone(),
                db.execute("SELECT * FROM realm_native_resume WHERE slot=?",(slot,)).fetchone(),
                db.execute("SELECT * FROM tickets WHERE slot=? ORDER BY ticket_hash",(slot,)).fetchall())
    with closing(sqlite3.connect(authority.path)) as db:
        before=snapshot(db)
        assert rename_login(authority,slot,old["username"],wanted)
        assert not rename_login(authority,slot,old["username"],wanted)
        assert snapshot(db)==before and before[2] and present["resume"]
        assert db.execute("SELECT username FROM realm_accounts WHERE slot=?",(slot,)).fetchone()==(wanted,)
        assert resolve(None,db,"云海ＯｗＯ",None)==slot
        for invalid in ("短","白 月OwO","云海\u202eOwO","云海OwO\n","a"*41):
            try:username_spelling(invalid)
            except AccountError as error:assert error.code=="invalid_username"
            else:raise AssertionError("Accepted unsupported account name")
        for source,target,error_code in [(wanted,"隔壁uP","username_taken"),("错账号","另一个","account_changed")]:
            try:rename_login(authority,slot,source,target)
            except AccountError as error:assert error.code==error_code
            else:raise AssertionError("Unexpected successful rename")
        assert snapshot(db)==before
        assert db.execute("SELECT username FROM realm_accounts WHERE slot=?",(other["slot"],)).fetchone()==("隔壁UP",)
    assert password_login(authority,dict(old,username="云海ｏＷｏ"))["slot"]==slot
    try:password_login(authority,old)
    except AccountError as error:assert error.code=="invalid_credentials"
    else:raise AssertionError("Retired login name accepted")
    assert rename_login(authority,slot,wanted,old["username"])
    assert password_login(authority,old)["nickname"]==wanted


def run(root,resources):
    check_login_rename(root)
    identity=LocalIdentity(root/"identity.sqlite3")
    legacy=identity.create_profile("此前角色")
    game=LocalGameService(identity,root,resources,port=0)
    register={"username":"First.user","password":"real-test-password","nickname":"满月"}
    other=dict(register,username="second.user",nickname="星星")
    game.start()
    try:
        with serve(identity) as port:
            post=lambda op,data:request(port,"POST","/local/v1/accounts/"+op,data)
            c,_,oldpid=login(game.port,identity.issue_ticket(*legacy))
            with c:join(c)
            code,auth=post("register",register)
            assert code==201 and "profile_key" not in auth
            assert post("register",dict(other,nickname="满月"))==(409,{"error":"nickname_taken"})
            assert post("register",dict(other,username="FIRST.USER"))==(409,{"error":"username_taken"})
            assert post("login",dict(username="First.user",password="wrong-password"))==(401,{"error":"invalid_credentials"})
            assert post("register",dict(other,password="too"))[0]==400
            code,more=post("register",other)
            assert code==201 and more["slot"]!=auth["slot"]
            c,_,pid=login(game.port,auth["ticket"])
            with c:
                main,_=join(c)
                assert main.player_name==register["nickname"]
                assert call(c,"ChangeNickName",nick_name=other["nickname"]).status==pb.StatusCode_DUPLICATED_NAME
                assert call(c,"ChangeNickName",nick_name="新月",birthday="2002-05-06").nick_name=="新月"
            assert login(game.port,auth["ticket"]) is None
            bind=dict(username="preserve.1",password="recovery-pass",nickname="迁移队长",slot=legacy[0],profile_key=legacy[1])
            assert post("bind",dict(bind,profile_key="not-valid"))[0]==401
            code,bound=post("bind",bind)
            assert code==201 and bound["slot"]==legacy[0]
            c,_,restorepid=login(game.port,bound["ticket"])
            with c:
                main,_=join(c)
                assert restorepid==oldpid and main.player_name=="迁移队长"
            assert post("bind",bind)==(409,{"error":"profile_already_bound"})
            autolabel={"username":"native.slot","password":"private-slot-password"}
            code,pending=post("register",autolabel)
            assert code==201 and pending["nickname"]==""
            from local_game_wire import encode,receive
            from network.msg_id import MsgId
            import socket
            with socket.create_connection(("127.0.0.1",game.port),timeout=5) as fresh:
                def rpc(msg_id,body,expected):
                    fresh.sendall(encode(msg_id,body.SerializeToString(),99))
                    for _ in range(100):
                        h,b=receive(fresh)
                        if h.packet_id==99 and h.msg_id==getattr(MsgId,expected):
                            return getattr(pb,expected).FromString(b)
                    raise AssertionError(expected)
                v=rpc(MsgId.VerifyLoginTokenReq,pb.VerifyLoginTokenReq(login_token=pending["ticket"]),"VerifyLoginTokenRsp")
                assert v.status==pb.StatusCode_OK
                assert rpc(MsgId.PlayerLoginReq,pb.PlayerLoginReq(),"PlayerLoginRsp").player_name==""
                assert rpc(MsgId.PlayerMainDataReq,pb.PlayerMainDataReq(),"PlayerMainDataRsp").status==pb.StatusCode_BAD_REQ
                assert rpc(MsgId.ChangeNickNameReq,pb.ChangeNickNameReq(nick_name="新月"),"ChangeNickNameRsp").status==pb.StatusCode_DUPLICATED_NAME
                assert rpc(MsgId.ChangeNickNameReq,pb.ChangeNickNameReq(nick_name="萌芽",birthday="2001-4-2"),"ChangeNickNameRsp").status==pb.StatusCode_OK
                assert rpc(MsgId.PlayerMainDataReq,pb.PlayerMainDataReq(),"PlayerMainDataRsp").player_name=="萌芽"
            assert post("login",autolabel)[1]["nickname"]=="萌芽"
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda u:post("register",dict(register,username=u,nickname="同时注册")),("race.one","race.two")))
            assert sorted(n for n,_ in results)==[201,409]
        game.stop()
        game.start()
        with serve(identity,allow_registration=False) as port:
            assert post("register",dict(register,username="closed.name"))==(403,{"error":"registration_closed"})
            code,auth=post("login",{"username":register["username"].upper(),"password":register["password"]})
            assert code==200 and auth["nickname"]=="新月"
            c,_,newpid=login(game.port,auth["ticket"])
            with c:
                main,_=join(c)
                assert newpid==pid and main.player_name=="新月"
            assert post("attach",{"pid":15,"ticket":"not-a-ticket"})[0]==403
            seats=NativeAccountSeats(identity,Path("launcher.exe"))
            code,proof=post("login",{"username":register["username"],"password":register["password"]})
            assert code==200
            def info(pid):return (Path("launcher.exe"),10,20) if pid==11 else (Path("parent.exe"),3,4)
            with patch("native_account_seats.process_info",side_effect=info),patch("native_account_seats.connection_pid",return_value=10):
                seats.attach(None,{"pid":11,"ticket":proof["ticket"]})
                try:seats.claim(None)
                except ValueError:pass
                else:raise AssertionError("wrong process accepted")
            with patch("native_account_seats.process_info",side_effect=info),patch("native_account_seats.connection_pid",return_value=11):
                # Same interactive PID finishes a long first update after the password ticket has expired.
                with patch.object(identity,"_clock",return_value=identity._clock()+38*60):
                    assert seats.claim(None)==auth["slot"]
                try:seats.claim(None)
                except ValueError:pass
                else:raise AssertionError("handoff replay accepted")
        import sys,subprocess
        if sys.platform=="win32":
            from private_profiles import create_profile_server
            from threading import Thread
            import socket
            with create_profile_server(identity,port=0) as http:
                http.native_accounts=NativeAccountSeats(identity,Path(sys.executable).resolve())
                thread=Thread(target=http.serve_forever,kwargs={"poll_interval":0.02},daemon=True)
                thread.start()
                try:
                    with socket.socket() as probe:
                        probe.bind(("127.0.0.1",0));probe.listen();probe.settimeout(5)
                        script="import sys,socket;sys.stdin.readline();s=socket.create_connection(('127.0.0.1',int(sys.argv[1])));s.recv(1)"
                        with subprocess.Popen([sys.executable,"-c",script,str(probe.getsockname()[1])],stdin=subprocess.PIPE) as proc:
                            try:
                                port=http.server_port
                                code,standalone=post("login",{"username":register["username"],"password":register["password"]})
                                assert code==200
                                assert post("attach",{"pid":proc.pid,"ticket":standalone["ticket"]})==(200,{"ok":True})
                                proc.stdin.write(b"go\n");proc.stdin.flush()
                                conn,_=probe.accept()
                                with conn:
                                    assert http.native_accounts.claim(conn)==standalone["slot"]
                                    conn.sendall(b"1")
                            finally:
                                if proc.poll() is None:
                                    proc.terminate()
                finally:
                    http.shutdown();thread.join()
        storage=identity.path.read_bytes()
        assert register["password"].encode() not in storage
        with closing(sqlite3.connect(identity.path)) as db:
            salts=[v for v, in db.execute("SELECT salt FROM realm_accounts")]
            assert len(salts)==len(set(salts)) and all(len(s)==16 for s in salts)
    finally:game.stop()
    print("PASS independent account register/login, gated bind to old UID, unique normalized names including concurrent signup, native name change, one-use game tickets, restart and process-specific handoff.")
if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)

