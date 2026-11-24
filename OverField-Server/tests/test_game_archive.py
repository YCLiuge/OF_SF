"""Real loopback TCP checks for realm-local preferences on fresh and pre-update databases."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from test_game_realm import join, login


def request(client, msg_id, response_id, payload, cls, seq):
    client.sendall(encode(msg_id, payload.SerializeToString(), seq))
    for _ in range(192):
        header, data = receive(client)
        if header.msg_id == response_id:
            assert header.packet_id == seq
            return cls.FromString(data)
    raise AssertionError("Archive command failed to answer")


def get(client, key):
    return request(client, MsgId.GetArchiveInfoReq, MsgId.GetArchiveInfoRsp,
                   pb.GetArchiveInfoReq(key=key), pb.GetArchiveInfoRsp, 30)


def put(client, key, value):
    return request(client, MsgId.SetArchiveInfoReq, MsgId.SetArchiveInfoRsp,
                   pb.SetArchiveInfoReq(key=key, value=value), pb.SetArchiveInfoRsp, 31)


def run(root, resources):
    ident = LocalIdentity(root / "identity.sqlite3")
    first = ident.create_profile("甲队长")
    second = ident.create_profile("乙队长")
    server = LocalGameService(ident, root, resources, port=0)

    def enter(profile):
        client, player, _ = login(server.port, ident.issue_ticket(*profile))
        assert player.scene_id == 100
        join(client)
        return client

    # An existing 1.1.12 player can already have a game database, scene and name when upgraded.
    server.start()
    try:
        with enter(first):
            pass
    finally:
        server.stop()
    with closing(sqlite3.connect(root / "game.sqlite")) as disk:
        assert disk.execute("SELECT count(*) FROM players").fetchone()[0] > 0
        assert disk.execute("SELECT count(*) FROM sqlite_master WHERE name='private_archive'").fetchone()[0] == 0

    server.start()
    try:
        with enter(first) as a, enter(second) as b:
            assert get(a, "BlessingTreeTutotal").value == "1"
            assert get(a, "ButtonModeState").value == ""
            stored = put(a, "ButtonModeState", "手动")
            assert (stored.status, stored.key, stored.value) == (pb.StatusCode_OK, "ButtonModeState", "手动")
            assert get(a, "ButtonModeState").value == "手动"
            assert get(b, "ButtonModeState").value == ""
            assert put(b, "ButtonModeState", "自动").status == pb.StatusCode_OK
            assert put(a, "BlessingTreeTutotal", "").status == pb.StatusCode_OK
            assert get(a, "BlessingTreeTutotal").value == ""
            assert put(a, "ButtonModeState", "a" * 32769).status == pb.StatusCode_FAIL
            assert get(a, "ButtonModeState").value == "手动"
    finally:
        server.stop()

    server.start()
    try:
        with enter(first) as a, enter(second) as b:
            assert get(a, "ButtonModeState").value == "手动"
            assert get(b, "ButtonModeState").value == "自动"
            assert get(a, "BlessingTreeTutotal").value == ""
            assert get(b, "BlessingTreeTutotal").value == "1"
    finally:
        server.stop()
    # In the native client a verified reconnect can ask for archive settings before PlayerLogin.
    import socket
    server.start()
    try:
        for profile, expected in ((first, "手动"), (second, "自动")):
            with socket.create_connection(("127.0.0.1",server.port),timeout=5) as c:
                token = ident.issue_ticket(*profile)
                verify = pb.VerifyLoginTokenReq(login_token=token)
                c.sendall(encode(MsgId.VerifyLoginTokenReq, verify.SerializeToString(),1))
                h,b=receive(c)
                assert h.msg_id==MsgId.VerifyLoginTokenRsp and pb.VerifyLoginTokenRsp.FromString(b).status==pb.StatusCode_OK
                assert get(c,"ButtonModeState").value==expected
                assert get(c,"unused-key").value==""
                c.sendall(encode(MsgId.PlayerLoginReq,b"",2))
                h,b=receive(c)
                assert h.msg_id==MsgId.PlayerLoginRsp and pb.PlayerLoginRsp.FromString(b).status==pb.StatusCode_OK
                main,_=join(c)
                assert main.player_name
        with socket.create_connection(("127.0.0.1",server.port),timeout=5) as c:
            verify=pb.VerifyLoginTokenReq(login_token=ident.issue_ticket(*first))
            c.sendall(encode(MsgId.VerifyLoginTokenReq,verify.SerializeToString(),1))
            assert receive(c)[0].msg_id==MsgId.VerifyLoginTokenRsp
            c.sendall(encode(MsgId.SetArchiveInfoReq,pb.SetArchiveInfoReq(key="ButtonModeState",value="bad").SerializeToString(),2))
            assert not c.recv(1)
        with enter(first) as a:
            assert get(a,"ButtonModeState").value=="手动"
    finally:
        server.stop()
    print("PASS private archive read/write, post-verify pre-login read-only access, reconnect and isolation.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as temp:
        run(Path(temp), args.resources)
