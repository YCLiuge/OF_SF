"""Measure concurrent native photo uploads and archive writes in one disposable realm.

Run this script against each unpacked server using the same WebP test image, for example:

    python tools/benchmark_photo_uploads.py --server OverField-Server --photo sample.webp --cpu-rounds 512

The default workload uses four uploaders and four writers, 12 synchronized rounds and 24
distinct photos. Before timing, each socket completes a native login. Afterward the test checks
media bytes, original database fingerprints, per-user quota and saved archive values. The optional
CPU measurement runs the real preparation on one or two threads using identical bytes and total
operations. Process affinity can be set externally when comparing processor counts.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, local
from time import perf_counter_ns

from benchmark_game_connections import TimedLock, stats


class RealmLock(TimedLock):
    def __init__(self, lock):
        super().__init__(lock)
        self.starts = local()
        self.holds = []

    def __enter__(self):
        super().__enter__()
        self.starts.value = perf_counter_ns()
        return self

    def __exit__(self, *exc):
        self.holds.append(perf_counter_ns() - self.starts.value)
        super().__exit__(*exc)


def measure_cpu(prepare, raw, count):
    """Repeat the same production preparation with one or two request threads, no socket or disk."""
    def phase(threads):
        barrier = Barrier(threads + 1)
        def work(n):
            barrier.wait()
            for _ in range(n):
                prepare(raw, 1)

        with ThreadPoolExecutor(max_workers=threads) as pool:
            tasks = [pool.submit(work, count // threads) for _ in range(threads)]
            begin = perf_counter_ns()
            barrier.wait()
            for task in tasks:
                task.result()
            elapsed = perf_counter_ns() - begin
        return {"operations": count, "wall_ms": round(elapsed/1e6, 3)}
    return {f"{threads}_thread": phase(threads) for threads in (1, 2)}


def run(server, photo, readers, uploaders, rounds, cpu_rounds):
    platform = "win64" if sys.platform == "win32" else "linux-x86_64"
    sys.path[:0] = [str(server / p) for p in ("app", "app/upstream", "tests", "vendor/common", "vendor/" + platform)]
    from local_game_service import LocalGameService, pb, MsgId
    from local_game_wire import encode, receive
    from local_identity import LocalIdentity
    from photo_media import dimensions
    from test_game_realm import join, login

    dimensions(photo)
    with TemporaryDirectory(prefix="of-photo-bench-") as directory:
        state = Path(directory)
        identity = LocalIdentity(state / "identity.sqlite3")
        game = LocalGameService(identity, state, server / "resources", port=0)
        barrier = Barrier(uploaders + readers)
        peers = []
        personas = []
        game.start()
        try:
            with ExitStack() as sockets:
                for i in range(uploaders + readers):
                    account = identity.create_profile(f"Test{i}")
                    peer, _, pid = login(game.port, identity.issue_ticket(*account))
                    sockets.enter_context(peer)
                    peer.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    join(peer)
                    peers.append(peer)
                    personas.append(pid)
                size = len(photo)
                def worker(i):
                    timings = []
                    peer = peers[i]
                    for iteration in range(rounds):
                        # Alternate a first-time upload and the same photograph under a new packet
                        # number. Both quota and the stored unique fingerprint get exercised.
                        seq = 32 + iteration
                        if i < uploaders:
                            tag = "UploadPhotoShare"
                            request = pb.UploadPhotoShareReq(file=photo, title=f"Frame{iteration // 2}")
                        else:
                            tag = "SetArchiveInfo"
                            request = pb.SetArchiveInfoReq(key="PhotoBench", value=str(iteration))
                        frame = encode(getattr(MsgId, tag + "Req"), request.SerializeToString(), seq)
                        barrier.wait(timeout=30)
                        start = perf_counter_ns()
                        peer.sendall(frame)
                        while True:
                            head, data = receive(peer)
                            if head.msg_id == getattr(MsgId, tag + "Rsp") and head.packet_id == seq:
                                break
                        timings.append(perf_counter_ns() - start)
                        reply = getattr(pb, tag + "Rsp").FromString(data)
                        assert reply.status == pb.StatusCode_OK
                        if i < uploaders:
                            assert reply.info.code > 0 and reply.info.title == request.title
                        else:
                            assert (reply.key, reply.value) == (request.key, request.value)
                    return timings

                lock = RealmLock(game.lock)
                game.lock = lock
                try:
                    start = perf_counter_ns()
                    with ThreadPoolExecutor(max_workers=len(peers)) as pool:
                        samples = list(pool.map(worker, range(len(peers))))
                    elapsed = perf_counter_ns() - start
                finally:
                    with lock.lock:
                        game.lock = lock.lock
        finally:
            game.stop()

        copies = (rounds + 1) // 2
        with closing(sqlite3.connect(state / "game.sqlite")) as saved:
            rows = saved.execute("SELECT owner,title,fingerprint,file FROM realm_photos "
                                 "JOIN realm_photo_media USING(code)").fetchall()
            assert len(rows) == copies * uploaders
            assert set(saved.execute("SELECT player,used FROM realm_photo_quotas")) == {
                (pid, copies) for pid in personas[:uploaders]}
            for owner, title, fingerprint, stored in rows:
                assert owner in personas[:uploaders] and stored == photo
                original = pb.UploadPhotoShareReq(file=photo, title=title).SerializeToString(deterministic=True)
                assert fingerprint == hashlib.sha256(original + bytes(4)).digest()
            assert set(saved.execute("SELECT player_id,value FROM private_archive WHERE key='PhotoBench'")) == {
                (pid, str(rounds-1)) for pid in personas[uploaders:]}
        import private_photos
        prepare = getattr(private_photos, "prepare_upload", None)
        raw = pb.UploadPhotoShareReq(file=photo, title="CPU").SerializeToString()
        return {"uploaders": uploaders, "writers": readers, "rounds": rounds,
                "preparation_only": measure_cpu(prepare, raw, cpu_rounds) if prepare and cpu_rounds else None,
                "photo_bytes": size, "photo_sha256": hashlib.sha256(photo).hexdigest(),
                "unique_photos": len(rows), "seconds": round(elapsed/1e9, 4),
                "upload_rtt": stats([v for group in samples[:uploaders] for v in group]),
                "archive_rtt": stats([v for group in samples[uploaders:] for v in group]),
                "lock_wait": stats(lock.wait), "lock_hold": stats(lock.holds),
                "total_lock_hold_ms": round(sum(lock.holds)/1e6, 3)}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--server", type=Path, required=True)
    cli.add_argument("--photo", type=Path, required=True, help="WebP image below the native 2 MiB frame limit")
    cli.add_argument("--uploaders", type=int, default=4)
    cli.add_argument("--writers", type=int, default=4)
    cli.add_argument("--rounds", type=int, default=12, help="each two rounds represent one stored photo per uploader")
    cli.add_argument("--cpu-rounds", type=int, default=0, help="also compare an even number of preparations on one/two threads")
    args = cli.parse_args()
    if not (1 <= args.rounds <= 16 and args.writers > 0 and args.uploaders > 0 and args.writers + args.uploaders <= 24):
        cli.error("choose 1–16 rounds and two positive client counts with a sum at most 24")
    if args.cpu_rounds < 0 or args.cpu_rounds % 2:
        cli.error("choose an even, nonnegative CPU sample size")
    print(json.dumps(run(args.server.resolve(), args.photo.read_bytes(), args.writers, args.uploaders,
                         args.rounds, args.cpu_rounds)))
