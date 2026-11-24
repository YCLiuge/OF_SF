"""Exercise archive, avatar, signature and scoped movement writes on a disposable loopback realm.

Run this script once per unpacked server bundle and compare the three SHA-256 fields.
Each client visits one channel, sends all four requests in every round and drains remaining
movement messages through a final ping. Timings exclude login, channel setup and final drain.
"""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, ExitStack
import hashlib
import json
import math
from pathlib import Path
import socket
import sqlite3
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
from time import monotonic, perf_counter_ns

from benchmark_game_connections import TimedConnection, TimedLock


OPERATIONS = ("SetArchiveInfo", "ChangeHead", "ChangeSign", "PlayerSceneRecord")
HEADS = (9005, 9404)
PLAZA = 9999


def percentiles(values):
    values = sorted(values)
    result = {"count": len(values)}
    for percentile in (50, 95, 99):
        rank = math.ceil(len(values) * percentile / 100)
        result[f"p{percentile}"] = round(values[rank - 1] / 1_000_000, 4)
    return result


def digest(rows):
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()


def check(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def run(server, clients, rounds, group):
    platform = "win64" if sys.platform == "win32" else "linux-x86_64"
    components = ("app", "app/upstream", "tests", "vendor/common", "vendor/" + platform)
    sys.path[:0] = [str(server / path) for path in components]
    from local_game_service import LocalGameService, MsgId, pb
    from local_game_wire import encode, receive
    from local_identity import LocalIdentity
    from realm_rules import configure
    from test_game_archive import request
    from test_game_realm import join, login, next_msg
    from utils import db

    stride = max(1000, rounds)

    def x_at(index, step):
        return 10000 + index * stride + step

    def channel(index):
        return 1 + index // group

    with TemporaryDirectory(prefix="of-mixed-tcp-") as tmp:
        root = Path(tmp)
        configure(root, {"game_connections": clients})
        identity = LocalIdentity(root / "identity.sqlite3")
        host = LocalGameService(identity, root, server / "resources", port=0)
        peers = []
        players = []
        receipts = [[] for _ in range(clients)]
        movements = [set() for _ in range(clients)]
        barrier = Barrier(clients)

        def read_until(index, reply_id, sequence, message_type, own_x=None):
            while True:
                head, raw = receive(peers[index])
                if head.msg_id == MsgId.PlayerSceneSyncDataNotice:
                    notice = pb.PlayerSceneSyncDataNotice.FromString(raw)
                    check(notice.status == pb.StatusCode_OK and len(notice.data) == 1,
                          "Invalid movement notice")
                    sender = notice.data[0]
                    check(len(sender.data) == 1 and len(sender.data[0].char_recorder_data_lst) == 1,
                          "Movement notice must contain exactly one record")
                    record = sender.data[0].char_recorder_data_lst[0]
                    event = (sender.player_id, record.pos.x)
                    check(record.char_id == 101001 and record.pos.x >= 10000,
                          "Movement record differs from the request")
                    check(event not in movements[index], "Duplicate movement delivery")
                    movements[index].add(event)
                    if event == (players[index], own_x):
                        return event
                elif head.msg_id == reply_id and head.packet_id == sequence:
                    reply = message_type.FromString(raw)
                    check(reply.status == pb.StatusCode_OK, f"{message_type.__name__} failed")
                    return reply

        def payload(tag, index, step):
            if tag == "SetArchiveInfo":
                return pb.SetArchiveInfoReq(key="latency", value=f"{players[index]}-{step}")
            if tag == "ChangeHead":
                return pb.ChangeHeadReq(head=HEADS[step % len(HEADS)])
            if tag == "ChangeSign":
                return pb.ChangeSignReq(sign=f"Mixed{index}x{step}")
            move = pb.PlayerSceneRecordReq()
            move.data.char_recorder_data_lst.add(char_id=101001).pos.x = x_at(index, step)
            return move

        def worker(index):
            timings = []
            barrier.wait(timeout=45)
            for step in range(rounds):
                for offset in range(len(OPERATIONS)):
                    tag = OPERATIONS[(offset + index) % len(OPERATIONS)]
                    seq = 20 + step * len(OPERATIONS) + offset
                    req = payload(tag, index, step)
                    start = perf_counter_ns()
                    peers[index].sendall(encode(getattr(MsgId, tag + "Req"), req.SerializeToString(), seq))
                    if tag == "PlayerSceneRecord":
                        event = read_until(index, None, seq, None, own_x=x_at(index, step))
                        receipts[index].append((seq, tag, *event))
                    else:
                        reply_type = getattr(pb, tag + "Rsp")
                        reply = read_until(index, getattr(MsgId, tag + "Rsp"), seq, reply_type)
                        if tag == "SetArchiveInfo":
                            check((reply.key, reply.value) == (req.key, req.value), "Archive reply differs")
                        elif tag == "ChangeHead":
                            check(reply.head == req.head, "Avatar reply differs")
                        elif tag == "ChangeSign":
                            check(reply.sign == req.sign, "Signature reply differs")
                        receipts[index].append((seq, tag, reply.SerializeToString(deterministic=True).hex()))
                    timings.append((tag, perf_counter_ns() - start))
            return timings

        host.start()
        try:
            with ExitStack() as sockets:
                for index in range(clients):
                    profile = identity.create_profile(f"Bench{index}")
                    sock, _, uid = login(host.port, identity.issue_ticket(*profile))
                    sockets.enter_context(sock)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    join(sock)
                    target = channel(index)
                    switch = pb.ChangeSceneChannelReq(scene_id=PLAZA, channel_label=target)
                    result = request(sock, MsgId.ChangeSceneChannelReq, MsgId.ChangeSceneChannelRsp,
                                     switch, pb.ChangeSceneChannelRsp, 7)
                    check(result.status == pb.StatusCode_OK and result.channel_id == target,
                          "Channel entry failed")
                    next_msg(sock, MsgId.SceneDataNotice, pb.SceneDataNotice)
                    peers.append(sock)
                    players.append(uid)

                lock = TimedLock(host.lock)
                connection = TimedConnection(db.conn)
                host.lock = lock
                db.conn = connection
                try:
                    start = monotonic()
                    with ThreadPoolExecutor(max_workers=clients) as pool:
                        samples = list(pool.map(worker, range(clients)))
                    elapsed = monotonic() - start
                    # All previous writes have been acknowledged by their sender. TCP preserves
                    # order, so the subsequent ping response is a barrier for each receiver.
                    drain_seq = 20 + rounds * len(OPERATIONS)
                    for index, sock in enumerate(peers):
                        ping = pb.PlayerPingReq(client_time_ms=1)
                        sock.sendall(encode(MsgId.PlayerPingReq, ping.SerializeToString(), drain_seq))
                        read_until(index, MsgId.PlayerPingRsp, drain_seq, pb.PlayerPingRsp)
                finally:
                    with lock.lock:
                        host.lock = lock.lock
                        db.conn = connection.connection
        finally:
            host.stop()

        events = []
        for index, seen in enumerate(movements):
            first = index // group * group
            expected = {(players[peer], x_at(peer, step))
                        for peer in range(first, first + group) for step in range(rounds)}
            check(seen == expected, f"Missing or foreign movement delivery for client {index}")
            events.extend((players[index], sender, x) for sender, x in sorted(seen))

        with closing(sqlite3.connect(root / "game.sqlite")) as saved:
            query = """
                SELECT p.player_id, a.value, p.head, p.sign, l.scene, l.channel, s.pos
                FROM players p
                JOIN private_archive a ON a.player_id = p.player_id AND a.key = 'latency'
                JOIN private_locations l ON l.player_id = p.player_id
                JOIN private_poses s ON s.player_id = p.player_id AND s.scene = ?
                ORDER BY p.player_id
            """
            rows = [(uid, value, head, sign, scene, chan, pb.Vector3.FromString(pose).x)
                    for uid, value, head, sign, scene, chan, pose in saved.execute(query, (PLAZA,))]
        step = rounds - 1
        expected_rows = [(uid, f"{uid}-{step}", HEADS[step % len(HEADS)], f"Mixed{i}x{step}",
                          PLAZA, channel(i), x_at(i, step)) for i, uid in enumerate(players)]
        check(rows == sorted(expected_rows), "Persisted player values differ")
        check(len(connection.commits) >= clients * rounds * 3, "Missing durable writes")
        by_operation = defaultdict(list)
        for timings in samples:
            for tag, duration in timings:
                by_operation[tag].append(duration)
        return {
            "clients": clients, "rounds": rounds, "members_per_channel": group,
            "elapsed_seconds": round(elapsed, 4),
            "native_rtt_ms": percentiles([v for durations in by_operation.values() for v in durations]),
            "lock_wait_ms": percentiles(lock.wait),
            "sqlite_commit_ms": percentiles(connection.commits),
            "ops_pcts_ms": {tag: percentiles(values) for tag, values in sorted(by_operation.items())},
            "verified_broadcast_deliveries": len(events), "persisted_roles": len(rows),
            "responses_sha256": digest(receipts), "broadcasts_sha256": digest(events),
            "persisted_sha256": digest(rows),
        }


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--server", type=Path, required=True, help="Unpacked server directory with app, resources and tests")
    cli.add_argument("--clients", type=int, default=24)
    cli.add_argument("--rounds", type=int, default=24)
    cli.add_argument("--group", type=int, default=6, help="Players per channel, must divide client count")
    args = cli.parse_args()
    if not 1 <= args.clients <= 512 or args.rounds < 1 or not 1 <= args.group <= args.clients:
        cli.error("Choose 1–512 clients, positive rounds, and a group size within the client count")
    if args.clients % args.group:
        cli.error("--group must divide --clients")
    if 10000 + args.clients * max(args.rounds, 1000) >= 2**24:
        cli.error("Choose fewer clients or rounds so all movement markers fit exactly in float32")
    print(json.dumps(run(args.server.resolve(), args.clients, args.rounds, args.group), sort_keys=True))
