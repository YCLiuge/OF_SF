"""Exercise the public CLI as an external tool, without touching an operator's live realm."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def invoke(state, *arguments, data=None, raw=None, expected=True):
    cmd = [sys.executable, "-B", str(ROOT / "of.py"), "--state", str(state), "admin", *arguments]
    payload = raw if raw is not None else json.dumps(data or {}, ensure_ascii=False).encode("utf-8")
    process = subprocess.run(cmd, input=payload, capture_output=True, timeout=25, cwd=ROOT)
    assert process.stdout.count(b"\n") == 1, (process.stdout, process.stderr)
    message = json.loads(process.stdout)
    assert message["apiVersion"] == "overfield.admin/v1"
    assert message["ok"] == expected and (process.returncode == 0) == expected, message
    return message["result" if expected else "error"]


def run(root):
    state = root / "server data"
    def call(op, **data):
        return invoke(state, "call", op, data=data)
    schema = invoke(state, "schema")
    assert len(schema["operations"]) >= 10 and schema["operations"]["notice.edit"]["inputSchema"]["required"] == ["id"]
    assert invoke(state, "status")["lifecycle"] == "uninitialized"
    assert call("maintenance.list")["notices"] == [] and call("rules.get")["uid_start"] == 1000001
    assert not state.exists()
    assert invoke(state, "call", "invalid", expected=False)["code"] == "unknown_operation"
    for bad in (b'{"world_max":10,"world_max":11}', b'{"noSuchOption":1}', b'[]', b'{}', b'{"world_max":true}', b" " * 66000):
        assert invoke(state, "call", "rules.set", raw=bad, expected=False)["code"] == "invalid_request"
    assert not state.exists()
    param = root / "参数.json"
    param.write_text('{"world_max":197}', encoding="utf-8-sig")
    assert invoke(state, "call", "rules.set", "--input", str(param))["world_max"] == 197
    assert invoke(state, "status")["lifecycle"] == "stopped"
    assert invoke(state, "call", "rules.set", data={"world_min":199}, expected=False)["code"] == "invalid_request"
    assert call("rules.get")["world_min"] == 1
    poster = root / "海报.png"
    poster.write_bytes(bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
                                    "0000000c49444154789c63606060000000040001f61738550000000049454e44ae426082"))
    notice = call("notice.publish", title="开服", body="维护完毕", images=[str(poster)])["id"]
    assert call("notice.edit", id=notice, title="维护公告")["id"] == notice
    edited = call("maintenance.list")["notices"][0]
    assert edited["title"] == "维护公告" and edited["images"][0]["sha256"] == hashlib.sha256(poster.read_bytes()).hexdigest()
    call("notice.edit", id=notice, images=[])
    assert not call("maintenance.list")["notices"][0].get("images")
    call("notice.withdraw", id=notice)
    assert not call("maintenance.list")["notices"]
    package = root / "client.zip"
    package.write_bytes(b"release-package-test")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    assert call("release.publish", file=str(package), sha256=digest, version="1.2.3", notes="测试")["sha256"] == digest
    call("release.withdraw")
    assert call("maintenance.list")["release"] is None

    with ExitStack() as reserved:
        sockets = [reserved.enter_context(socket.socket()) for _ in range(2)]
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        http_port, game_port = [str(sock.getsockname()[1]) for sock in sockets]
    env = {k: v for k, v in os.environ.items() if not k.startswith("OF_")}
    env.update(OF_HTTP_PORT=http_port, OF_GAME_PORT=game_port)
    with tempfile.TemporaryFile() as log:
        worker = subprocess.Popen([sys.executable, "-B", str(ROOT / "of.py"), "--state", str(state), "start"],
                                  env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if worker.poll() is not None:
                    log.seek(0)
                    raise AssertionError(log.read().decode(errors="replace"))
                if invoke(state, "status")["lifecycle"] == "running":
                    break
                time.sleep(0.15)
            else:
                raise AssertionError("Temporary realm did not start")
            assert invoke(state, "call", "rules.set", data={"world_max":33}, expected=False)["code"] == "state_busy"
            assert call("rules.get")["world_max"] == 197
            assert call("notice.publish", title="在线发布", body="一")["id"] > notice
            assert isinstance(call("realm.status")["accounts"], int)
            assert call("realm.stop")["requested"]
            assert worker.wait(timeout=15) == 0
        finally:
            if worker.poll() is None:
                worker.terminate()
                worker.wait(timeout=15)
    snapshot = root / "realm.zip"
    assert Path(call("state.backup", archive=str(snapshot))["archive"]).samefile(snapshot)
    assert invoke(state, "call", "state.backup", data={"archive":str(snapshot)}, expected=False)["code"] == "already_exists"
    restored = root / "restored"
    assert Path(invoke(restored, "call", "state.restore", data={"archive":str(snapshot)})["state"]).samefile(restored)
    assert invoke(restored, "call", "rules.get")["world_max"] == 197
    assert invoke(restored, "call", "maintenance.list")["notices"][0]["title"] == "在线发布"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources")
    parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("PASS administration discovery, validation, publication, process lifecycle and portable backups.")

