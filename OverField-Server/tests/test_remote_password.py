"""A remote password account plays its own role over an authenticated private game transport."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing,contextmanager
from dataclasses import replace
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from local_game_service import LocalGameService,pb,MsgId
from local_identity import LocalIdentity
from local_game_wire import encode,receive
from remote_password import RemotePassword
from test_game_realm import join
from test_private_profiles import request
from test_remote_realm import free_ports,ObservedSocket


def resume_lifetime(database):
    import hashlib
    from realm_accounts import AccountError, RESUME_GRACE, RESUME_TTL, end_native_session, initialize, login, register, reserve_native
    now=[1000]
    identity=LocalIdentity(database,clock=lambda:now[0])
    initialize(identity)
    creds={"username":"resume-fixture","password":"password-for-synthetic-resume"}
    initial=register(identity,{**creds,"nickname":"断线测试"},False)
    slot=initial["slot"]
    def start(receipt):
        return reserve_native(identity,{"slot":slot,"ticket":receipt["ticket"]})
    def resume(session,target=slot):
        return reserve_native(identity,{"slot":target,"resume":session["resume"]})
    def lose(session):
        assert identity.resolve_ticket(session["ticket"])==slot
        end_native_session(identity,slot,hashlib.sha256(session["ticket"].encode()).digest(),logout=False)
    def denied(action):
        try:action()
        except AccountError as error:
            assert error.status==401
        else:raise AssertionError("Expected rejected private resume credential")
    first=start(initial)
    lose(first)
    denied(lambda:resume(first,"ofp_"+"f"*32))
    now[0]+=RESUME_GRACE
    denied(lambda:resume(first))
    a=start(login(identity,creds))
    assert identity.resolve_ticket(a["ticket"])==slot
    now[0]+=RESUME_TTL-1
    # This game stayed online beyond its original one-use ticket's lifetime.
    end_native_session(identity,slot,hashlib.sha256(a["ticket"].encode()).digest(),logout=False)
    b=resume(a)
    now[0]+=1
    lose(b)
    denied(lambda:resume(b))
    previous=start(login(identity,creds))
    lose(previous)
    new=start(login(identity,creds))
    denied(lambda:resume(previous))
    lose(new)
    with identity._write() as db:
        row=db.execute("SELECT digest FROM realm_native_resume WHERE slot=?",(slot,)).fetchone()
        assert row[0]==hashlib.sha256(new["resume"].encode()).digest()
        db.execute("UPDATE realm_accounts SET verifier=randomblob(32) WHERE slot=?",(slot,))
    denied(lambda:resume(new))

    # The front socket can reconnect just before the realm finishes saving the lost connection.
    from remote_password import RemoteLogin, SessionStillClosing
    proxy=RemotePassword(None,None,0,0)
    fixture=RemoteLogin(slot,"ticket",resume_token="resume")
    with patch.object(proxy,"_exchange",side_effect=[SessionStillClosing(),fixture]) as exchange, \
         patch("remote_password.time.sleep") as delay:
        assert proxy.resume(fixture) is fixture and exchange.call_count==2
        delay.assert_called_once_with(.2)


def run(root,resources):
    resume_lifetime(root/"resume-policy.sqlite3")
    server=Path(__file__).resolve().parents[1]
    hport,gport=free_ports()
    remote=root/"remote";remote.mkdir()
    front=root/"front";front.mkdir()
    env={k:v for k,v in os.environ.items() if not k.startswith("OF_")}
    env.update(OF_ACCOUNT_REGISTRATION="open",OF_STATE=str(remote),OF_HTTP_PORT=str(hport),OF_GAME_PORT=str(gport))
    cmd=[sys.executable,"-B",str(server/"of.py")]
    proc=subprocess.Popen(cmd+["start"],env=env,cwd=server,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    seen=[]
    class Channels:
        @contextmanager
        def open(self,port):
            assert port in (hport,gport)
            with socket.create_connection(("127.0.0.1",port),timeout=5) as sock:
                yield ObservedSocket(sock,seen)
    def submit(k,fields):
        return request(hport,"POST","/local/v1/accounts/"+k,fields)
    try:
        for n in range(80):
            try:
                if request(hport,"GET","/healthz")[0]==200:break
            except OSError:
                time.sleep(.1)
        local=LocalIdentity(front/"identity.sqlite3")
        proxy=RemotePassword(local,Channels(),hport,gport)
        game=LocalGameService(local,front,resources,port=0)
        class Seat:
            def claim(self,sock):
                assert self.grant is not None
                grant=self.grant;self.grant=None
                return grant
        class Verified:
            def verify_channel(self,uid,lic,peer):
                if (uid,lic,peer)!=("development-uid","development-proof","127.0.0.1"):raise ValueError("Unexpected credential in test")
        seats=Seat()
        game.remote_password=proxy
        game.online_identity=Verified()
        game.native_accounts=seats
        game.start()
        try:
            for index in (1,2):
                payload=dict(username="account"+str(index),password="test-private-password",nickname="远端"+str(index))
                status,proof=submit("register",payload)
                assert status==201
                assert submit("launch",{"slot":"bad","ticket":proof["ticket"]})[0]==401
                grant=proxy.reserve(proof["slot"],proof["ticket"])
                assert grant.resume_token
                assert submit("launch",{"slot":proof["slot"],"resume":grant.resume_token})[0]==409
                assert submit("launch",{"slot":proof["slot"],"ticket":proof["ticket"]})[0]==401
                assert submit("launch",{"slot":proof["slot"],"ticket":grant.ticket})[0]==401
                disconnected=Event()
                seats.grant=replace(grant,on_disconnect=disconnected.set)
                with socket.create_connection(("127.0.0.1",game.port),timeout=5) as c:
                    c.sendall(encode(MsgId.VerifyLoginTokenReq,pb.VerifyLoginTokenReq(sdk_uid="development-uid",login_token="development-proof").SerializeToString(),1))
                    h,b=receive(c)
                    assert pb.VerifyLoginTokenRsp.FromString(b).user_id==1000000+index
                    c.sendall(encode(MsgId.PlayerLoginReq,pb.PlayerLoginReq().SerializeToString(),2))
                    h,b=receive(c)
                    assert pb.PlayerLoginRsp.FromString(b).player_name==payload["nickname"]
                    main,scene=join(c)
                    assert main.player_name==payload["nickname"] and main.player_id==1000000+index
                    if index==2:
                        from test_native_gacha import call
                        assert call(c,"PlayerOffline",19).status==pb.StatusCode_OK
                assert disconnected.wait(0.5 if index==2 else 4)==(index==1)
                assert seats.grant is None
                resume={"slot":proof["slot"],"resume":grant.resume_token}
                if index==2:
                    assert submit("launch",resume)[0]==401
                    continue
                for _ in range(40):
                    with closing(sqlite3.connect(remote/"identity.sqlite3")) as db:
                        row=db.execute("SELECT reconnect_until FROM realm_native_resume WHERE slot=?",(proof["slot"],)).fetchone()
                    if row and row[0]>0:break
                    time.sleep(.05)
                def reconnect():
                    try:return proxy.resume(grant)
                    except ValueError:return None
                with ThreadPoolExecutor(max_workers=2) as pool:
                    replies=list(pool.map(lambda _: reconnect(),range(2)))
                accepted=[reply for reply in replies if reply]
                assert len(accepted)==1 and accepted[0].resume_token!=grant.resume_token
                seats.grant=accepted[0]
                with socket.create_connection(("127.0.0.1",game.port),timeout=5) as again:
                    again.sendall(encode(MsgId.VerifyLoginTokenReq,pb.VerifyLoginTokenReq(sdk_uid="development-uid",login_token="development-proof").SerializeToString(),1))
                    assert pb.VerifyLoginTokenRsp.FromString(receive(again)[1]).user_id==1000000+index
                    again.sendall(encode(MsgId.PlayerLoginReq,pb.PlayerLoginReq().SerializeToString(),2))
                    assert pb.PlayerLoginRsp.FromString(receive(again)[1]).player_name==payload["nickname"]
                    assert join(again)[0].player_id==1000000+index
                    from test_native_gacha import call
                    assert call(again,"PlayerOffline",20).status==pb.StatusCode_OK
                assert submit("launch",{"slot":proof["slot"],"resume":accepted[0].resume_token})[0]==401
            with closing(sqlite3.connect(front/"game.sqlite")) as db:
                assert not db.execute("SELECT player_id FROM private_slots").fetchall()
            assert all(key not in b"".join(seen) for key in (b"development-uid",b"development-proof",b"test-private-password"))
        finally:game.stop()
    finally:
        if proc.poll() is None:
            subprocess.run(cmd+["stop"],env=env,cwd=server,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:proc.wait(8)
            except subprocess.TimeoutExpired:proc.terminate();proc.wait()
    print("PASS native account login, same-role reconnect, single-use rotation, logout, expiry, revocation and channel privacy.")
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:run(Path(tmp),p.parse_args().resources)
