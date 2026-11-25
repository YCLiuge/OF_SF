"""Private game + account gateway over real TLS, with independent regional and player processes."""
import argparse
import asyncio
from contextlib import closing, contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
from unittest.mock import patch

import player_gateway
from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from player_tls_channels import PlayerTlsChannels
from remote_password import RemotePassword
from test_game_realm import join
from test_private_profiles import request
from test_remote_realm import free_ports


def certificates(root):
    openssl=shutil.which("openssl")
    if not openssl and os.name=="nt":
        candidates=(Path(os.environ.get("ProgramFiles","C:/Program Files"))/"Git/usr/bin/openssl.exe",
                    Path(sys.prefix)/"Library/bin/openssl.exe")
        openssl=next((str(p) for p in candidates if p.is_file()),None)
    if not openssl:
        return None
    base=root/"certs"
    base.mkdir()
    conf=base/"cert.cnf"
    conf.write_text("[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=Test Authority\n"
                    "[ca]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n"
                    "[leaf]\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
                    "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost\n",encoding="ascii")
    def generate(*args):
        subprocess.run([openssl,*args],cwd=base,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=25)
    generate("req","-x509","-newkey","rsa:2048","-nodes","-sha256","-days","2","-keyout","ca.key",
             "-out","ca.pem","-config","cert.cnf","-extensions","ca")
    generate("req","-new","-newkey","rsa:2048","-nodes","-keyout","server.key","-out","server.csr",
             "-subj","/CN=localhost","-config","cert.cnf")
    generate("x509","-req","-in","server.csr","-CA","ca.pem","-CAkey","ca.key","-set_serial","1001",
             "-out","server.pem","-days","2","-sha256","-extfile","cert.cnf","-extensions","leaf")
    return base


class ClearChannels:
    """Only used when ephemeral certificate generation is unavailable."""
    def __init__(self,port,http_port,game_port):
        self.port,self.http,self.game=port,http_port,game_port

    @contextmanager
    def open(self, port):
        with socket.create_connection(("127.0.0.1",self.port),timeout=6) as sock:
            sock.sendall(b"OF-SF/1 ACCOUNT\n" if port==self.http else b"OF-SF/1 GAME\n")
            yield sock


@contextmanager
def gateway(http_port,game_port,certs,observed,state):
    loop=asyncio.new_event_loop()
    thread=Thread(target=loop.run_forever,daemon=True)
    thread.start()
    import player_gateway_peer
    gate=player_gateway.Gate(http_port,game_port,16,player_gateway_peer.load(state))
    loopback=player_gateway.loopback

    class RecordingWriter:
        def __init__(self,stream):
            self.stream=stream
        def write(self,data):
            observed.append(bytes(data))
            self.stream.write(data)
        def __getattr__(self,name):
            return getattr(self.stream,name)

    async def traced(port):
        read,write=await loopback(port)
        return read,RecordingWriter(write) if port==game_port else write

    async def start():
        if certs:
            ctx=player_gateway.tls(certs/"server.pem",certs/"server.key")
            return await player_gateway.listen(gate,"127.0.0.1",0,ctx)
        return await asyncio.start_server(gate.accept,"127.0.0.1",0,limit=8192)

    async def stop(server):
        server.close()
        await server.wait_closed()
        for _ in range(80):
            if not gate.connected:
                break
            await asyncio.sleep(.05)
        assert not gate.connected, "Gateway retained closed player sessions"

    try:
        with patch.object(player_gateway,"loopback",traced):
            server=asyncio.run_coroutine_threadsafe(start(),loop).result(10)
            try:
                yield server.sockets[0].getsockname()[1]
            finally:
                asyncio.run_coroutine_threadsafe(stop(server),loop).result(7)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()


def media_request(channels,port,method,path):
    with channels.open(port) as stream:
        conn=http.client.HTTPConnection("127.0.0.1",port,timeout=7)
        conn.sock=stream
        conn.request(method,path)
        response=conn.getresponse()
        return response.status,response.getheader("Content-Length"),response.read()


def source_limits(port, tls_context):
    # Two actual TCP peers behind one gateway plus independently authenticated source buckets.
    import player_gateway_peer as proof
    def send(source,action):
        with socket.create_connection(("127.0.0.1",port),timeout=5,source_address=(source,0)) as tcp:
            with (tls_context.wrap_socket(tcp,server_hostname="localhost") if tls_context else tcp) as sock:
                sock.sendall(b"OF-SF/1 ACCOUNT\n")
                conn=http.client.HTTPConnection("localhost",port,timeout=5)
                conn.sock=sock
                conn.request("POST","/local/v1/accounts/"+action,b"{}",{"Content-Type":"application/json",
                              "X-Forwarded-For":"127.0.0.1",proof.HEADER:"untrusted-client-header"})
                rsp=conn.getresponse()
                rsp.read()
                return rsp.status
    a,b="127.0.0.2","127.0.0.3"
    assert [send(a,"register") for _ in range(7)] == [400]*6+[429]
    assert send(b,"register")==400
    assert [send(a,"login") for _ in range(31)] == [400]*30+[429]
    assert send(b,"login")==400
    assert [send("127.0.0.1","register") for _ in range(7)] == [400]*7


def run(root,resources,certs):
    package=Path(__file__).resolve().parents[1]
    remote,front=root/"world",root/"player"
    remote.mkdir();front.mkdir()
    http_port,game_port=free_ports()
    env={k:v for k,v in os.environ.items() if not k.startswith("OF_")}
    env.update(OF_STATE=str(remote),OF_HTTP_PORT=str(http_port),OF_GAME_PORT=str(game_port),OF_ACCOUNT_REGISTRATION="open")
    cli=[sys.executable,"-B",str(package/"of.py")]
    proc=subprocess.Popen(cli+["start"],cwd=package,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def stop_world():
        if proc.poll() is None:
            subprocess.run(cli+["stop"],cwd=package,env=env,capture_output=True,timeout=8,check=True)
            assert proc.wait(12)==0
    def account(method,path,data=None,raw=None):
        with channels.open(http_port) as secure:
            conn=http.client.HTTPConnection("127.0.0.1",http_port,timeout=7)
            conn.sock=secure
            headers={"Content-Type":"application/json"} if data is not None or raw is not None else {}
            body=json.dumps(data).encode() if data is not None else raw
            conn.request(method,path,body,headers)
            response=conn.getresponse()
            body=response.read()
            return response.status, json.loads(body) if body else {}
    def account_route(kind,fields):
        return account("POST","/local/v1/accounts/"+kind,fields)
    observed=[]
    try:
        for _ in range(100):
            try:
                if request(http_port,"GET","/healthz")[0]==200:
                    break
            except OSError:
                pass
            if proc.poll() is not None:
                raise RuntimeError("Test realm terminated during startup")
            time.sleep(.1)
        else:
            raise TimeoutError("Test realm did not start")
        with gateway(http_port,game_port,certs,observed,remote) as gateway_port:
            def tls_client(name,ca):
                return PlayerTlsChannels(f"https://{name}:{gateway_port}",ca,
                                         account_port=http_port,game_port=game_port)
            if certs:
                ca=str(certs/"ca.pem")
                channels=tls_client("localhost",ca)
                for hostname,trust in (("127.0.0.1",ca),("localhost",None)):
                    try:
                        with tls_client(hostname,trust).open(http_port):
                            raise AssertionError("Rejected certificate accepted")
                    except ssl.SSLCertVerificationError:
                        pass
            else:
                channels=ClearChannels(gateway_port,http_port,game_port)
            source_limits(gateway_port,channels.context if certs else None)
            from player_gateway_peer import HEADER
            with closing(http.client.HTTPConnection("127.0.0.1",http_port,timeout=5)) as local_admin:
                local_admin.request("POST","/local/v1/accounts/register",b"{}",{"Content-Type":"application/json",
                                    HEADER:"unverified-origin"})
                assert local_admin.getresponse().status==403
            assert account("GET","/local/v1/region")[0]==200
            from realm_maintenance import Maintenance
            from test_realm_maintenance import Tests
            owner=Maintenance(LocalIdentity(remote/"identity.sqlite3"))
            png=root/"notice.png"
            png.write_bytes(Tests.png())
            owner.announce("测试图片","证书和附件测试",[png])
            artifact=root/"test.zip"
            artifact.write_bytes(b"test package for HEAD and GET")
            digest=hashlib.sha256(artifact.read_bytes()).hexdigest()
            owner.publish(artifact,"1.2.2",digest,"临时资源")
            for route,blob in (("/private/notice-images/"+hashlib.sha256(png.read_bytes()).hexdigest(),png.read_bytes()),
                               ("/private/releases/"+digest,artifact.read_bytes())):
                assert media_request(channels,http_port,"GET",route)==(200,str(len(blob)),blob)
                assert media_request(channels,http_port,"HEAD",route)==(200,str(len(blob)),b"")
            assert media_request(channels,http_port,"HEAD","/local/v1/region")[0]==404
            assert media_request(channels,http_port,"GET","/private/photos/invalid")[0]==404
            assert media_request(channels,http_port,"GET","/private-game/photos/123")[0]==404
            assert account("GET","/admin/v1/openapi.json")[0]==404
            assert account("POST","/local/v1/accounts/attach",{})[0]==404
            assert account("POST","/local/v1/accounts/login",raw=b" "*2049)[0]==413
            login_fields={"username":"tls-account","password":"private-tls-CREDENTIAL-42"}
            signup=dict(login_fields,nickname="测试载入")
            assert account_route("register",signup)==(428,{"error":"captcha_required"})
            assert "captcha_lookup_v1" in account("GET","/local/v1/region")[1]["account_features"]
            # This suite starts its authoritative HTTP service in a separate
            # subprocess. The captcha roundtrip is tested with an in-memory
            # renderer in test_player_captcha; provision this transport fixture locally.
            status,created=request(http_port,"POST","/local/v1/accounts/register",signup)
            assert status==201
            assert account_route("login",dict(login_fields,password="incorrect-test"))[0]==401
            status,signed=account_route("login",login_fields)
            assert status==200 and signed["slot"]==created["slot"]

            local=LocalIdentity(front/"identity.sqlite3")
            proxy=RemotePassword(local,channels,http_port,game_port)
            direct=proxy.reserve(created["slot"],created["ticket"])
            with channels.open(game_port) as world:
                mixed=pb.VerifyLoginTokenReq(login_token=direct.ticket,sdk_uid="synthetic-extra-uid",
                    channel_code="synthetic-extra-channel",device_uuid="synthetic-extra-device")
                world.sendall(encode(MsgId.VerifyLoginTokenReq,mixed.SerializeToString(),1))
                header,body=receive(world)
                assert header.msg_id==MsgId.VerifyLoginTokenRsp and pb.VerifyLoginTokenRsp.FromString(body).status==pb.StatusCode_OK
                world.sendall(encode(MsgId.PlayerLoginReq,pb.PlayerLoginReq().SerializeToString(),2))
                head,body=receive(world)
                assert pb.PlayerLoginRsp.FromString(body).status==pb.StatusCode_OK
                join(world)
                from test_native_gacha import call
                from test_native_photos import WEBP
                photo=call(world,"UploadPhotoShare",19,file=WEBP,title="照片",tag1="风景",camera_param=pb.CameraParameters(camera_version=3))
                assert photo.status==pb.StatusCode_OK, photo.status
                for prefix in ("/private/photos/","/private/photos/thumb/"):
                    route=prefix+str(photo.info.code)
                    assert media_request(channels,http_port,"GET",route)==(200,str(len(WEBP)),WEBP)
                    assert media_request(channels,http_port,"HEAD",route)==(200,str(len(WEBP)),b"")
            assert account_route("launch",{"slot":created["slot"],"ticket":created["ticket"]})[0]==401
            # Decode the very first frame delivered to the real native server after gateway verification.
            fresh=pb.VerifyLoginTokenReq(login_token=direct.ticket)
            assert observed[0]==encode(MsgId.VerifyLoginTokenReq,fresh.SerializeToString(),1)

            grant=proxy.reserve(signed["slot"],signed["ticket"])
            from native_account_seats import NativeAccountSeats
            test_exe=front/"launcher.exe"
            seats=NativeAccountSeats(local,test_exe)
            seats.remote=proxy
            seats.pending[21]=(73,grant,time.monotonic()+300)
            class LocalChannel:
                def verify_channel(self,uid,token,peer):
                    assert (uid,token,peer)==("synthetic-only-uid","synthetic-only-proof","127.0.0.1")

            local_game=LocalGameService(local,front,resources,port=0)
            local_game.remote_password=proxy
            local_game.online_identity=LocalChannel()
            local_game.native_accounts=seats
            local_game.start()
            try:
                from contextlib import ExitStack
                with ExitStack() as native:
                    native.enter_context(patch("native_account_seats.connection_pid",return_value=21))
                    native.enter_context(patch("native_account_seats.process_info",return_value=(test_exe,7,73)))
                    with socket.create_connection(("127.0.0.1",local_game.port),timeout=9) as first:
                        verify=pb.VerifyLoginTokenReq(sdk_uid="synthetic-only-uid",login_token="synthetic-only-proof",
                            device_uuid="synthetic-front-device",channel_code="synthetic-channel-name")
                        first.sendall(encode(MsgId.VerifyLoginTokenReq,verify.SerializeToString(),1))
                        assert pb.VerifyLoginTokenRsp.FromString(receive(first)[1]).user_id==1000001
                        first.sendall(encode(MsgId.PlayerLoginReq,b"",2))
                        assert pb.PlayerLoginRsp.FromString(receive(first)[1]).status==pb.StatusCode_OK
                        assert join(first)[0].player_id==1000001
                    for _ in range(80):
                        with closing(sqlite3.connect(remote/"identity.sqlite3")) as db:
                            ready=db.execute("SELECT reconnect_until FROM realm_native_resume WHERE slot=?",(grant.slot,)).fetchone()
                        if seats.disconnected_owner and ready and ready[0]>0:break
                        time.sleep(.05)
                    assert seats.disconnected_owner and ready and ready[0]>0
                    # Verify the actual player's next socket goes through the same native seat and TLS tunnel.
                    current=socket.create_connection(("127.0.0.1",local_game.port),timeout=9)
                    try:
                        current.sendall(encode(MsgId.VerifyLoginTokenReq,verify.SerializeToString(),1))
                        assert pb.VerifyLoginTokenRsp.FromString(receive(current)[1]).user_id==1000001
                    except BaseException:
                        current.close()
                        raise
                with current as client:
                    fields=pb.PlayerLoginReq(device_model="synthetic-front-model",analysis_distinct_id="synthetic-analytics")
                    client.sendall(encode(MsgId.PlayerLoginReq,fields.SerializeToString(),2))
                    head,body=receive(client)
                    assert head.msg_id==MsgId.PlayerLoginRsp and pb.PlayerLoginRsp.FromString(body).player_name=="测试载入"
                    main,scene=join(client)
                    assert main.player_id==1000001 and main.player_name=="测试载入" and main.scene_id==scene.data.scene_id
                    client.sendall(encode(MsgId.PlayerPingReq,pb.PlayerPingReq(client_time_ms=123).SerializeToString(),16))
                    for _ in range(64):
                        head,body=receive(client)
                        if head.msg_id==MsgId.PlayerPingRsp:
                            assert pb.PlayerPingRsp.FromString(body).client_time_ms==123
                            break
                    else:
                        raise AssertionError("No native game ping over TLS")
                    assert b"synthetic" not in b"".join(observed) and login_fields["password"].encode() not in b"".join(observed)
                    stop_world()
                    # Already-established front-game/TLS/realm traffic must also unwind.
                    client.settimeout(6)
                    try:
                        while client.recv(65536):
                            pass
                    except (ConnectionResetError,ConnectionAbortedError):
                        pass
                with closing(sqlite3.connect(front/"game.sqlite")) as db:
                    assert not db.execute("SELECT 1 FROM private_slots").fetchone()
            finally:
                local_game.stop()
        try:
            with socket.create_connection(("127.0.0.1",gateway_port),timeout=5):
                raise AssertionError("Stopped gateway still accepted connections")
        except (ConnectionRefusedError,ConnectionResetError):
            pass
    finally:
        if proc.poll() is None:
            try:
                stop_world()
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(5)
    print("PASS public player routes, private registration/login/ticket exchange, native remote world, "
          "field isolation, management path denial, size limit and connected shutdown.")
    print("PASS TLS temporary CA, hostname verification and untrusted-root rejection." if certs else
          "SKIP TLS certificate handshake (OpenSSL generator unavailable); transport and gameplay regressions passed.")


async def managed_terminator(root):
    import os
    import sys
    from player_gateway_terminator import Terminator
    identity=root/"frpc-process.id"
    try:
        Terminator("0.0.0.0",identity)
    except ValueError:
        pass
    else:
        raise AssertionError("Managed plaintext listener allowed an external binding")
    if sys.platform!="win32":
        return
    from player_gateway_terminator import start_time
    class Probe:
        async def accept(self,reader,writer):
            writer.write(b"verified")
            await player_gateway.close(writer)
    server=await player_gateway.listen_terminated(Probe(),"127.0.0.1",0,identity)
    async with server:
        async def result():
            reader,writer=await asyncio.open_connection(*server.sockets[0].getsockname())
            try:
                return await asyncio.wait_for(reader.read(),2)
            finally:
                await player_gateway.close(writer)
        assert await result()==b""
        identity.write_text(f"{os.getpid()} 1","ascii")
        assert await result()==b""
        identity.write_text(f"1 {start_time(os.getpid())}","ascii")
        assert await result()==b""
        identity.write_text(f"{os.getpid()} {start_time(os.getpid())}","ascii")
        assert await result()==b"verified"
        identity.unlink()
        assert await result()==b""


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        root=Path(tmp)
        asyncio.run(managed_terminator(root))
        run(root,parser.parse_args().resources,certificates(root))
