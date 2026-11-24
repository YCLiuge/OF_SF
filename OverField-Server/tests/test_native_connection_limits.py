"""Verify that the configured TCP cap admits native sessions, rejects excess and recycles slots."""
import argparse
from contextlib import ExitStack, closing
import json
from pathlib import Path
import socket
import sqlite3
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode
from local_identity import LocalIdentity
from realm_rules import configure, load
from test_game_realm import login, next_msg


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    assert load(identity).game_connections == 24
    with identity._write() as db:
        db.execute("INSERT OR REPLACE INTO realm_rules VALUES(1,?)", (json.dumps({"world_max": 80}),))
    assert load(identity).game_connections == 24
    for limit in (0, 513):
        try:
            configure(root, {"game_connections": limit})
        except ValueError:
            continue
        raise AssertionError("Invalid connection limit accepted")
    updated = configure(root, {"game_connections": 2})
    assert (updated.game_connections, updated.world_max) == (2, 80)
    profiles = [identity.create_profile(f"Slot{i}") for i in range(3)]
    host = LocalGameService(identity, root, resources, port=0)

    def connect(i):
        return login(host.port, identity.issue_ticket(*profiles[i]))

    def ping(sock, value):
        req = pb.PlayerPingReq(client_time_ms=value)
        sock.sendall(encode(MsgId.PlayerPingReq, req.SerializeToString(), 4))
        rsp = next_msg(sock, MsgId.PlayerPingRsp, pb.PlayerPingRsp)
        assert rsp.status == pb.StatusCode_OK and rsp.client_time_ms == value

    host.start()
    try:
        with ExitStack() as clients:
            first, _, _ = connect(0)
            clients.enter_context(first)
            second, _, _ = connect(1)
            clients.enter_context(second)
            ping(first, 1)
            ping(second, 2)
            with socket.create_connection(("127.0.0.1",host.port),timeout=4) as excess:
                try:
                    assert excess.recv(1) == b""
                except ConnectionResetError:
                    pass
            from test_game_realm import join_after_intro
            from test_native_gacha import call
            join_after_intro(first)
            logout = call(first, "PlayerOffline", 20)
            assert logout.status == pb.StatusCode_OK and logout.reason == pb.PlayerOfflineReason_NONE
            assert first.recv(1) == b""
            first.close()
            assert host.slots.acquire(timeout=4)
            host.slots.release()
            third, _, _ = connect(2)
            clients.enter_context(third)
            ping(third, 3)
            ping(second, 4)
    finally:
        host.stop()
    assert not host.connections
    with closing(sqlite3.connect(root / "game.sqlite")) as saved:
        assert saved.execute("SELECT COUNT(*) FROM private_slots").fetchone() == (3,)
    print("PASS native connection quota, default/legacy rules, over-cap refusal, immediate slot reuse and intact remaining sessions.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp), parser.parse_args().resources)
