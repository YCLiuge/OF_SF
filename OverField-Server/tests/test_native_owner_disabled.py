"""The three disabled operator services discard submitted data and preserve the native failure reply."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from test_game_realm import join, login
from test_native_gacha import call
from unsupported_requests import OWNER_DISABLED


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profile = identity.create_profile("区服设置测试")
    service = LocalGameService(identity, root, resources, port=0)
    marker = "OF-SF-SYNTHETIC-DISCARD-ONLY"
    examples = {
        "GetExpressAddress": {},
        "UploadExpressAddress": {"address": marker},
        "ClientLogAuth": {"device_id": marker, "log_server_token": marker},
        "ClientLogMessage": {"logs": [{"log_template": marker}]},
    }
    service.start()
    try:
        assert OWNER_DISABLED == {getattr(MsgId, name+"Req") for name in examples}
        assert OWNER_DISABLED.isdisjoint(service.handlers)
        sock, _, _ = login(service.port, identity.issue_ticket(*profile))
        with sock:
            join(sock)
            for seq, (name, fields) in enumerate(examples.items(), 120):
                reply = call(sock, name, seq, **fields)
                assert [(f.name, value) for f, value in reply.ListFields()] == [("status", pb.StatusCode_FAIL)]
            assert call(sock, "PlayerPing", 130, client_time_ms=441).client_time_ms == 441
    finally:
        service.stop()
    for path in root.rglob("*"):
        if path.is_file():
            assert marker.encode() not in path.read_bytes(), path.name
    print("PASS four owner-disabled native requests discarded synthetic values, returned original status only and kept gameplay online.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    with TemporaryDirectory() as state:
        run(Path(state), parser.parse_args().resources)
