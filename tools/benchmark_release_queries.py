"""Measure version queries against one OF_SF server checkout. Run once per revision to compare.

For each round, publish a synthetic package into a new temporary realm, issue 20 administrator
queries, then run eight simultaneous workers (four admin, four public) issuing eight queries each.
Timings include the application handlers and their locks, excluding HTTP and fixture setup.
Only bytes consumed by SHA-256 on the published package are counted, independent of OS disk cache.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import median
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Lock
from time import perf_counter
from unittest.mock import patch


class CountedReader:
    def __init__(self, stream):
        self.stream = stream
        self.count = 0

    def readable(self):
        return self.stream.readable()

    def readinto(self, buffer):
        n = self.stream.readinto(buffer)
        if n is not None:
            self.count += n
        return n


def summarize(responses, elapsed, hashes, checksum_bytes):
    times = sorted(ms for ms, error in responses if error is None)
    errors = Counter(error for _, error in responses if error is not None)
    return {
        "requests": len(responses), "successes": len(times), "errors": dict(errors),
        "p50_ms": round(median(times), 3) if times else None,
        "p95_ms": round(times[math.ceil(len(times)*0.95)-1], 3) if times else None,
        "elapsed_ms": round(elapsed*1000, 3), "hashes": hashes, "checksum_bytes": checksum_bytes,
    }


def measure(root, mib):
    import realm_admin
    from local_identity import LocalIdentity
    from realm_maintenance import Maintenance

    state = root/"realm"
    state.mkdir()
    owner = Maintenance(LocalIdentity(state/"identity.sqlite3"))
    package = root/"fixture.zip"
    block = bytes(range(256)) * 4096
    digest = hashlib.sha256()
    with package.open("wb") as out:
        for _ in range(mib):
            out.write(block)
            digest.update(block)
    expected = owner.publish(package, "1.2.0", digest.hexdigest(), "benchmark")
    published = (owner.blobs/expected["sha256"]).stat()
    real_digest = hashlib.file_digest
    hashes = checksum_bytes = 0
    counter_lock = Lock()

    def count_digest(stream, algorithm, *args, **kwargs):
        nonlocal hashes, checksum_bytes
        if algorithm != "sha256" or not os.path.samestat(os.fstat(stream.fileno()), published):
            return real_digest(stream, algorithm, *args, **kwargs)
        reader = CountedReader(stream)
        result = real_digest(reader, algorithm, *args, **kwargs)
        with counter_lock:
            hashes += 1
            checksum_bytes += reader.count
        return result

    def query(admin):
        started = perf_counter()
        if admin:
            response = realm_admin.response_for(lambda: realm_admin.execute(state, "maintenance.list", {}, None))
            if not response["ok"]:
                code = response["error"]["code"]
                if code != "state_busy":
                    raise RuntimeError("Unexpected management error: "+code)
                return (perf_counter()-started)*1000, code
            release = response["result"]["release"]
        else:
            release = owner.feed()["release"]
        if release != expected:
            raise RuntimeError("Returned release differs from the published fixture")
        return (perf_counter()-started)*1000, None

    rendezvous = Barrier(8)
    def worker(i):
        rendezvous.wait(timeout=20)
        return [query(i % 2 == 0) for _ in range(8)]

    with patch.object(hashlib, "file_digest", count_digest):
        start = perf_counter()
        sequential = [query(True) for _ in range(20)]
        seq = summarize(sequential, perf_counter()-start, hashes, checksum_bytes)
        if seq["successes"] != 20:
            raise RuntimeError("Uncontended management queries failed")
        seq["cold_ms"] = round(sequential[0][0], 3)
        hashes = checksum_bytes = 0
        start = perf_counter()
        with ThreadPoolExecutor(max_workers=8) as pool:
            mixed = [reply for batch in pool.map(worker, range(8)) for reply in batch]
        mix = summarize(mixed, perf_counter()-start, hashes, checksum_bytes)
    return {"sequential": seq, "mixed": mix}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, required=True, help="repository root or OverField-Server directory")
    parser.add_argument("--mib", type=int, default=64, help="synthetic package size in MiB (default 64)")
    parser.add_argument("--rounds", type=int, default=1, help="independent disposable realms (default 1)")
    args = parser.parse_args()
    if not (1 <= args.mib <= 4096 and args.rounds >= 1):
        parser.error("--mib must be 1..4096 and --rounds must be positive")
    server = args.server.resolve()
    if (server/"OverField-Server").is_dir():
        server = server/"OverField-Server"
    if not (server/"app"/"realm_admin.py").is_file():
        parser.error("--server must contain the OF_SF server sources")
    sys.path.insert(0, str(server/"app"))
    rounds = []
    for _ in range(args.rounds):
        with TemporaryDirectory(prefix="of-release-benchmark-") as temp:
            rounds.append(measure(Path(temp), args.mib))
    print(json.dumps({"package_bytes": args.mib*1024**2, "rounds": rounds}, indent=2))


if __name__ == "__main__":
    main()
