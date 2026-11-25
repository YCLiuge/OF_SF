"""Account verification through the actual TLS gateway; readable answers exist only in this fixture."""
from base64 import b64decode
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch

from local_identity import LocalIdentity
from private_profiles import create_profile_server
from player_captcha import PlayerCaptcha,picture
from realm_accounts import AccountError
from test_native_player_gateway import certificates,gateway,ClearChannels
from player_tls_channels import PlayerTlsChannels


def replay_boundaries():
    captcha=PlayerCaptcha()
    answers=[]
    def draw(value):
        answers.append(value)
        return picture(value)
    now=[0.]
    with patch("player_captcha.picture",side_effect=draw),patch("player_captcha.monotonic",side_effect=lambda:now[0]):
        def issue(user="player",source="host",purpose="lookup"):
            out=captcha.issue({"username":user,"purpose":purpose},source)
            return {"challenge_id":out["challenge_id"],"answer":answers[-1]}
        def verify(fields,source="host",user="player",purpose="lookup",status=None):
            try:
                captcha.verify(dict(fields),user,source,purpose,True)
            except AccountError as exc:
                assert status==exc.status,(status,exc.code)
                return False
            assert status is None
            return True
        current=issue()
        verify(current,"peer",status=400)
        verify(current,user="other",status=400)
        verify(current,purpose="register",status=400)
        verify(current)
        verify(current,status=400)
        wrong=issue()
        verify(dict(wrong,answer="wrong"),status=400)
        verify(wrong,status=400)
        timed=issue()
        now[0]+=121
        verify(timed,status=400)
        signed=captcha.lookup_proof("host","player",False)["account_proof"]
        verify({"account_proof":signed},purpose="login",status=400)
        verify({"account_proof":signed},source="peer",purpose="register",status=400)
        def concurrent(_):
            try:captcha.verify({"account_proof":signed},"player","host","register",True)
            except AccountError:return False
            return True
        with ThreadPoolExecutor(2) as jobs:
            assert sorted(jobs.map(concurrent,range(2)))==[False,True]
        for _ in range(6):issue("limited")
        try:issue("limited")
        except AccountError as error:assert error.status==429
        else:raise AssertionError("challenge throttle not enforced")


def run():
    replay_boundaries()
    def ensure_limited(pairs,shared=False):
        guard=PlayerCaptcha()
        for source,user in pairs[:-1]:
            guard.limit(source,user,shared=shared)
        try:guard.limit(*pairs[-1],shared=shared)
        except AccountError as exc:assert exc.status==429
        else:raise AssertionError("Expected the configured rate limit")
    ensure_limited([("peer",f"u{i}") for i in range(21)])
    ensure_limited([(f"p{i}","member") for i in range(13)])
    ensure_limited([(f"p{i}",f"u{i}") for i in range(241)])
    ensure_limited([("frp",f"u{i}") for i in range(121)],shared=True)
    ensure_limited([("frp","fixed")]*7,shared=True)
    failures=PlayerCaptcha()
    for i in range(119):failures.failed("frp",f"u{i}")
    assert failures.login_challenge("frp","new") and not failures.login_challenge("frp","new",shared=True)
    failures.failed("frp","last")
    assert failures.login_challenge("frp","other",shared=True)
    with TemporaryDirectory() as tmp:
        state=Path(tmp);cert=certificates(state)
        identity=LocalIdentity(state/"identity.sqlite3")
        srv=create_profile_server(identity,port=0,allow_registration=True)
        worker=Thread(target=srv.serve_forever,daemon=True);worker.start()
        answers=Queue()
        def draw(value):
            answers.put(value)
            return picture(value)
        try:
            with patch("player_captcha.picture",side_effect=draw),gateway(srv.server_port,11033,cert,[],state) as port:
                ch=(PlayerTlsChannels(f"https://localhost:{port}",str(cert/"ca.pem"))
                    if cert else ClearChannels(port,18881,11033))
                def post(action,fields):
                    with ch.open(18881) as stream,closing(http.client.HTTPConnection("localhost",18881)) as conn:
                        conn.sock=stream
                        conn.request("POST","/local/v1/accounts/"+action,json.dumps(fields).encode(),{"Content-Type":"application/json"})
                        r=conn.getresponse()
                        return r.status,json.loads(r.read())
                def solve(user,purpose):
                    status,box=post("challenge",{"username":user,"purpose":purpose})
                    assert status==200 and set(box)=={"challenge_id","image_png","expires_in"}
                    img=b64decode(box["image_png"],validate=True)
                    assert img.startswith(b"\x89PNG\r\n\x1a\n") and len(img)<20000
                    return dict(challenge_id=box["challenge_id"],answer=answers.get(timeout=1))
                u="验证OwO";form={"username":u,"password":"independent-test-pass"}
                for name in (u,"unknown.test"):
                    assert post("lookup",{"username":name})==(428,{"error":"captcha_required"})
                assert post("register",form)==(428,{"error":"captcha_required"})
                assert post("bind",form)==(428,{"error":"captcha_required"})
                status,receipt=post("lookup",{"username":u,**solve("验证ＯｗＯ","lookup")})
                assert status==200 and receipt["exists"] is False
                proof=receipt["account_proof"]
                status,created=post("register",dict(form,account_proof=proof))
                assert status==201 and created.get("ticket")
                assert post("register",dict(form,account_proof=proof))==(400,{"error":"captcha_invalid"})
                assert post("register",form)==(428,{"error":"captcha_required"})
                assert post("lookup",{"username":u})==(428,{"error":"captcha_required"})
                status,receipt=post("lookup",{"username":u,**solve(u,"lookup")})
                assert status==200 and receipt["exists"] is True
                assert post("login",dict(form,account_proof=receipt["account_proof"]))[0]==200
                for name in (u,"unknown.test"):
                    for _ in range(3):
                        assert post("login",{"username":name,"password":"wrong-value"})==(401,{"error":"invalid_credentials"})
                    assert post("login",{"username":name,"password":"wrong-value"})==(428,{"error":"captcha_required"})
                assert post("login",dict(form,**solve(u,"login")))[0]==200
                assert post("login",form)[0]==200
        finally:
            srv.shutdown();srv.server_close();worker.join()
    transport="TLS" if cert else "signed loopback fixture (certificate generator unavailable)"
    print(f"PASS {transport}: account PNG, registration and lookup privacy, authenticated login, adaptive limits, expiry, owner/purpose binding and concurrent single-use checks.")


if __name__=="__main__":run()
