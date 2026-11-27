"""Two independent realms; normal private TCP and a synthetic input login. No internet access."""
import argparse
from contextlib import contextmanager, nullcontext, closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from local_game_service import LocalGameService,MsgId,pb
from local_game_wire import encode,receive
from local_identity import LocalIdentity
from ssh_game_channel import SshChannels
from test_game_realm import join,next_msg

class ObservedSocket:
    def __init__(self,stream,seen):
        self._stream=stream
        self._seen=seen
    def __getattr__(self,name):
        return getattr(self._stream,name)
    def sendall(self,data):
        self._seen.append(bytes(data))
        return self._stream.sendall(data)

def free_ports():
    with socket.socket() as a,socket.socket() as b:
        a.bind(("127.0.0.1",0));b.bind(("127.0.0.1",0))
        return a.getsockname()[1],b.getsockname()[1]

def run(resources,ssh_config=None,ssh_port=None,http_port=None,game_port=None):
    source=Path(__file__).resolve().parents[1]
    local_uid="SYNTHETIC-PLATFORM-USER"
    local_token="SYNTHETIC-PLATFORM-TOKEN"
    device="SYNTHETIC-DEVICE-ID"
    private_note="SYNTHETIC-LOCAL-SETTING"
    seen=[]
    if http_port is None:http_port,game_port=free_ports()
    @contextmanager
    def channel(channels,port):
        assert port in (http_port,game_port)
        with socket.create_connection(("127.0.0.1",port),timeout=4) as s:
            yield ObservedSocket(s,seen)
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        remote_state=root/"remote";remote_state.mkdir()
        remote_id=LocalIdentity(remote_state/"identity.sqlite3")
        rslot,rkey=remote_id.create_profile("异地私服角色")
        profile=root/"remote.json"
        profile.write_text(json.dumps(dict(realm="overfield-local-v1",slot=rslot,profile_key=rkey)),"utf8")
        henv={k:v for k,v in os.environ.items() if not k.startswith("OF_")}
        henv.update(OF_STATE=str(remote_state),OF_BIND="127.0.0.1",OF_GAME_HOST="127.0.0.1",
                    OF_HTTP_PORT=str(http_port),OF_GAME_PORT=str(game_port))
        cmd=[sys.executable,"-B",str(source/"of.py")]
        proc=subprocess.Popen([*cmd,"start"],cwd=source,env=henv,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(60):
                if proc.poll() is not None:raise AssertionError("isolated remote service failed")
                if (remote_state/".active-server").exists():break
                try:
                    with socket.create_connection(("127.0.0.1",http_port),timeout=.2):break
                except OSError:time.sleep(.1)
            front_state=root/"front";front_state.mkdir()
            local=LocalIdentity(front_state/"identity.sqlite3")
            slot,key=local.create_profile("本机认证角色")
            local_opt={"OF_REMOTE_SSH_TARGET":"game-qa","OF_REMOTE_PROFILE":str(profile),
                       "OF_REMOTE_HTTP_PORT":str(http_port),"OF_REMOTE_GAME_PORT":str(game_port),
                       "OF_REMOTE_PAIR_FIRST":"1","OF_REMOTE_LOCAL_SLOT":"","OF_QUICKSDK_VERIFY_URL":""}
            if ssh_config:
                local_opt["OF_REMOTE_SSH_CONFIG"]=str(ssh_config)
                local_opt["OF_REMOTE_SSH_PORT"]=str(ssh_port)
            with patch.dict(os.environ,local_opt), (nullcontext() if ssh_config else patch.object(SshChannels,"open",channel)):
                # Embedded protocol tests do not need the optional system OpenSSH program installed.
                with (nullcontext() if ssh_config else patch("ssh_game_channel.shutil.which",return_value="synthetic-ssh")):
                    front=LocalGameService(local,front_state,resources,port=0)
                def authentic(uid,token,peer):
                    if (uid,token,peer)!=(local_uid,local_token,"127.0.0.1"):
                        raise ValueError("synthetic identity denied")
                    return slot
                class FixtureIssuer:
                    authenticate=staticmethod(authentic)
                front.online_identity=FixtureIssuer()
                front.start()
                try:
                    def connect(uid,token):
                        sock=socket.create_connection(("127.0.0.1",front.port),timeout=25)
                        req=pb.VerifyLoginTokenReq(sdk_uid=uid,login_token=token)
                        sock.sendall(encode(MsgId.VerifyLoginTokenReq,req.SerializeToString(),1))
                        h,b=receive(sock)
                        assert h.msg_id==MsgId.VerifyLoginTokenRsp
                        return sock,pb.VerifyLoginTokenRsp.FromString(b)
                    s,denied=connect(local_uid,"invalid-local-token")
                    with s:assert denied.status!=pb.StatusCode_OK
                    assert not seen
                    s,ok=connect(local_uid,local_token)
                    with s:
                        assert ok.status==pb.StatusCode_OK and ok.user_id and ok.sdk_uid==local_uid
                        pre=pb.GetArchiveInfoReq(key="BlessingTreeTutotal")
                        s.sendall(encode(MsgId.GetArchiveInfoReq,pre.SerializeToString(),19))
                        assert next_msg(s,MsgId.GetArchiveInfoRsp,pb.GetArchiveInfoRsp).value=="1"
                        signin=pb.PlayerLoginReq(device_uuid=device,analysis_distinct_id=device,fg_info=device.encode())
                        s.sendall(encode(MsgId.PlayerLoginReq,signin.SerializeToString(),2))
                        info=next_msg(s,MsgId.PlayerLoginRsp,pb.PlayerLoginRsp)
                        assert info.player_name=="异地私服角色"
                        main,scene=join(s)
                        assert main.player_name==info.player_name and scene.data.scene_id==100
                        s.sendall(encode(MsgId.PlayerPingReq,pb.PlayerPingReq(client_time_ms=193).SerializeToString(),5))
                        assert next_msg(s,MsgId.PlayerPingRsp,pb.PlayerPingRsp).client_time_ms==193
                        from test_observed_initialization import call
                        found=call(s,"FriendSearch",search_args=main.player_name)
                        assert found.status==pb.StatusCode_OK and found.data.player_id==main.player_id
                        assert call(s,"FriendSearch",search_args=str(main.player_id)).data.player_id==main.player_id
                        assert call(s,"SelfIntervalInit").interval_id==101
                        assert call(s,"IntervalStart").status==pb.StatusCode_OK
                        rift=call(s,"SelfIntervalInit").interval
                        assert rift.create_time>0
                        assert call(s,"IntervalReward",friend_id=main.player_id,interval_param=str(rift.create_time)).status==pb.StatusCode_INTERVAL_NOT_FINISH
                        assert call(s,"IntervalReward",friend_id=main.player_id,interval_param=private_note).status==pb.StatusCode_INTERVAL_NOT_EXISTS
                        # A chat message is the only explicitly authored text allowed on this path.
                        from test_native_chat import barrier
                        assert call(s,"ChangeChatChannel",channel_id=9999).channel_id==9999
                        assert call(s,"SendChatMsg",type=pb.ChatChannel_ChatRoom,text="remote-chat-qa <b>原\u200b客户端</b>\ufeff").text=="remote-chat-qa ＜b＞原客户端＜/b＞"
                        for forbidden in (local_token,local_uid,slot,slot[:3]+"\u200b"+slot[3:],"login_token=must-stay-local",chr(1)+"invalid"):
                            assert call(s,"SendChatMsg",type=pb.ChatChannel_ChatRoom,text=forbidden).status==pb.StatusCode_BAD_REQ
                        barrier(s)
                        req=pb.SetArchiveInfoReq(key="qa-local",value=private_note)
                        s.sendall(encode(MsgId.SetArchiveInfoReq,req.SerializeToString(),6))
                        assert next_msg(s,MsgId.SetArchiveInfoRsp,pb.SetArchiveInfoRsp).value==private_note
                        s.sendall(encode(MsgId.SceneSitChairReq,pb.SceneSitChairReq(chair_id=58,seat_id=2,is_sit=True).SerializeToString(),7))
                        chair=next_msg(s,MsgId.SceneSitChairRsp,pb.SceneSitChairRsp)
                        assert chair.status==pb.StatusCode_OK and chair.is_sit and chair.chair_id==58
                    other,secret=local.create_profile("另一本机角色")
                    s,denied=connect(other,local.issue_ticket(other,secret))
                    with s:assert denied.status!=pb.StatusCode_OK
                    s,again=connect(slot,local.issue_ticket(slot,key))
                    with s:
                        assert again.status==pb.StatusCode_OK and again.user_id==ok.user_id
                        s.sendall(encode(MsgId.PlayerLoginReq,b"",11))
                        assert next_msg(s,MsgId.PlayerLoginRsp,pb.PlayerLoginRsp).player_name=="异地私服角色"
                        join(s)
                        from test_native_chat import history
                        assert "remote-chat-qa ＜b＞原客户端＜/b＞" in history(barrier(s),pb.ChatChannel_ChatRoom)
                        s.sendall(encode(MsgId.GetArchiveInfoReq,pb.GetArchiveInfoReq(key="qa-local").SerializeToString(),12))
                        reply=next_msg(s,MsgId.GetArchiveInfoRsp,pb.GetArchiveInfoRsp)
                        assert (reply.key,reply.value)==("qa-local",private_note)
                finally:front.stop()
                if not ssh_config:
                    wire=b"".join(seen)
                    assert rkey.encode() in wire and b"oft_" in wire and b"remote-chat-qa" in wire and b"must-stay-local" not in wire
                    for val in (local_uid,local_token,device,private_note,slot,'异地私服角色'):
                        assert val.encode() not in wire
                with closing(sqlite3.connect(remote_state/"game.sqlite")) as db:
                    assert db.execute("SELECT slot FROM private_slots").fetchall()==[(rslot,)]
                with closing(sqlite3.connect(front_state/"game.sqlite")) as db:
                    assert db.execute("SELECT count(*) FROM private_slots").fetchone()[0]==0
        finally:
            if proc.poll() is None:
                subprocess.run([*cmd,"stop"],env=henv,cwd=source,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                try:proc.wait(timeout=8)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
    print("PASS paired remote private role, typed game session, remote scene 9999, heartbeat, account isolation, rejoin and local preferences"+(" (real OpenSSH)." if ssh_config else " (loopback transport fixture)."))

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--resources",type=Path,required=True)
    p.add_argument("--ssh-config",type=Path);p.add_argument("--ssh-port",type=int)
    p.add_argument("--http-port",type=int);p.add_argument("--game-port",type=int)
    a=p.parse_args();run(a.resources,a.ssh_config,a.ssh_port,a.http_port,a.game_port)
