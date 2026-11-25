"""Exchange a selected private account ticket inside its own authenticated SSH tunnel."""
import http.client
import ipaddress
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

from local_identity import SLOT,TICKET
from remote_realm import RemoteRealm,REALM
from ssh_game_channel import SshChannels,port_value


@dataclass(frozen=True)
class RemoteLogin:
    slot:str
    ticket:str
    on_disconnect:Callable[[],None] | None=None
    resume_token:str | None=None

RESUME = re.compile(r"ofr_[A-Za-z0-9_-]{43}\Z", re.ASCII)

class SessionStillClosing(BlockingIOError):
    pass


class RemotePassword:
    def __init__(self,identity,channels,http_port,game_port):
        self.identity,self.channels=identity,channels
        self.http_port,self.game_port=http_port,game_port

    @classmethod
    def configured(cls,identity,bind):
        if os.environ.get("OF_REMOTE_ACCOUNT_NATIVE","0")!="1":
            return None
        if not ipaddress.ip_address(bind).is_loopback or os.environ.get("OF_ACCOUNT_NATIVE")!="1":
            raise ValueError("Remote native account selection requires the local private game launcher")
        http_port = port_value(os.environ.get("OF_REMOTE_HTTP_PORT", "18881"))
        game_port = port_value(os.environ.get("OF_REMOTE_GAME_PORT", "11033"))
        if os.environ.get("OF_PLAYER_REALM_TLS"):
            from player_tls_channels import PlayerTlsChannels
            channels = PlayerTlsChannels(os.environ["OF_PLAYER_REALM_TLS"], os.environ.get("OF_PLAYER_REALM_CA"),
                                         account_port=http_port, game_port=game_port)
        else:
            channels = SshChannels(os.environ.get("OF_REMOTE_SSH_TARGET", ""),os.environ.get("OF_REMOTE_SSH_PORT"),os.environ.get("OF_REMOTE_SSH_CONFIG"))
        return cls(identity, channels, http_port, game_port)

    def reserve(self,slot,ticket):
        if not isinstance(slot,str) or not SLOT.fullmatch(slot) or not isinstance(ticket,str) or not TICKET.fullmatch(ticket):
            raise ValueError("Invalid private realm ticket")
        return self._exchange(slot,{"ticket":ticket})

    def resume(self,login):
        for delay in (0,.2,.5):
            if delay:
                time.sleep(delay)
            try:
                return self._exchange(login.slot,{"resume":login.resume_token})
            except SessionStillClosing:
                continue
            except http.client.HTTPException as failure:
                raise OSError("Private reconnect transport interrupted") from failure
        raise BlockingIOError("Previous game connection is still closing")

    def _exchange(self,slot,credential):
        with self.channels.open(self.http_port) as channel:
            conn=http.client.HTTPConnection("127.0.0.1",self.http_port,timeout=12)
            conn.sock=channel
            try:
                conn.request("POST","/local/v1/accounts/launch",json.dumps({"slot":slot,**credential}).encode(),{"Content-Type":"application/json","Connection":"close"})
                response=conn.getresponse()
                raw=response.read(4097)
                if response.status==409 and "resume" in credential:
                    raise SessionStillClosing("Previous game connection is still closing")
                if response.status in (429,502,503,504) and "resume" in credential:
                    raise BlockingIOError("Private reconnect is not ready")
                if response.status!=200 or len(raw)>4096:
                    raise ValueError("Private account reservation refused")
                data=json.loads(raw)
                if not isinstance(data,dict) or data.get("realm")!=REALM or data.get("slot")!=slot:
                    raise ValueError("Unexpected private account response")
                fresh=data.get("ticket")
                resume=data.get("resume")
                if not isinstance(fresh,str) or not TICKET.fullmatch(fresh) or (resume is not None and (not isinstance(resume,str) or not RESUME.fullmatch(resume))):
                    raise ValueError("Unexpected private account response")
                return RemoteLogin(slot,fresh,resume_token=resume)
            finally:
                conn.close()

    @contextmanager
    def connect(self,login):
        # Each authenticated slot gets its own archive namespace and gameplay session.
        realm=RemoteRealm(self.identity,{"slot":login.slot,"profile_key":None},self.channels,self.http_port,self.game_port,None,False)
        with realm.connect(login.slot,account_ticket=login.ticket) as player:
            try:
                yield player
            finally:
                if login.on_disconnect and player.resume_needed:
                    login.on_disconnect()
