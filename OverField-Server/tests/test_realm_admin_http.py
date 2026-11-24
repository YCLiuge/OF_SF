"""Check the operator transport against the actual supervisor and game HTTP listener."""
import argparse
from contextlib import ExitStack
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import realm_admin_http as api


def request(port, method, route, token=None, data=None, *, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    if host is not None:
        headers["Host"] = host
    body = json.dumps(data).encode() if data is not None and not isinstance(data, bytes) else data
    try:
        conn.request(method, route, body=body, headers=headers)
        reply = conn.getresponse()
        raw = reply.read()
        return reply.status, json.loads(raw) if reply.getheader("Content-Type", "").startswith("application/json") else None
    finally:
        conn.close()


def ports():
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(3)]
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]


def run(root):
    profile, game, owner = ports()
    token = secrets.token_urlsafe(32)
    env = {k: v for k, v in os.environ.items() if not k.startswith("OF_")}
    assert api.configuration((profile, game), {}) is None
    for port, key in [(game, token), (profile, token), (0, token), (65536, token), ("x", token),
                      (owner, ""), (owner, "short")]:
        try:
            api.configuration((profile, game), {"OF_ADMIN_PORT": str(port), "OF_ADMIN_TOKEN": key})
        except ValueError:
            pass
        else:
            raise AssertionError("Accepted invalid owner listener configuration")
    env.update(OF_HTTP_PORT=str(profile), OF_GAME_PORT=str(game), OF_ADMIN_PORT=str(owner), OF_ADMIN_TOKEN=token)
    state = root / "realm"

    def call(method, name, data=None):
        return request(owner, method, api.OPERATIONS + name, token, data)

    with tempfile.TemporaryFile() as log:
        worker = subprocess.Popen([sys.executable, "-B", str(ROOT / "of.py"), "--state", str(state), "start"],
                                  env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if worker.poll() is not None:
                    log.seek(0)
                    raise AssertionError(log.read().decode(errors="replace"))
                try:
                    status, body = call("GET", "realm.status")
                    assert status == 200 and body["result"]["lifecycle"] == "running"
                    break
                except (ConnectionRefusedError, TimeoutError):
                    time.sleep(0.1)
            else:
                raise AssertionError("Operator listener did not start")
            spec_path = api.PREFIX + "/openapi.json"
            for bad_token in (None, "wrong-token"):
                assert request(owner, "GET", spec_path, bad_token)[0] == 401
                assert request(owner, "POST", api.OPERATIONS + "notice.publish", bad_token,
                               {"title": "Denied", "body": "test"})[0] == 401
            status, body = request(owner, "GET", spec_path + "?token=" + token)
            assert status == 401 and token not in json.dumps(body)
            status, body = request(owner, "GET", spec_path + "?token=" + token, token)
            assert status == 404 and token not in json.dumps(body)
            status, body = request(owner, "UNKNOWN" + token, spec_path)
            assert status == 501 and token not in json.dumps(body)
            assert request(owner, "GET", spec_path, token, host="other.test")[0] == 400
            status, schema = request(owner, "GET", api.PREFIX + "/schema", token)
            assert status == 200
            status, document = request(owner, "GET", spec_path, token)
            assert status == 200 and document["openapi"] == "3.1.0"
            assert document["security"] == [{"ownerToken": []}]
            for name, spec in schema["result"]["operations"].items():
                route = document["paths"][api.OPERATIONS + name]
                method = "get" if spec["readOnly"] else "post"
                assert set(route) == {method}
                if method == "post":
                    payload = route[method]["requestBody"]["content"]["application/json"]
                    assert payload["schema"] == spec["inputSchema"]
            assert request(profile, "POST", api.OPERATIONS + "notice.publish", token,
                           {"title": "Public port", "body": "test"})[0] >= 400
            assert call("GET", "maintenance.list")[1]["result"]["notices"] == []
            assert call("GET", "photos.list?before=99&limit=1")[0] == 200
            assert call("GET", "photos.list?limit=0")[0] == 400
            assert call("GET", "photos.list?limit=1&limit=2")[0] == 400
            assert {v["name"] for v in document["paths"][api.OPERATIONS+"photos.list"]["get"]["parameters"]} == {"before","limit"}
            assert call("GET", "notice.publish")[0] == 405
            assert call("GET", "missing")[0] == 404
            assert call("POST", "notice.publish", b'{"title":"a","title":"b","body":"c"}')[0] == 400
            assert call("POST", "notice.publish", b"x" * 65537)[0] == 413
            for path,kind,text in (("/admin/","text/html","OF_SF"),("/admin/console.js","text/javascript","/admin/v1/schema"),
                                   ("/admin/console.css","text/css","#workspace")):
                conn=http.client.HTTPConnection("127.0.0.1",owner,timeout=3)
                try:
                    conn.request("GET",path)
                    doc=conn.getresponse()
                    raw=doc.read().decode("utf-8")
                    assert doc.status==200 and doc.getheader("Content-Type").startswith(kind) and text in raw
                    assert "frame-ancestors 'none'" in doc.getheader("Content-Security-Policy")
                    assert token not in raw
                finally:
                    conn.close()
            assert request(owner,"POST","/admin/")[0]==405
            assert request(owner,"GET","/admin/v1/schema")[0]==401
            assert call("POST", "rules.set", {"world_max": 50})[1]["error"]["code"] == "state_busy"
            for path in ["//server/share/poster.png", "file://server/pkg.png", r"\\.\pipe\test"]:
                assert call("POST", "notice.publish", {"title": "x", "body": "x", "images": [path]})[0] == 400
            archive = root / "unexpected.zip"
            assert call("POST", "state.backup", {"archive": str(archive)})[0] == 409 and not archive.exists()
            assert call("POST", "state.restore", {"archive": "//server/share/a.zip"})[0] == 409
            issued = call("POST", "notice.publish", {"title": "Notice", "body": "Test"})
            assert issued[0] == 200 and type(issued[1]["result"]["id"]) is int
            assert call("GET", "maintenance.list")[1]["result"]["notices"][0]["title"] == "Notice"
            assert call("POST", "realm.stop", {})[1]["result"] == {"requested": True}
            assert worker.wait(timeout=15) == 0
        finally:
            if worker.poll() is None:
                worker.terminate()
                worker.wait(timeout=15)
        log.seek(0)
        assert token.encode() not in log.read()
    assert not state.with_name(".realm.active").exists()
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", owner)) != 0
    with api.AdminServer(("127.0.0.1", owner), api.Handler):
        # An occupied owner port must also release the game/profile listeners and realm lock.
        attempted = subprocess.run([sys.executable, "-B", str(ROOT / "of.py"), "--state", str(state), "start"],
                                   env=env, cwd=ROOT, capture_output=True, timeout=20)
        assert attempted.returncode != 0 and b"Server ready" not in attempted.stdout
        assert not state.with_name(".realm.active").exists()
        for port in (profile, game):
            with socket.socket() as probe:
                probe.settimeout(1)
                assert probe.connect_ex(("127.0.0.1", port)) != 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources")
    parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("PASS loopback owner authentication, OpenAPI, mutations, online locks and shutdown.")
