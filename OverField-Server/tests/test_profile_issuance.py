"""Recover an existing played character from a legacy backup and issue independent credentials."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from urllib.request import build_opener, ProxyHandler, Request

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode,receive
from local_identity import LocalIdentity, InvalidProfile
import migrate
from online_identity import VERIFIER_URL
from private_profiles import create_profile_server
from profile_issuance import issue_profile
from state_lock import exclusive_state, StateBusy
from test_game_realm import join,login,next_msg

def native_sdk_scene(game,token):
    s=socket.create_connection(("127.0.0.1",game.port),timeout=8)
    req=pb.VerifyLoginTokenReq(sdk_uid="SYNTHETIC-LEGACY-PLAYER",login_token=token)
    s.sendall(encode(MsgId.VerifyLoginTokenReq,req.SerializeToString(),1))
    h,b=receive(s);assert h.msg_id==MsgId.VerifyLoginTokenRsp
    ver=pb.VerifyLoginTokenRsp.FromString(b);assert ver.status==pb.StatusCode_OK
    s.sendall(encode(MsgId.PlayerLoginReq,b"",2))
    logininfo=next_msg(s,MsgId.PlayerLoginRsp,pb.PlayerLoginRsp)
    assert logininfo.status==pb.StatusCode_OK
    join(s)
    return s,ver.user_id,logininfo.player_name

def run(resources):
    kit=Path(__file__).resolve().parents[1]
    with TemporaryDirectory() as td:
        work=Path(td)
        before=work/"source";before.mkdir()
        ident=LocalIdentity(before/"identity.sqlite3")
        friend,original=ident.create_profile("独立档案")
        with patch.dict(os.environ,{"OF_QUICKSDK_VERIFY_URL":VERIFIER_URL,"OF_QUICKSDK_CREATE_PLAYERS":"1"}),patch("online_identity._validate_issuer",return_value=True):
            game=LocalGameService(ident,before,resources,port=0);game.start()
            try:
                s,expected_id,expected_name=native_sdk_scene(game,"fictional-session")
                with s:
                    req=pb.SetArchiveInfoReq(key="migration-progress",value="earned-on-source")
                    s.sendall(encode(MsgId.SetArchiveInfoReq,req.SerializeToString(),4))
                    assert next_msg(s,MsgId.SetArchiveInfoRsp,pb.SetArchiveInfoRsp).status==pb.StatusCode_OK
                s,_,_=login(game.port,ident.issue_ticket(friend,original))
                with s:join(s)
            finally:game.stop()
        with closing(sqlite3.connect(ident.path)) as db:
            assert not db.execute("SELECT 1 FROM sqlite_schema WHERE name='issued_game_keys'").fetchone()
        # Some existing game saves have a display name different from the identity registration name.
        expected_name="原有游戏角色"
        with closing(sqlite3.connect(before/"game.sqlite")) as db:
            db.execute("UPDATE players SET player_name=? WHERE player_id=?",(expected_name,expected_id))
            db.commit()
        archive=work/"old.zip"
        migrate.backup(before,archive)
        restored=work/"restored"
        migrate.restore(archive,restored)
        profile=work/"private-account.json"
        # Test the actual admin entry point on a stopped, restored realm.
        cmd=[sys.executable,"-B",str(kit/"of.py"),"--state",str(restored),"issue-profile"]
        subprocess.run([*cmd,"--out",str(profile)],check=True)
        raw=profile.read_bytes()
        made=json.loads(raw)
        assert made["nickname"]==expected_name and b"SYNTHETIC" not in raw
        if os.name!="nt":assert stat.S_IMODE(profile.stat().st_mode)==0o600
        else:
            acl=subprocess.check_output(["icacls",str(profile)])
            assert b"(I)" not in acl
        credential=json.loads(raw)
        restored_id=LocalIdentity(restored/"identity.sqlite3")
        other=work/"second.json"
        issue_profile(restored,other,nickname=expected_name)
        second=json.loads(other.read_bytes())
        assert second["slot"]==credential["slot"] and second["profile_key"]!=credential["profile_key"]
        old=restored_id.issue_ticket(friend,original)
        for data in (credential,second,credential):
            assert restored_id.resolve_ticket(restored_id.issue_ticket(data["slot"],data["profile_key"]))==data["slot"]
        assert restored_id.resolve_ticket(old)==friend
        try:restored_id.issue_ticket(friend,credential["profile_key"])
        except InvalidProfile:pass
        else:raise AssertionError("New key worked for another character")
        try:issue_profile(restored,profile,nickname="独立档案")
        except FileExistsError:pass
        else:raise AssertionError("Existing profile was overwritten")
        assert profile.read_bytes()==raw
        with exclusive_state(restored):
            try:issue_profile(restored,work/"on-live-should-refuse.json",nickname=expected_name)
            except StateBusy:pass
            else:raise AssertionError("Live export accepted")
        occupied=work/"concurrent.json"
        def concurrent(_):
            try:issue_profile(restored,occupied,slot=credential["slot"])
            except (FileExistsError,StateBusy):return False
            return True
        with ThreadPoolExecutor(max_workers=3) as pool:
            assert sum(pool.map(concurrent,range(3)))==1
        with restored_id._write() as db:
            db.execute("INSERT INTO online_bindings(subject_hash,slot) VALUES(?,?)",(bytes(32),friend))
        ambiguous=work/"ambiguous.json"
        try:issue_profile(restored,ambiguous)
        except ValueError:pass
        else:raise AssertionError("Multiple linked played characters require an explicit selector")
        assert not ambiguous.exists()
        game=LocalGameService(restored_id,restored,resources,port=0);game.start()
        try:
            with create_profile_server(restored_id,port=0,game_port=game.port,allow_registration=False) as http:
                t=Thread(target=http.serve_forever,daemon=True);t.start()
                try:
                    params=json.dumps({k:credential[k] for k in ("slot","profile_key")}).encode()
                    req=Request(f"http://127.0.0.1:{http.server_port}/local/v1/sessions",data=params,headers={"Content-Type":"application/json"})
                    with build_opener(ProxyHandler({})).open(req,timeout=5) as resp:ticket=json.load(resp)["ticket"]
                    s,info,pid=login(game.port,ticket)
                    with s:
                        main,_=join(s)
                        assert pid==expected_id and info.player_name==main.player_name==expected_name
                        pref=pb.GetArchiveInfoReq(key="migration-progress")
                        s.sendall(encode(MsgId.GetArchiveInfoReq,pref.SerializeToString(),7))
                        assert next_msg(s,MsgId.GetArchiveInfoRsp,pb.GetArchiveInfoRsp).value=="earned-on-source"
                finally:http.shutdown();t.join()
        finally:game.stop()
        after=work/"with-new-credentials.zip";migrate.backup(restored,after)
        next_state=work/"second-machine";migrate.restore(after,next_state)
        new_id=LocalIdentity(next_state/"identity.sqlite3")
        assert new_id.resolve_ticket(new_id.issue_ticket(credential["slot"],credential["profile_key"]))==credential["slot"]
        assert new_id.resolve_ticket(new_id.issue_ticket(friend,original))==friend
    print("PASS restored existing auto-created character, multiple independent keys, private output, no replacement, concurrency, native game progress and subsequent backup.")

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--resources",type=Path,required=True)
    run(parser.parse_args().resources)
