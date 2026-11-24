"""Measure native TCP movement broadcasts and SQLite pose writes on a disposable realm."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
import json
import math
from pathlib import Path
import socket
import sqlite3
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns, sleep


def run(server, pairs, rounds, concurrent_channels, tick_ms):
    platform = "win64" if sys.platform == "win32" else "linux-x86_64"
    sys.path[:0] = [str(server / path) for path in
                    ("app", "app/upstream", "tests", "vendor/common", "vendor/" + platform)]
    from local_game_service import LocalGameService, MsgId, pb
    from local_game_wire import encode, receive
    from local_identity import LocalIdentity
    from test_game_realm import join, login
    from utils import db

    counts = Counter()
    samples = []
    players = []
    sockets = []

    def count(statement):
        upper = statement.lstrip().upper()
        if upper.startswith(("INSERT", "UPDATE", "REPLACE")) and "PRIVATE_POSES" in upper:
            counts["pose_writes"] += 1
        if upper == "COMMIT":
            counts["commits"] += 1

    def wait_for(sock, message_id):
        while True:
            head, raw = receive(sock)
            if head.msg_id == message_id:
                return raw
            if head.msg_id == MsgId.PlayerSceneSyncDataNotice:
                raise RuntimeError("Unexpected movement frame from another channel")

    with TemporaryDirectory(prefix="of-movement-") as tmp:
        root = Path(tmp)
        identity = LocalIdentity(root / "identity.sqlite3")
        service = LocalGameService(identity, root, server / "resources", port=0)
        service.start()
        try:
            with ExitStack() as stack:
                for i in range(2 * pairs):
                    profile = identity.create_profile("Bench" + str(i))
                    client, _, pid = login(service.port, identity.issue_ticket(*profile))
                    stack.enter_context(client)
                    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    join(client)
                    req = pb.ChangeSceneChannelReq(scene_id=9999, channel_label=i // 2 + 1)
                    client.sendall(encode(MsgId.ChangeSceneChannelReq, req.SerializeToString(), 4))
                    change = pb.ChangeSceneChannelRsp.FromString(wait_for(client, MsgId.ChangeSceneChannelRsp))
                    if change.status != pb.StatusCode_OK or change.channel_id != req.channel_label:
                        raise RuntimeError("Could not place disposable role in benchmark channel")
                    wait_for(client, MsgId.SceneDataNotice)
                    sockets.append(client)
                    players.append(pid)
                with service.lock:
                    db.conn.set_trace_callback(count)
                def move(group):
                    timings = []
                    origin = perf_counter_ns()
                    for step in range(1, rounds + 1):
                        if tick_ms:
                            delay = (origin + (step - 1) * tick_ms * 1_000_000 - perf_counter_ns()) / 1e9
                            if delay > 0:
                                sleep(delay)
                        for i in range(group * 2, group * 2 + 2):
                            client = sockets[i]
                            expected_x = step + i * rounds
                            req = pb.PlayerSceneRecordReq()
                            req.data.char_recorder_data_lst.add(char_id=101001).pos.x = expected_x
                            packet = encode(MsgId.PlayerSceneRecordReq, req.SerializeToString(), 10 + step)
                            sent = perf_counter_ns()
                            client.sendall(packet)
                            for dest in (client, sockets[i ^ 1]):
                                raw = wait_for(dest, MsgId.PlayerSceneSyncDataNotice)
                                notice = pb.PlayerSceneSyncDataNotice.FromString(raw)
                                found = [(entry.player_id, rec.pos.x) for entry in notice.data
                                         for data in entry.data for rec in data.char_recorder_data_lst]
                                if found != [(players[i], expected_x)]:
                                    raise RuntimeError("Movement broadcast had unexpected player or position")
                            timings.append(perf_counter_ns() - sent)
                    return timings

                start = perf_counter_ns()
                if concurrent_channels:
                    with ThreadPoolExecutor(max_workers=pairs) as pool:
                        for timings in pool.map(move, range(pairs)):
                            samples.extend(timings)
                else:
                    for group in range(pairs):
                        samples.extend(move(group))
                elapsed = (perf_counter_ns() - start) / 1e9
                # A ping is a FIFO barrier. Detect any out-of-channel frames still in flight.
                for client in sockets:
                    client.sendall(encode(MsgId.PlayerPingReq, pb.PlayerPingReq().SerializeToString(), 5))
                    wait_for(client, MsgId.PlayerPingRsp)
        finally:
            service.stop()
        with closing(sqlite3.connect(root / "game.sqlite")) as saved:
            restored = {pid: pb.Vector3.FromString(raw).x for pid, raw in saved.execute(
                "SELECT player_id,pos FROM private_poses WHERE scene=9999")}
        if restored != {pid: rounds * (i + 1) for i, pid in enumerate(players)}:
            raise RuntimeError("Final persisted positions do not match the last visible movement")
    samples.sort()
    def percentile(fraction):
        return round(samples[math.ceil(len(samples) * fraction) - 1] / 1e6, 3)
    return {"tick_ms": tick_ms, "concurrent_channels": concurrent_channels, "clients": len(players), "channels": pairs, "movement_frames": len(samples),
            "verified_deliveries": 2 * len(samples), "pose_writes": counts["pose_writes"],
            "commits_including_logout": counts["commits"], "elapsed_s": round(elapsed, 3),
            "delivery_p50_ms": percentile(0.50), "delivery_p95_ms": percentile(0.95),
            "final_positions_verified": len(restored)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, default=Path(__file__).resolve().parents[1] / "OverField-Server")
    parser.add_argument("--pairs", type=int, default=2, help="pairs of clients in separate public channels")
    parser.add_argument("--rounds", type=int, default=80, help="movements per client")
    parser.add_argument("--concurrent-channels", action="store_true", help="move one pair per thread")
    parser.add_argument("--tick-ms", type=int, default=0, help="minimum time between scheduled updates per player")
    args = parser.parse_args()
    if args.tick_ms < 0:
        parser.error("tick-ms must be nonnegative")
    if min(args.pairs, args.rounds) < 1:
        parser.error("pairs and rounds must be positive")
    print(json.dumps(run(args.server.resolve(), args.pairs, args.rounds, args.concurrent_channels, args.tick_ms), sort_keys=True))
