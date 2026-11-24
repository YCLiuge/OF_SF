"""Check diagnostic stage markers on real private HTTP and TCP sockets."""
import argparse
import json
import os
import socket
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from private_profiles import create_profile_server
from stage_diagnostics import StageDiagnostics, MAX_BYTES
from test_private_profiles import request
from test_game_realm import login, join
from test_native_gacha import call


def run(root, resources):
    with patch.dict(os.environ, {"OF_STAGE_DIAGNOSTICS": "0"}):
        off = StageDiagnostics(root / "disabled")
        off.http("/healthz")
        off.message(1001)
        assert not off.path.exists() and not off.previous.exists()
    with patch.dict(os.environ, {"OF_STAGE_DIAGNOSTICS": "1", "OF_QUICKSDK_VERIFY_URL": ""}):
        state = root / "active"
        state.mkdir()
        identity = LocalIdentity(state / "identity.sqlite3")
        profile = identity.create_profile("诊断测试角色")
        game = LocalGameService(identity, state, resources, port=0)
        store = game.stages
        game.start()
        try:
            with create_profile_server(identity, port=0) as http:
                http.stages = store
                thread = Thread(target=http.serve_forever, kwargs={"poll_interval": 0.02})
                thread.start()
                try:
                    status, body = request(http.server_port, "GET", "/healthz")
                    assert status == 200 and body["nativeBuild"] == "online-pc-1.1.12"
                    request(http.server_port, "GET", "/not-a-route/SECRET_PATH?secret=QUERY")
                    request(http.server_port, "POST", "/dispatch/client_hot_update?secret=QUERY", {"uid":"SECRET_BODY"})
                    request(http.server_port, "GET", "/Resources/SECRET_ASSET?secret=QUERY")
                finally:
                    http.shutdown()
                    thread.join()
            with socket.create_connection(("127.0.0.1",game.port),timeout=5) as sock:
                req = pb.VerifyLoginTokenReq(sdk_uid="SECRET_UID", login_token="SECRET_TOKEN")
                sock.sendall(encode(MsgId.VerifyLoginTokenReq, req.SerializeToString(), 99))
                _, data = receive(sock)
                assert pb.VerifyLoginTokenRsp.FromString(data).status == pb.StatusCode_FAIL
            conn, _, _ = login(game.port, identity.issue_ticket(*profile))
            with conn:
                join(conn)
                for phase in (23, 999999):
                    rsp=call(conn,"Tutorial",37,tutorial_id=phase,tutorial_name="SECRET_LABEL")
                    assert rsp.status == pb.StatusCode_BAD_REQ
                rsp=call(conn,"OperateMails",38,operate_type=pb.MailOperateType_READ,mail_id=4294967294)
                assert rsp.status == pb.StatusCode_MAIL_NOT_EXIST
        finally:
            game.stop()
        markers = list(map(json.loads, store.path.read_bytes().splitlines()))
        assert markers[:3] == [{"http":"health"},{"http":"hot_update"},{"http":"resources"}], markers
        assert {"verify":"rejected"} in markers and {"verify":"ok"} in markers
        assert {"msg_id":1001} in markers and {"msg_id":1003} in markers
        assert markers.count({"tcp":"open"}) == markers.count({"tcp":"close"}) == 2
        allowed = {"http":{"health","hot_update","resources"}, "msg_id":{1001,1003,MsgId.PlayerMainDataReq,MsgId.TutorialReq,MsgId.OperateMailsReq},
                   "verify":{"rejected","ok"}, "tcp":{"open","close"}, "close_reason":{"eof","verify_rejected","server_stop","transport"}}
        assert all(len(row)==1 and all(val in allowed[key] for key,val in row.items()) for row in markers)
        operations=[json.loads(line)["operation"] for line in store.operations.read_bytes().splitlines()]
        assert [(row["request"], row["modes"]) for row in operations] == [
            (MsgId.TutorialReq, {"tutorial_id":23}), (MsgId.TutorialReq, {"tutorial_id":"other"}),
            (MsgId.OperateMailsReq, {"operate_type":pb.MailOperateType_READ})]
        assert all(row["time"] > 0 for row in operations)
        assert all(secret not in store.operations.read_bytes() for secret in (b"SECRET", b"999999", b"4294967294"))
        # Unrecognized numbers cannot carry arbitrary data into the stage stream.
        store.message(2**30)
        assert json.loads(store.path.read_bytes().splitlines()[-1]) == {"tcp":"unknown_msg"}
        for _ in range(1600):
            store.message(1001)
        assert 0 < store.previous.stat().st_size <= MAX_BYTES and 0 < store.path.stat().st_size <= MAX_BYTES
        assert len(list(state.glob("stage-events*"))) == 2
    print("PASS static nativeBuild, diagnostics off by default, live HTTP + TCP stages, no identifiers, bounded rotation.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    with TemporaryDirectory() as temp:
        run(Path(temp), parser.parse_args().resources)
