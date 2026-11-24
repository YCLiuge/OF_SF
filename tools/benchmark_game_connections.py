"""Measure local native protocol latency, shared-lock waits, durable commits and slot reuse."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
import hashlib
import json
import math
from pathlib import Path
import socket
import sqlite3
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
from time import monotonic, perf_counter_ns, sleep


class TimedLock:
    def __init__(self, lock):
        self.lock = lock
        self.wait = []

    def __enter__(self):
        before = perf_counter_ns()
        self.lock.acquire()
        self.wait.append(perf_counter_ns() - before)
        return self

    def __exit__(self, *exc):
        self.lock.release()


class TimedConnection:
    """Measure only exits which commit an actual SQLite write transaction."""
    def __init__(self, connection):
        self.connection = connection
        self.commits = []

    def __getattr__(self, key):
        return getattr(self.connection, key)

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, kind, value, trace):
        if kind is not None or not self.connection.in_transaction:
            return self.connection.__exit__(kind, value, trace)
        before = perf_counter_ns()
        result = self.connection.__exit__(kind, value, trace)
        self.commits.append(perf_counter_ns() - before)
        return result


def stats(values):
    ordered = sorted(values)
    def percentile(q):
        return round(ordered[math.ceil(q * len(ordered)) - 1] / 1_000_000, 3)
    return {"count": len(ordered), "p50_ms": percentile(.5), "p95_ms": percentile(.95)}


def run(server, clients, rounds, configured_limit):
    platform = "win64" if sys.platform == "win32" else "linux-x86_64"
    sys.path[:0] = [str(server / p) for p in ("app", "app/upstream", "tests", "vendor/common", "vendor/" + platform)]
    from local_game_service import LocalGameService, MsgId, pb
    from local_identity import LocalIdentity
    from realm_rules import configure
    from test_game_archive import request
    from test_game_realm import login, join
    from utils import db

    with TemporaryDirectory(prefix="of-connection-bench-") as tmp:
        root = Path(tmp)
        if configured_limit:
            configure(root, {"game_connections": configured_limit})
        identity = LocalIdentity(root / "identity.sqlite3")
        host = LocalGameService(identity, root, server / "resources", port=0)
        host.start()
        try:
            with ExitStack() as sockets:
                peers = []
                for i in range(clients):
                    profile = identity.create_profile("Bench" + str(i))
                    sock, _, uid = login(host.port, identity.issue_ticket(*profile))
                    sockets.enter_context(sock)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    join(sock)
                    peers.append((sock, uid))
                lock = TimedLock(host.lock)
                conn = TimedConnection(db.conn)
                barrier = Barrier(clients)
                def worker(i):
                    sock, uid = peers[i]
                    digest = hashlib.sha256()
                    timings = []
                    barrier.wait(timeout=30)
                    for step in range(rounds):
                        key = "latency"
                        val = f"{uid}-{step}"
                        payload = pb.SetArchiveInfoReq(key=key, value=val)
                        before = perf_counter_ns()
                        rsp = request(sock, MsgId.SetArchiveInfoReq, MsgId.SetArchiveInfoRsp,
                                      payload, pb.SetArchiveInfoRsp, step + 20)
                        timings.append(perf_counter_ns() - before)
                        if (rsp.status, rsp.key, rsp.value) != (pb.StatusCode_OK, key, val):
                            raise RuntimeError("Native archive response differs")
                        digest.update(rsp.SerializeToString())
                    return timings, digest.hexdigest()
                host.lock = lock
                db.conn = conn
                try:
                    with ThreadPoolExecutor(max_workers=clients) as pool:
                        outcomes = list(pool.map(worker, range(clients)))
                finally:
                    # The underlying lock is the same one used by existing session conditions.
                    with lock.lock:
                        db.conn = conn.connection
                        host.lock = lock.lock
                if len(conn.commits) != clients * rounds:
                    raise RuntimeError("Expected one durable commit per archive write")
                before_close = perf_counter_ns()
            deadline = monotonic() + 10
            while True:
                with host.connections_lock:
                    if not host.connections:
                        break
                if monotonic() > deadline:
                    raise RuntimeError("Slots were not released on logout")
                sleep(.001)
            close_ms = round((perf_counter_ns() - before_close) / 1_000_000, 3)
        finally:
            host.stop()
        with closing(sqlite3.connect(root / "game.sqlite")) as saved:
            rows = saved.execute("SELECT player_id,value FROM private_archive WHERE key='latency' ORDER BY player_id").fetchall()
            if rows != sorted((uid, f"{uid}-{rounds-1}") for _,uid in peers):
                raise RuntimeError("Persisted archive differs")
        return {"clients":clients, "requests":clients * rounds, "lock_wait":stats(lock.wait),
                "sqlite_commit":stats(conn.commits), "native_rtt":stats([t for times,_ in outcomes for t in times]),
                "logout_all_slots_ms":close_ms, "persisted_players":len(rows),
                "response_sha256":hashlib.sha256("".join(h for _,h in outcomes).encode()).hexdigest()}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--server", type=Path, default=Path(__file__).resolve().parents[1] / "OverField-Server")
    cli.add_argument("--clients", type=int, default=8)
    cli.add_argument("--rounds", type=int, default=10)
    cli.add_argument("--limit", type=int, help="configure the disposable realm admission limit before launch")
    opts = cli.parse_args()
    if not 1 <= opts.clients <= (opts.limit or 24) or opts.rounds < 1:
        cli.error("Choose positive rounds and a client count within the realm limit")
    print(json.dumps(run(opts.server.resolve(), opts.clients, opts.rounds, opts.limit), sort_keys=True))
