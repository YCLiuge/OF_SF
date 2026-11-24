"""A selected private-realm login endpoint can open an independent gameplay session on its owner's server."""
from contextlib import contextmanager, ExitStack
import http.client
import ipaddress
import json
import os
from pathlib import Path
import sqlite3
import time
from threading import Lock, Thread

from google.protobuf.message import DecodeError
from local_game_wire import encode, receive
from local_identity import PROFILE_KEY, SLOT, TICKET
from network.msg_id import MsgId
from proto import net_pb2 as pb
from social_game_fields import SocialGameFields
from ssh_game_channel import SshChannels, port_value, shutdown

REALM="overfield-local-v1"
TYPES={v:getattr(pb,k) for k,v in vars(MsgId).items() if type(v) is int and hasattr(pb,k)}
HANDSHAKE={MsgId.VerifyLoginTokenReq,MsgId.VerifyLoginTokenRsp,MsgId.PlayerLoginReq,MsgId.PlayerLoginRsp}

def clean_game_fields(msg):
    msg.DiscardUnknownFields()
    for field,value in msg.ListFields():
        if field.type in (field.TYPE_BYTES,field.TYPE_STRING):
            msg.ClearField(field.name)
        elif field.message_type:
            if field.message_type.GetOptions().map_entry:
                msg.ClearField(field.name)
            elif field.is_repeated:
                for part in value:
                    clean_game_fields(part)
            else:
                clean_game_fields(value)

class RemoteRealm:
    def __init__(self,identity,account,channels,http_port,game_port,owner,first):
        self.identity=identity
        self.remote_slot=account["slot"]
        self.private_key=account["profile_key"]
        self.channels=channels
        self.http_port=http_port
        self.game_port=game_port
        self.owner=owner
        self.first=first
        with identity._write() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS remote_game_owners (
                remote_slot TEXT PRIMARY KEY, local_slot TEXT NOT NULL REFERENCES profiles(slot))""")
            db.execute("""CREATE TABLE IF NOT EXISTS remote_local_settings (
                remote_slot TEXT NOT NULL, local_slot TEXT NOT NULL, k TEXT NOT NULL, v TEXT NOT NULL,
                PRIMARY KEY(remote_slot,local_slot,k))""")

    @classmethod
    def configured(cls,identity,bind):
        keys=("OF_REMOTE_SSH_TARGET","OF_REMOTE_PROFILE","OF_REMOTE_LOCAL_SLOT","OF_REMOTE_PAIR_FIRST")
        target,path,owner,first=(os.environ.get(k,"").strip() for k in keys)
        if not any((target,path,owner,first)):
            return None
        if not (target and path and ipaddress.ip_address(bind).is_loopback):
            raise ValueError("Remote gameplay requires a loopback login endpoint, SSH target and private profile")
        if first not in ("","0","1") or (owner and not SLOT.fullmatch(owner)):
            raise ValueError("Invalid local player selection for remote realm")
        file=Path(path)
        if not file.is_absolute() or not file.is_file() or file.stat().st_size>16384:
            raise ValueError("Choose the remote private realm profile file")
        try:
            account=json.loads(file.read_bytes())
            valid=(isinstance(account,dict) and account.get("realm")==REALM and
                   SLOT.fullmatch(account["slot"]) and PROFILE_KEY.fullmatch(account["profile_key"]))
        except (ValueError,KeyError,TypeError):
            valid=False
        if not valid:
            raise ValueError("Invalid remote private profile")
        http=port_value(os.environ.get("OF_REMOTE_HTTP_PORT","18881"))
        game=port_value(os.environ.get("OF_REMOTE_GAME_PORT","11033"))
        if http==game:
            raise ValueError("Profile and game ports must be distinct")
        channels=SshChannels(target,os.environ.get("OF_REMOTE_SSH_PORT"),os.environ.get("OF_REMOTE_SSH_CONFIG"))
        return cls(identity,account,channels,http,game,owner,first=="1")

    def authorize(self,slot,*,commit):
        with self.identity._write() as db:
            row=db.execute("SELECT local_slot FROM remote_game_owners WHERE remote_slot=?",(self.remote_slot,)).fetchone()
            if row is not None:
                if row[0]!=slot or (self.owner and self.owner!=slot):
                    raise ValueError("A different local account owns this remote profile")
                return
            exists=db.execute("SELECT 1 FROM profiles WHERE slot=?",(slot,)).fetchone()
            single=self.first and db.execute("SELECT COUNT(*) FROM profiles").fetchone()==(1,)
            allowed=(slot==self.owner) if self.owner else single
            if not exists or not allowed:
                raise ValueError("A specific local account is required for this remote profile")
            if commit:
                db.execute("INSERT INTO remote_game_owners VALUES (?,?)",(self.remote_slot,slot))

    def issue_ticket(self):
        form=json.dumps({"slot":self.remote_slot,"profile_key":self.private_key}).encode("ascii")
        with self.channels.open(self.http_port) as channel:
            session_api=http.client.HTTPConnection("127.0.0.1",self.http_port,timeout=14)
            session_api.sock=channel
            try:
                session_api.request("POST","/local/v1/sessions",form,headers={"Content-Type":"application/json","Connection":"close"})
                response=session_api.getresponse()
                if response.status!=200:
                    raise ValueError("The selected remote realm rejected the private profile")
                data=response.read(8193)
            finally:
                session_api.close()
        if len(data)>8192:
            raise ValueError("Invalid remote response")
        content=json.loads(data)
        region=content["region"]
        if (content["realm"]!=REALM or region["realm"]!=REALM or
                region["game_protocol"]!="overfield-packethead-protobuf" or region["game_port"]!=self.game_port or
                not TICKET.fullmatch(content["ticket"])):
            raise ValueError("Unexpected private realm or game ticket")
        return content["ticket"]

    @contextmanager
    def connect(self,slot,*,account_ticket=None):
        if account_ticket is None:
            self.authorize(slot,commit=False)
        with ExitStack() as stack:
            try:
                ticket=self.issue_ticket() if account_ticket is None else account_ticket
                stream=stack.enter_context(self.channels.open(self.game_port))
                request=pb.VerifyLoginTokenReq(login_token=ticket)
                stream.sendall(encode(MsgId.VerifyLoginTokenReq,request.SerializeToString(),1))
                del request,ticket
                h,b=receive(stream)
                if h.msg_id!=MsgId.VerifyLoginTokenRsp or h.packet_id!=1:
                    raise ValueError("Unexpected private realm response")
                reply=pb.VerifyLoginTokenRsp.FromString(b)
                if reply.status!=pb.StatusCode_OK or reply.user_id<=0 or reply.sdk_uid!=self.remote_slot:
                    raise ValueError("The selected remote realm rejected the private game ticket")
                if account_ticket is None:
                    self.authorize(slot,commit=True)
            except (OSError,EOFError,ValueError,TypeError,KeyError,sqlite3.Error,http.client.HTTPException,DecodeError) as exc:
                raise ValueError("Remote private game session could not be opened") from exc
            yield RemotePlayer(self,stream,slot,reply.user_id)

    def preference(self,slot,request):
        from private_archive import MAX_KEYS,MAX_KEY_BYTES,MAX_VALUE_BYTES
        setting=isinstance(request,pb.SetArchiveInfoReq)
        response=(pb.SetArchiveInfoRsp if setting else pb.GetArchiveInfoRsp)(status=pb.StatusCode_FAIL)
        if len(request.key.encode())>MAX_KEY_BYTES or (setting and len(request.value.encode())>MAX_VALUE_BYTES):
            return response
        args=(self.remote_slot,slot,request.key)
        with self.identity._write() as db:
            if setting:
                changed=db.execute("UPDATE remote_local_settings SET v=? WHERE remote_slot=? AND local_slot=? AND k=?",(request.value,*args)).rowcount
                if not changed:
                    count=db.execute("SELECT COUNT(*) FROM remote_local_settings WHERE remote_slot=? AND local_slot=?",args[:2]).fetchone()[0]
                    if count>=MAX_KEYS:return response
                    db.execute("INSERT INTO remote_local_settings VALUES(?,?,?,?)",(*args,request.value))
                response.value=request.value
            else:
                value=db.execute("SELECT v FROM remote_local_settings WHERE remote_slot=? AND local_slot=? AND k=?",args).fetchone()
                response.value=value[0] if value else ("1" if request.key=="BlessingTreeTutotal" else "")
        response.key=request.key
        response.status=pb.StatusCode_OK
        return response

class RemotePlayer:
    def __init__(self,realm,stream,slot,player_id):
        self.realm=realm
        self.stream=stream
        self.slot=slot
        self.player_id=player_id

    def play(self,client,host):
        stream=self.stream
        deadline=time.monotonic()+8
        reads=0
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError("Private login deadline expired")
            client.settimeout(remaining)
            head,body=receive(client)
            host.stages.message(head.msg_id)
            if head.msg_id==MsgId.PlayerLoginReq:
                break
            if head.msg_id!=MsgId.GetArchiveInfoReq or reads>=32:
                raise ValueError("Expected private player login or read-only preferences")
            reads+=1
            request=pb.GetArchiveInfoReq.FromString(body)
            reply=self.realm.preference(self.slot,request)
            client.sendall(encode(MsgId.GetArchiveInfoRsp,reply.SerializeToString(),head.packet_id,reads))
        pb.PlayerLoginReq.FromString(body)
        del body
        # Build the independent private login. Device, analytics and channel data stay on the client machine.
        stream.sendall(encode(MsgId.PlayerLoginReq,b"",head.packet_id))
        h,b=receive(stream)
        if h.msg_id!=MsgId.PlayerLoginRsp or h.packet_id!=head.packet_id:
            raise ValueError("Unexpected private player login")
        login=pb.PlayerLoginRsp.FromString(b)
        if login.status!=pb.StatusCode_OK:
            raise ValueError("Remote private player login rejected")
        login.DiscardUnknownFields()
        client.sendall(encode(h.msg_id,login.SerializeToString(),h.packet_id))
        client.settimeout(120)
        stream.settimeout(120)
        social=SocialGameFields()
        write_lock=Lock()
        sequence=reads+1
        def send_client(msg_id,body,packet_id):
            nonlocal sequence
            with write_lock:
                serial=0 if msg_id in (1002,1004,1006,1008) else sequence
                if serial:sequence=1 if sequence==0xffffffff else sequence+1
                client.sendall(encode(msg_id,body,packet_id,serial))
        def publish():
            try:
                while not host.closing.is_set():
                    h,b=receive(stream)
                    cls=TYPES.get(h.msg_id)
                    if cls is None or h.msg_id in HANDSHAKE or not cls.__name__.endswith(("Rsp","Notice")):
                        break
                    msg=cls.FromString(b)
                    msg.DiscardUnknownFields()
                    social.observe(msg)
                    send_client(h.msg_id,msg.SerializeToString(),h.packet_id)
            except (OSError,EOFError,ValueError,DecodeError):
                pass
            finally:
                shutdown(client)
        thread=Thread(target=publish,daemon=True,name="private-remote-events")
        thread.start()
        main_requested=False
        try:
            while not host.closing.is_set():
                head,body=receive(client)
                host.stages.message(head.msg_id)
                cls=TYPES.get(head.msg_id)
                if head.msg_id in HANDSHAKE or cls is None or not cls.__name__.endswith("Req"):
                    raise ValueError("Unsupported remote gameplay request")
                msg=cls.FromString(body)
                if head.msg_id not in host.handlers:
                    pair=host.failure_replies.get(head.msg_id)
                    if not main_requested or not pair:
                        raise ValueError("Unsupported remote gameplay request")
                    host.stages.unsupported(head.msg_id)
                    _,reply,reply_id=pair
                    send_client(reply_id,reply(status=pb.StatusCode_FAIL).SerializeToString(),head.packet_id)
                    continue
                if head.msg_id==MsgId.PlayerMainDataReq:main_requested=True
                if head.msg_id in (MsgId.GetArchiveInfoReq,MsgId.SetArchiveInfoReq):
                    result=self.realm.preference(self.slot,msg)
                    send_client(getattr(MsgId,result.DESCRIPTOR.name),result.SerializeToString(),head.packet_id)
                    continue
                if isinstance(msg,pb.SendChatMsgReq):
                    from private_chat_text import chat_text
                    if chat_text(msg.text) is None or not self.chat_guard.allows(msg.text):
                        send_client(MsgId.SendChatMsgRsp,pb.SendChatMsgRsp(status=pb.StatusCode_BAD_REQ).SerializeToString(),head.packet_id)
                        continue
                social.outgoing(msg,clean_game_fields)
                stream.sendall(encode(head.msg_id,msg.SerializeToString(),head.packet_id))
        finally:
            shutdown(stream)
            thread.join(timeout=2)
