"""Run the peer transport on three real processes with disposable mTLS identities."""
import json
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from federation_topology import Topology
from federation_world import MAX_BODY, TIMEOUT, World, WorldError, read_frame, send_frame


def make_configs(root, names=("a", "b", "c"), scenes=None, uid_ranges=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if scenes is None:
        scenes = {101 + i: name for i, name in enumerate(names)}
    (root / "req.cnf").write_text("[req]\ndistinguished_name=dn\n[dn]\nCN=OF world\n", encoding="ascii")
    nodes = {}
    ports = []
    try:
        for i, name in enumerate(names):
            port = socket.socket()
            ports.append(port)
            port.bind(("127.0.0.1", 0))
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                            "-config", "req.cnf", "-subj", "/CN=" + name, "-keyout", name + ".key", "-out", name + ".pem",
                            "-addext", "basicConstraints=critical,CA:FALSE",
                            "-addext", "keyUsage=critical,digitalSignature,keyEncipherment",
                            "-addext", "subjectAltName=IP:127.0.0.1",
                            "-addext", "extendedKeyUsage=clientAuth,serverAuth"],
                           cwd=root, check=True, capture_output=True, timeout=15)
            uids = (uid_ranges or {}).get(name, ((i+1)*100000000, (i+2)*100000000-1))
            nodes[name] = {"host": "127.0.0.1", "port": port.getsockname()[1],
                           "cert": name + ".pem", "uids": list(uids)}
    finally:
        for port in ports:
            port.close()
    paths = {}
    for name in names:
        config = {"world": "test", "node": name, "key": name + ".key", "bind": "127.0.0.1",
                  "nodes": nodes, "scenes": scenes}
        paths[name] = root / (name + ".json")
        paths[name].write_text(json.dumps(config), encoding="utf-8")
    return paths


def worker(path):
    world = World(path.parent, path)
    def handle(peer, msg):
        if msg.get("bad"):
            raise ValueError("Private diagnostic")
        return {"source": peer, "owner": world.node_id, "data": msg, "secret": False}
    world.start(handle)
    print("ready", flush=True)
    try:
        for line in sys.stdin:
            req = json.loads(line)
            if req == "exit":
                break
            try:
                result = world.call(req["peer"], req["data"])
            except WorldError:
                result = {"failed": True}
            print(json.dumps(result), flush=True)
    finally:
        world.close()


class Worker:
    def __init__(self, path):
        self.p = subprocess.Popen([sys.executable, "-B", __file__, "child", str(path)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        ready = self.p.stdout.readline()
        if ready.strip() != "ready":
            self.close()
            raise RuntimeError("Worker failed to start")

    def write(self, msg):
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def call(self, peer, data):
        self.write({"peer": peer, "data": data})
        return json.loads(self.p.stdout.readline())

    def close(self):
        if self.p.poll() is None:
            try:
                self.write("exit")
                self.p.wait(8)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                self.p.kill()
                self.p.wait(5)
        self.p.stdin.close()
        self.p.stdout.close()


def test_world(root):
    paths = make_configs(root)
    topp = Topology(paths["a"])
    assert topp.uid_ranges["b"] == (200000000, 299999999)
    assert topp.owners == {101:"a", 102:"b", 103:"c"}
    for field in ("uids","cert"):
        changed = json.loads(paths["a"].read_bytes())
        changed["nodes"]["b"][field] = changed["nodes"]["a"][field]
        bad = root / "invalid.json"
        bad.write_text(json.dumps(changed))
        try:
            Topology(bad)
        except ValueError:
            continue
        raise AssertionError("Ambiguous origin or UID range accepted")
    workers = {}
    try:
        for name, path in paths.items():
            workers[name] = Worker(path)
        world = World(root, paths["a"])
        world.probe("b")
        world.probe("c")
        for a in workers:
            for b in workers:
                if a != b:
                    reply = workers[a].call(b, {"scene": 103, "msg":"hello"})
                    assert (reply["source"], reply["owner"], reply["data"]["msg"]) == (a,b,"hello")
        assert workers["a"].call("b", {"bad": True}) == {"failed": True}
        assert workers["a"].call("b", {"over": "X"*MAX_BODY}) == {"failed": True}
        # No client certificate and a trusted node claiming another ID both fail before dispatch.
        node = topp.nodes["a"]
        for authenticated in (False, True):
            client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
            client.load_verify_locations(cadata=node.pem)
            if authenticated:
                client.load_cert_chain(root / "b.pem",root / "b.key")
            try:
                with socket.create_connection((node.host,node.port), TIMEOUT) as raw:
                    with client.wrap_socket(raw, server_hostname=node.host) as channel:
                        send_frame(channel, {"wire":"OF-SF/WORLD/1","node":"c","manifest":topp.digest})
                        read_frame(channel)
                raise AssertionError("Unknown or mismatched client authenticated")
            except (EOFError, OSError):
                pass
        workers.pop("c").close()
        assert workers["b"].call("c", {}) == {"failed": True}
        assert workers["a"].call("b", {})["source"] == "a"
        original = paths["c"].read_bytes()
        mismatch = json.loads(original)
        mismatch["scenes"]["103"] = "b"
        paths["c"].write_text(json.dumps(mismatch))
        workers["c"] = Worker(paths["c"])
        assert workers["c"].call("b", {}) == {"failed": True}
        assert workers["b"].call("c", {}) == {"failed": True}
        workers.pop("c").close()
        paths["c"].write_bytes(original)
        workers["c"] = Worker(paths["c"])
        assert workers["b"].call("c", {"again":True})["owner"] == "c"
    finally:
        for worker in workers.values():
            worker.close()


if __name__ == "__main__":
    if sys.argv[1:2] == ["child"]:
        worker(Path(sys.argv[2]))
    elif shutil.which("openssl"):
        with TemporaryDirectory() as tmp:
            test_world(Path(tmp))
        print("PASS three peer processes: six direct links, UID ownership, mTLS identity, routing agreement, outage and recovery")
    else:
        print("SKIP three-peer transport check: openssl required for disposable certificates")
