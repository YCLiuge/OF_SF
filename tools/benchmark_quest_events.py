"""Time native NPC events on an isolated realm and verify notices and quest persistence."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns


def benchmark(server, rounds):
    platform = "win64" if sys.platform == "win32" else "linux-x86_64"
    sys.path[:0] = [str(server / p) for p in
                    ("app", "app/upstream", "tests", "vendor/common", "vendor/" + platform)]
    from local_game_service import LocalGameService, MsgId, pb
    from local_game_wire import encode, receive
    from local_identity import LocalIdentity
    from test_game_realm import join_after_intro, login
    from utils import db
    from utils.pb_create import make_Quest

    quest_id, following = 200001, 200002
    samples = {"active": [], "finished": []}
    digest = hashlib.sha256()
    with TemporaryDirectory(prefix="of-quest-bench-") as folder:
        root = Path(folder)
        identity = LocalIdentity(root / "identity.sqlite3")
        host = LocalGameService(identity, root, server / "resources", port=0)
        host.start()
        try:
            profile = identity.create_profile("QuestBench")
            client, _, uid = login(host.port, identity.issue_ticket(*profile))
            with client:
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                join_after_intro(client)
                first = make_Quest(uid, quest_id)
                req = pb.NpcTalkReq(talk_type=pb.NpcTalkReq.NpcTalkType_Npc, id=first.conditions[0].condition_id)
                for cycle in range(rounds + 1):
                    # Each active sample starts from the same stored step in this temporary realm.
                    with host.lock, db.conn:
                        db.set_quest(uid, quest_id, first.SerializeToString())
                        db.conn.execute("DELETE FROM quests WHERE player_id=? AND quest_id=?", (uid, following))
                    for offset, (case, timings) in enumerate(samples.items()):
                        number = 100 + cycle * 2 + offset
                        frame = encode(MsgId.NpcTalkReq, req.SerializeToString(), number)
                        changes = []
                        start = perf_counter_ns()
                        client.sendall(frame)
                        while True:
                            head, raw = receive(client)
                            if head.msg_id == MsgId.QuestNotice:
                                changes.extend(pb.QuestNotice.FromString(raw).quests)
                            if head.msg_id == MsgId.NpcTalkRsp and head.packet_id == number:
                                elapsed = perf_counter_ns() - start
                                reply = pb.NpcTalkRsp.FromString(raw)
                                break
                        assert reply.status == pb.StatusCode_OK
                        expected = [(quest_id, pb.QuestStatus_Finish), (following, pb.QuestStatus_InProgress)]
                        assert [(q.quest_id, q.status) for q in changes] == (expected if case == "active" else [])
                        with host.lock:
                            if case == "active":
                                for q in changes:
                                    stored = pb.Quest.FromString(db.get_quest(uid, q.quest_id))
                                    assert stored == q
                        if cycle:  # Warm the immutable resource indexes once.
                            timings.append(elapsed / 1e6)
                            for q in changes:
                                digest.update(q.SerializeToString(deterministic=True))
                            digest.update(reply.SerializeToString(deterministic=True))
        finally:
            host.stop()
    results = {"rounds": rounds, "replies_sha256": digest.hexdigest()}
    for case, values in samples.items():
        values.sort()
        results[case + "_ms"] = {key: round(values[math.ceil(frac * len(values)) - 1], 3)
                                 for key, frac in (("p50", .5), ("p95", .95))}
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, default=Path(__file__).resolve().parents[1] / "OverField-Server")
    parser.add_argument("--rounds", type=int, default=80)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("rounds must be positive")
    print(json.dumps(benchmark(args.server.resolve(), args.rounds), sort_keys=True))
