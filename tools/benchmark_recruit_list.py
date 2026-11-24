"""Measure native banner-list reads on a disposable realm with an existing draw history."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns


def run(server, iterations):
    platform = "win64" if sys.platform == "win32" else "linux-x86_64"
    sys.path[:0] = [str(server / path) for path in
                    ("app", "app/upstream", "tests", "vendor/common", "vendor/" + platform)]
    from local_game_service import LocalGameService, pb
    from local_identity import LocalIdentity
    from private_gacha import list_pools
    from realm_gacha import configure, defaults
    from test_game_realm import login, join
    from test_native_gacha import call
    from utils import db

    results = {}
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        owner = LocalIdentity(root / "identity.sqlite3")
        profile = owner.create_profile("Bench")
        host = LocalGameService(owner, root, server / "resources", port=0)
        host.start()
        try:
            client, _, pid = login(host.port, owner.issue_ticket(*profile))
            with client:
                join(client)
                with host.lock, db.conn:
                    session = next(s for s in host.active.values() if s.player_id == pid)
                    for pool, size in ((2000, 1100), (3000, 300), (9000, 12), (3001, 588)):
                        db.conn.executemany("INSERT INTO gacha_record(player_id,gacha_id,item_id,gacha_time) VALUES(?,?,?,?)",
                                            ((pid, pool, 10040, t) for t in range(size)))
                    db.set_gacha_guarantee(pid, 3000, 12)
                    db.conn.execute("INSERT INTO private_gacha_choices VALUES(?,3000)", (pid,))
                    db.conn.execute("INSERT INTO private_gacha_picks VALUES(?,3001,401004)", (pid,))
                config = defaults()
                for phase in ("initial", "disable", "reopen"):
                    if phase != "initial":
                        next(p for p in config["pools"] if p["id"] == 3001)["enabled"] = phase == "reopen"
                        configure(root, config)
                    response = call(client, "GachaList", 12)
                    rows = {p.gacha_id: p for p in response.gachas}
                    assert rows[2000].gacha_times == 1100 and rows[3000].gacha_times == 300
                    assert rows[9000].gacha_times == 12 and rows[9000].choose_pool_id == 3000
                    assert (3001 in rows) == (phase != "disable")
                    if 3001 in rows:
                        assert rows[3001].gacha_times == 588 and rows[3001].has_full_pick
                    wire = response.SerializeToString(deterministic=True)
                    times = []
                    with host.lock:
                        def read():
                            rsp = pb.GachaListRsp()
                            rsp.status = list_pools(session, pb.GachaListReq(), rsp)
                            return rsp
                        trace = []
                        db.conn.set_trace_callback(trace.append)
                        try:
                            assert read().SerializeToString(deterministic=True) == wire
                        finally:
                            db.conn.set_trace_callback(None)
                        for _ in range(7):
                            start = perf_counter_ns()
                            for _ in range(iterations):
                                last = read()
                            times.append((perf_counter_ns() - start) / iterations / 1000)
                            assert last.SerializeToString(deterministic=True) == wire
                    results[phase] = {"pools": len(rows), "sql_statements": len(trace),
                                      "handler_median_us": round(median(times), 1), "reply_sha256": sha256(wire).hexdigest()}
        finally:
            host.stop()
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, default=Path(__file__).resolve().parents[1] / "OverField-Server")
    parser.add_argument("--iterations", type=int, default=300)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    print(json.dumps(run(args.server.resolve(), args.iterations), indent=2))
