
"""Verify a dedicated private profile against its operator-specified realm."""
import ipaddress
import json
import socket
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from pathlib import Path

from local_game_wire import encode, receive
from network.msg_id import MsgId
from proto import net_pb2 as pb


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_player(endpoint, profile, *, trusted_vpn=False):
    target = urlsplit(endpoint)
    if target.scheme not in ("http","https") or not target.hostname or target.username or target.password or target.query or target.fragment or target.path.strip("/"):
        raise ValueError("Specify the root HTTP(S) URL of your private realm.")
    try:
        local=ipaddress.ip_address(target.hostname).is_loopback
    except ValueError:
        local=target.hostname.lower()=="localhost"
    if target.scheme=="http" and not local and not trusted_vpn:
        raise ValueError("Use HTTPS or add --trusted-vpn from your private network.")
    account=json.loads(Path(profile).read_bytes())
    realm="overfield-local-v1"
    if account.get("realm")!=realm: raise ValueError("Expected a private-realm profile file.")
    http=build_opener(ProxyHandler({}),NoRedirect())
    def call(path, payload=None):
        req=Request(endpoint.rstrip("/") + path, data=json.dumps(payload).encode() if payload is not None else None,
                    headers={"Content-Type":"application/json"})
        with http.open(req,timeout=10) as response:
            data=response.read(65537)
            if len(data)>65536:raise ValueError("Unexpected profile response size.")
            return json.loads(data)
    region=call("/local/v1/region")
    if region.get("realm")!=realm or region.get("game_protocol")!="overfield-packethead-protobuf":
        raise ValueError("The URL does not advertise the expected private game protocol.")
    session=call("/local/v1/sessions",{k:account[k] for k in ("slot","profile_key")})
    if session.get("realm")!=realm or session.get("region")!=region:
        raise ValueError("Private realm metadata changed.")
    with socket.create_connection((region["game_host"],region["game_port"]),timeout=12) as stream:
        stream.settimeout(20)
        def send(cmd, msg, number):
            stream.sendall(encode(cmd,msg.SerializeToString(),number))
        def get(cmd, cls):
            for _ in range(160):
                h,b=receive(stream)
                if h.msg_id==cmd:return cls.FromString(b)
            raise ValueError("Expected gameplay response missing.")
        send(MsgId.VerifyLoginTokenReq,pb.VerifyLoginTokenReq(login_token=session["ticket"]),1)
        if get(MsgId.VerifyLoginTokenRsp,pb.VerifyLoginTokenRsp).status!=pb.StatusCode_OK:
            raise ValueError("Game connection ticket rejected.")
        send(MsgId.PlayerLoginReq,pb.PlayerLoginReq(),2)
        login=get(MsgId.PlayerLoginRsp,pb.PlayerLoginRsp)
        if login.status!=pb.StatusCode_OK or not login.player_name.strip():
            raise ValueError("Player login did not return a nickname.")
        send(MsgId.PlayerMainDataReq,pb.PlayerMainDataReq(),3)
        main=get(MsgId.PlayerMainDataRsp,pb.PlayerMainDataRsp)
        scene=get(MsgId.SceneDataNotice,pb.SceneDataNotice)
        if not main.characters or main.scene_id!=scene.data.scene_id or not scene.data.players:
            raise ValueError("Incomplete player or first-scene response.")
        print("Login confirmed. Nickname present. Scene %d, %d character(s)." % (scene.data.scene_id,len(main.characters)))
        return scene.data.scene_id

