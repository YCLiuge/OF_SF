"""Compare native star-tree and ability queries on a temporary loopback realm.

Use the same benchmark script, resources, Python and arguments for both revisions.
For example: python tools/benchmark_badge_queries.py --server OverField-Server
--assets <extracted-server> --players 28 --workers 7 --rounds 12
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import json
from math import ceil
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from threading import Event
from time import perf_counter, perf_counter_ns, process_time


def run(server, assets, players, workers, rounds):
    platform = "win64" if sys.platform == "win32" else "linux-x86_64"
    sys.path[:0] = [str(Path(__file__).resolve().parent), *(str(server / p) for p in ("app", "app/upstream", "tests"))]
    sys.path[:0] = [str(assets / "vendor" / p) for p in ("common", platform)]
    from local_game_service import LocalGameService, MsgId, pb
    from local_game_wire import encode, receive
    from local_identity import LocalIdentity
    from realm_rules import configure
    from test_game_realm import login, join
    from utils import db
    from utils.pb_create import make_item
    from utils.res_loader import res

    requests = (("AbilityBadgeList", {}), ("AbilityBadgeList", {"scene_id": 1}), ("PlayerAbilityList", {}))
    def exchange(sock, operation, packet):
        key, fields = requests[operation]
        payload = getattr(pb, key + "Req")(**fields).SerializeToString()
        sock.sendall(encode(getattr(MsgId, key + "Req"), payload, packet))
        while True:
            head, raw = receive(sock)
            if head.msg_id == getattr(MsgId, key + "Rsp") and head.packet_id == packet:
                reply = getattr(pb, key + "Rsp").FromString(raw)
                if reply.status != pb.StatusCode_OK:
                    raise RuntimeError(f"{key} returned {reply.status}")
                return raw

    with TemporaryDirectory(prefix="ofsf-badge-bench-") as tmp, ExitStack() as stack:
        state = Path(tmp)
        configure(state, {"game_connections": players})
        identity = LocalIdentity(state / "identity.sqlite3")
        host = LocalGameService(identity, state, assets / "resources", port=0)
        host.start()
        stack.callback(host.stop)
        abilities = sorted(res.index("Ability", "ability").values(), key=lambda r: r["i_d"])
        rewards = sorted({r["achieve_condition_i_d"] for g in res.index("MoonStone", "stone_reward_quest_data").values()
                          for r in g.get("stone_reward_quest_data_group_info", ()) if r.get("achieve_condition_i_d")})
        peers = []
        expected = []
        for i in range(players):
            profile = identity.create_profile("Bench" + str(i))
            sock, _, pid = login(host.port, identity.issue_ticket(*profile))
            stack.enter_context(sock)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            join(sock)
            with host.lock, db.conn:
                # Half have a complete ability inventory, a quarter partial, a quarter untouched.
                items = abilities if i % 4 < 2 else abilities[::3] if i % 4 == 2 else []
                for item in items:
                    key = item["need_item_i_d"]
                    detail = make_item(key, 1, pid)
                    db.set_item_detail(pid, detail.SerializeToString(), key)
                for key in rewards[:(362 if i % 4 < 2 else 80 if i % 4 == 2 else 0)]:
                    row = pb.Achieve(achieve_id=key, count=1 + (key + i) % 7)
                    db.set_achieve(pid, key, row.SerializeToString())
            peers.append(sock)
            expected.append([exchange(sock, op, 10 + op) for op in range(len(requests))])

        # One mature player's SQL sample is outside the timed phase.
        sql = Counter()
        achievement_select = []
        def count_statement(statement):
            sql[statement.split(None, 1)[0].upper()] += 1
            if not achievement_select and statement.startswith("SELECT achieve_id,achieve_blob FROM achieve"):
                achievement_select.append(statement)
        with host.lock:
            db.conn.set_trace_callback(count_statement)
        try:
            for op in range(len(requests)):
                if exchange(peers[0], op, 20 + op) != expected[0][op]:
                    raise AssertionError("Warm-up changed the reply")
        finally:
            with host.lock:
                db.conn.set_trace_callback(None)
        with host.lock:
            plan = [row[3] for row in db.conn.execute("EXPLAIN QUERY PLAN " + achievement_select[0])]

        start = Event()
        def worker(i):
            timings = []
            start.wait()
            for turn in range(rounds):
                for offset in range(len(requests)):
                    op = (i + offset) % len(requests)
                    then = perf_counter_ns()
                    raw = exchange(peers[i], op, 30 + turn * len(requests) + offset)
                    timings.append(perf_counter_ns() - then)
                    if raw != expected[i][op]:
                        raise AssertionError(f"Unexpected reply for player {i}, operation {op}")
            return timings

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(worker, i) for i in range(players)]
            began, cpu = perf_counter(), process_time()
            start.set()
            timings = sorted(t for future in futures for t in future.result())
            duration, cpu = perf_counter() - began, process_time() - cpu
        digest = hashlib.sha256()
        for result in expected:
            for raw in result:
                digest.update(len(raw).to_bytes(4, "big"))
                digest.update(raw)
        # Fingerprint the actual persistent ability/achievement rows as well as response bytes.
        saved = hashlib.sha256()
        with host.lock:
            for query in ("SELECT player_id,k,typeof(v),quote(v) FROM player_kv WHERE ns='ability' ORDER BY player_id,k",
                          "SELECT player_id,achieve_id,hex(achieve_blob) FROM achieve ORDER BY player_id,achieve_id"):
                for row in db.conn.execute(query):
                    saved.update(json.dumps(tuple(row)).encode() + b"\n")
        report = dict(players=players, workers=workers, requests=len(timings), rps=round(len(timings)/duration, 1),
                      p95_ms=round(timings[ceil(len(timings)*0.95)-1]/1e6, 3), cpu_s=round(cpu, 3),
                      receipt_sha256=digest.hexdigest(), saved_sha256=saved.hexdigest(), sample_sql=dict(sql),
                      achievement_plan=plan)
        if sys.platform == "win32":
            from benchmark_resident_memory import process_memory
            report["end_working_set_MiB"] = round(process_memory()[0] / 2**20, 1)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, required=True)
    parser.add_argument("--assets", type=Path, help="Hydrated resources and vendor, defaults to --server")
    parser.add_argument("--players", type=int, default=28)
    parser.add_argument("--workers", type=int, default=7)
    parser.add_argument("--rounds", type=int, default=12)
    args = parser.parse_args()
    if not 1 <= args.workers <= args.players <= 512 or args.rounds < 1:
        parser.error("require 1 <= workers <= players <= 512, rounds >= 1")
    print(json.dumps(run(args.server.resolve(), (args.assets or args.server).resolve(), args.players,
                         args.workers, args.rounds), sort_keys=True))
