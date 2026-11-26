"""Measure independent realm processes with synchronized, durable native TCP workloads."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_mixed_gameplay import digest, percentiles, run


def install_gate(gate):
    global start_gate
    start_gate = gate


def measure_realm(server, players, rounds, group, first_role):
    details = {}
    try:
        result = run(server, players, rounds, group, first_role=first_role,
                     ready=lambda: start_gate.wait(timeout=180), details=details)
    except BaseException:
        start_gate.abort()
        raise
    if details["journal_mode"] != "wal" or details["synchronous"] < 2:
        raise RuntimeError("Benchmark requires on-disk WAL and synchronous FULL or EXTRA")
    return details, result


def measure(server, realms, players, rounds, group):
    if players % (group * realms):
        raise ValueError("Distribute complete channels evenly across processes")
    local_players = players // realms
    ctx = multiprocessing.get_context("spawn")
    gate = ctx.Barrier(realms)
    with ProcessPoolExecutor(max_workers=realms, mp_context=ctx,
                             initializer=install_gate, initargs=(gate,)) as pool:
        futures = [pool.submit(measure_realm, server, local_players, rounds, group, i * local_players)
                   for i in range(realms)]
        samples = [future.result() for future in futures]

    elapsed = (max(d["end_ns"] for d, _ in samples) - min(d["start_ns"] for d, _ in samples)) / 1e9
    rtt = [t for d, _ in samples for t in d["rtt_ns"]]
    result = dict(realms=realms, players=players, players_per_realm=local_players, rounds=rounds,
                  members_per_channel=group, requests=len(rtt), elapsed_s=round(elapsed, 3),
                  requests_per_s=round(len(rtt) / elapsed, 1), rtt_ms=percentiles(rtt),
                  commits=sum(s["sqlite_commit_ms"]["count"] for _, s in samples),
                  saved=sum(s["persisted_roles"] for _, s in samples),
                  broadcasts=sum(s["verified_broadcast_deliveries"] for _, s in samples),
                  role_proof=digest([proof for d, _ in samples for proof in d["role_proofs"]]),
                  journal_mode="wal", synchronous=sorted({d["synchronous"] for d, _ in samples}))
    result["lock_p95_ms"] = percentiles([t for d, _ in samples for t in d["lock_wait_ns"]])["p95"]
    result["commit_p95_ms"] = percentiles([t for d, _ in samples for t in d["commit_ns"]])["p95"]
    if all("private_bytes" in d for d, _ in samples):
        result["combined_private_MiB"] = round(sum(d["private_bytes"] for d, _ in samples) / 2**20, 1)
    if (len(rtt), result["saved"], result["broadcasts"]) != (players * rounds * 4, players, players * group * rounds):
        raise RuntimeError(f"Incomplete workload: {result}")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, required=True, help="Hydrated server root with native tests")
    parser.add_argument("--realms", type=int, nargs="+", choices=(1, 2, 4), default=[1, 2, 4])
    parser.add_argument("--players", type=int, default=48)
    parser.add_argument("--group", type=int, default=6)
    parser.add_argument("--rounds", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.players <= 512 or not 1 <= args.group <= args.players or args.rounds < 1:
        parser.error("Choose 1–512 players, a valid channel size and positive rounds")
    if any(args.players % (args.group * n) for n in args.realms):
        parser.error("Each realm must contain the same number of complete channels")
    if 10000 + args.players * max(args.rounds, 1000) >= 2**24:
        parser.error("Movement markers must fit exactly in float32")
    reference = None
    for n in args.realms:
        result = measure(args.server.resolve(), n, args.players, args.rounds, args.group)
        if reference is not None and result["role_proof"] != reference:
            raise RuntimeError("Receipts, scoped broadcasts or final player state changed across layouts")
        reference = result["role_proof"]
        print(json.dumps(result, sort_keys=True), flush=True)
