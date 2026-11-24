"""Compare public chat recipient lookup at fixed room occupancy; run native_chat for TCP delivery."""
import argparse
from pathlib import Path
from statistics import median
import sys
from time import perf_counter_ns


def time_lookup(fn, rounds):
    start = perf_counter_ns()
    for i in range(rounds):
        fn(i % 8 + 1)
    return (perf_counter_ns() - start) / rounds / 1000


def run(server, total, rounds):
    target = "win64" if sys.platform == "win32" else "linux-x86_64"
    sys.path[:0] = [str(server / p) for p in ("app","app/upstream","vendor/common","vendor/" + target)]
    from server import scene_data

    class Member:
        def __init__(self, i):
            self.player_id = i + 1
            self.channel_id = i // 3 + 1
            self.chat_channel_id = self.channel_id
            self.indexed_nearby = self.indexed_world = None
            self.running = self.logged_in = True

    members = [Member(i) for i in range(total)]
    for member in members:
        scene_data.register_session(member)
        scene_data.sync_chat_channels(member)
    try:
        before = lambda room: [p for p in scene_data.get_session() if p.channel_id == room]
        after = lambda room: scene_data.channel_members(room, world=False)
        for room in range(1,9):
            assert before(room) == after(room)
        samples = [[],[]]
        for attempt in range(8):
            for idx in ([0,1] if attempt % 2 == 0 else [1,0]):
                samples[idx].append(time_lookup((before,after)[idx],rounds))
        print(f"{total} online, 3 per channel, {rounds} lookups per trial, median us: scan {median(samples[0]):.2f}, indexed {median(samples[1]):.2f}")
    finally:
        scene_data.clear_sessions()


if __name__ == "__main__":
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--server", type=Path, default=Path(__file__).resolve().parents[1]/"OverField-Server")
    args.add_argument("--rounds",type=int,default=5000)
    params=args.parse_args()
    if params.rounds <= 0: args.error("rounds must be positive")
    for size in (24,128,512):
        run(params.server.resolve(),size,params.rounds)
