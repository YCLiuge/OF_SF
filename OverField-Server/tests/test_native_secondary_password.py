"""Exercise the native realm secondary password and its per-role, per-session boundaries."""
import argparse
from contextlib import closing
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_secondary_password import MAX_ERRORS, state
from test_game_realm import join, login
from test_native_events import rpc
from utils import db


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profiles = [identity.create_profile(name) for name in ("安全测试甲", "安全测试乙")]
    service = LocalGameService(identity, root, resources, port=0)
    sequence = count(40)
    opened = pb.SPOperateType_Open
    changed = pb.SPOperateType_Change
    checked = pb.SPOperateType_Check
    closed = pb.SPOperateType_Close
    ok, bad, fail = pb.StatusCode_OK, pb.StatusCode_BAD_REQ, pb.StatusCode_FAIL

    def connect(index):
        client, _, uid = login(service.port, identity.issue_ticket(*profiles[index]))
        main, _ = join(client)
        return client, uid, (main.sp_open_status, main.sp_check_status)

    def operate(client, kind, pin, *, old="", seq=None):
        result, _ = rpc(client, "SPOperate", next(sequence) if seq is None else seq,
                        operate_type=kind, password=pin, old_password=old)
        assert result.operate_type == kind
        return result.status, result.sp_open_status, result.sp_check_status

    first, second = "012345", "043219"
    service.start()
    try:
        a, uid, initial = connect(0)
        b, foreign, other_initial = connect(1)
        with closing(a), closing(b):
            assert initial == other_initial == (False, False)
            for pin in ("", "12345", "1234567", "３４５６７８", "abcdef"):
                assert operate(a, opened, pin) == (bad, False, False)
            assert operate(a, checked, first) == (fail, False, False)
            assert operate(a, opened, first, seq=201) == (ok, True, True)
            with service.lock:
                salt, verifier, errors, blocked = state(uid)
                assert (len(salt), len(verifier), errors, blocked) == (16, 32, 0, 0)
                assert first.encode() not in (salt, verifier) and state(foreign) is None
            assert operate(a, opened, second) == (fail, True, True)
            assert operate(a, checked, second, seq=202) == (fail, True, False)
            assert operate(a, checked, second, seq=202) == (fail, True, False)
            with service.lock:
                assert state(uid)[2] == 1
            assert operate(a, checked, first) == (ok, True, True)
            assert operate(a, changed, second, old=first) == (ok, True, True)
            with service.lock:
                assert state(uid)[:2] != (salt, verifier)
            assert operate(b, checked, first) == (fail, False, False)
            assert operate(b, opened, first) == (ok, True, True)
            with service.lock:
                assert state(foreign)[1] != verifier
    finally:
        service.stop()
    service.start()
    try:
        a, _, restored = connect(0)
        with closing(a):
            assert restored == (True, False)
            for _ in range(MAX_ERRORS):
                assert operate(a, checked, first) == (fail, True, False)
            # Same packet can retry when the temporary cooldown expires.
            assert operate(a, checked, second, seq=301) == (fail, True, False)
            with service.lock, db.conn:
                assert state(uid)[3] > 0
                db.conn.execute("UPDATE private_secondary_password SET blocked_until=0 WHERE player_id=?", (uid,))
            assert operate(a, checked, second, seq=301) == (ok, True, True)
            assert operate(a, closed, first) == (fail, True, False)
            assert operate(a, checked, second, seq=301) == (ok, True, False)
            assert operate(a, closed, second) == (ok, False, False)
            assert operate(a, opened, first, seq=302) == (ok, True, True)
            assert operate(a, closed, first) == (ok, False, False)
            assert operate(a, opened, first, seq=302) == (ok, False, False)
            with service.lock:
                assert state(uid) is None and state(foreign)
    finally:
        service.stop()
    print("PASS native secondary password open/change/check/close, ASCII PIN validation, isolated salts, blocked guesses, replay and reconnect flags.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory() as folder:
        run(Path(folder), args.resources)
