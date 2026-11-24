"""Loopback native packets and isolated synthetic SDK verification; makes no external requests."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
from unittest.mock import patch, MagicMock

from online_identity import OnlineIdentity, bind, _validate_issuer, VERIFIER_URL, ISSUER
from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode,receive
from local_identity import LocalIdentity
from test_game_realm import join

def refused(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError("Unexpected identity acceptance")

def attempt(port, uid, token, *, expect=False):
    with socket.create_connection(("127.0.0.1",port),timeout=8) as c:
        req=pb.VerifyLoginTokenReq(sdk_uid=uid,login_token=token,account_type=1)
        c.sendall(encode(MsgId.VerifyLoginTokenReq,req.SerializeToString(),1))
        h,b=receive(c);assert h.msg_id==MsgId.VerifyLoginTokenRsp
        response=pb.VerifyLoginTokenRsp.FromString(b)
        if not expect:
            assert response.status!=pb.StatusCode_OK
            return None
        assert response.sdk_uid==uid and response.account_type==1 and response.status==pb.StatusCode_OK
        c.sendall(encode(MsgId.PlayerLoginReq,pb.PlayerLoginReq().SerializeToString(),2))
        h,b=receive(c);assert h.msg_id==MsgId.PlayerLoginRsp
        login=pb.PlayerLoginRsp.FromString(b);assert login.status==pb.StatusCode_OK
        main,scene=join(c)
        assert login.player_name==main.player_name and main.scene_id==scene.data.scene_id==100
        return response.user_id,login.player_name

def tls_contract():
    for status,doc,ok in [
        (200,{'status':True,'data':{'uid':'19372'}},True),
        (200,{'status':True,'data':{'uid':19372}},True),
        (200,{'status':True,'data':{'uid':'other'}},False),
        (200,{'status':'true','data':{'uid':19372}},False),
        (200,{'status':True},False),(200,{'status':False},False),
        (301,{'status':True,'data':{'uid':'19372'}},False),
        (200,None,False)]:
        with patch("online_identity.http.client.HTTPSConnection") as make:
            conn=make.return_value;r=conn.getresponse.return_value
            r.status=status;r.read.return_value=b'not json' if doc is None else json.dumps(doc).encode()
            assert _validate_issuer('19372','synthetic-value-&') is ok
            assert make.call_args.args==(ISSUER,443)
            ssl_context=make.call_args.kwargs['context']
            assert ssl_context.check_hostname and ssl_context.keylog_filename is None
            args=conn.request.call_args
            assert args.args==('POST','/webapi/checkUserInfo') and b'token=synthetic-value-%26' in args.kwargs['body']
            conn.close.assert_called_once()

def run(resources):
    tls_contract()
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);profile=root/'profile.json';identity=LocalIdentity(root/'identity.sqlite3')
        slot,key=identity.create_profile("授权玩家")
        profile.write_text(json.dumps(dict(realm="overfield-local-v1",slot=slot,profile_key=key)),'utf8')
        clean={'OF_QUICKSDK_VERIFY_URL':VERIFIER_URL,'OF_QUICKSDK_CREATE_PLAYERS':'0','OF_QUICKSDK_VPN_CIDR':''}
        with patch.dict(os.environ,clean),patch('online_identity._validate_issuer',side_effect=lambda u,t:t=='synthetic-'+u) as verify:
            with patch.dict(os.environ,{'OF_QUICKSDK_VERIFY_URL':''}):
                assert OnlineIdentity.configured(identity,'0.0.0.0') is None
            for url in ('http://sdkapi-of.inutan.com/webapi/checkUserInfo','https://example.org/webapi/checkUserInfo'):
                with patch.dict(os.environ,{'OF_QUICKSDK_VERIFY_URL':url}):
                    refused(lambda:OnlineIdentity.configured(identity,'127.0.0.1'))
            refused(lambda:OnlineIdentity.configured(identity,'0.0.0.0'))
            service=LocalGameService(identity,root,resources,port=0);service.start()
            try:
                attempt(service.port,"19372","synthetic-19372");verify.assert_not_called()
                bind(identity,profile,'19372');bind(identity,profile,'19372')
                refused(lambda:bind(identity,profile,'other-binding'))
                attempt(service.port,"19372","incorrect")
                known=attempt(service.port,"19372","synthetic-19372",expect=True)
                assert known[1]=='授权玩家'
                bind(identity,profile,'19372',remove=True)
                attempt(service.port,"19372","synthetic-19372")
                bind(identity,profile,'19372')
            finally:service.stop()
            with patch.dict(os.environ,{'OF_QUICKSDK_CREATE_PLAYERS':'1'}):
                svc=LocalGameService(identity,root,resources,port=0);svc.start()
                try:
                    assert attempt(svc.port,"19372","synthetic-19372",expect=True)==known
                    attempt(svc.port,"new-user","incorrect")
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        results=list(pool.map(lambda _:svc.online_identity.authenticate("new-user","synthetic-new-user","127.0.0.1"),range(5)))
                    assert len(set(results))==1
                    ok=attempt(svc.port,"new-user","synthetic-new-user",expect=True)
                    assert ok[0]!=known[0] and ok[1].startswith('队长')
                    refused(lambda:svc.online_identity.authenticate("new-user","synthetic-new-user","172.16.5.2"))
                finally:svc.stop()
            with patch.dict(os.environ,{'OF_QUICKSDK_VPN_CIDR':'10.90.0.0/24'}):
                refused(lambda:OnlineIdentity.configured(identity,'10.90.0.1'))
                loopback=OnlineIdentity.configured(identity,'127.0.0.1')
                refused(lambda:loopback.authenticate("19372","synthetic-19372","10.90.0.8"))
            with closing(sqlite3.connect(identity.path)) as db:
                assert db.execute("SELECT count(*) FROM online_bindings").fetchone()==(2,)
            for file in (root/'identity.sqlite3',root/'game.sqlite'):
                raw=file.read_bytes()
                assert b'synthetic-' not in raw and b'new-user' not in raw
    print("PASS native 1001/1003/1005 flow, dedicated HTTPS verifier, default-deny, optional creates, isolation and restart (synthetic issuer).")

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--resources',required=True,type=Path)
    run(parser.parse_args().resources)
