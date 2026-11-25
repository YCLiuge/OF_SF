"""Replay field and dungeon movement through the real pose checkpoint path."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import statistics
import sys
from time import perf_counter_ns
from types import SimpleNamespace
from unittest.mock import patch


def run(server, players, frames_per_checkpoint, checkpoints, repeats):
    platform = "win64" if sys.platform == "win32" else "linux-x86_64"
    sys.path[:0] = [str(server / path) for path in
                    ("app", "app/upstream", "vendor/" + platform, "vendor/common")]
    from local_game_service import LocalGameService, pb
    from utils import db

    intervals = []
    serializations = 0
    original = pb.Vector3.SerializeToString

    def count_serializations(vector, *args, **kwargs):
        nonlocal serializations
        serializations += 1
        return original(vector, *args, **kwargs)

    records = []
    for tick in range(frames_per_checkpoint * checkpoints):
        actor = pb.PlayerSceneRecordReq().data.char_recorder_data_lst.add(char_id=101001)
        if tick % 3 != 1:
            actor.pos.x = tick + 10
        if tick % 3 != 0:
            actor.rot.y = tick + 20
        records.append(actor)
    last_position = max(rec.pos.x for rec in records)
    last_rotation = max(rec.rot.y for rec in records)

    def replay():
        with closing(sqlite3.connect(":memory:")) as conn, patch.object(db, "conn", conn):
            conn.executescript("""
                CREATE TABLE private_poses(player_id INTEGER,scene INTEGER,pos BLOB,rot BLOB,
                    PRIMARY KEY(player_id,scene));
                CREATE TABLE private_dungeon_poses(player INTEGER,dungeon INTEGER,visit INTEGER,
                    pos BLOB,rot BLOB,PRIMARY KEY(player,dungeon));
            """)
            host = LocalGameService.__new__(LocalGameService)
            host.pending_poses, host.pending_dungeon_poses = {}, {}
            roles = []
            for index in range(players):
                inside = index % 2
                player = index + 1000001
                scene_player = pb.ScenePlayer()
                role = SimpleNamespace(player_id=player, scene_id=9999, pos={}, pose_checkpoint=None,
                    scene_player=scene_player, dungeon=[11101 if inside else 0, 0, 0, -1],
                    dungeon_visit=1, dungeon_lead=pb.ScenePlayer().team.char1)
                roles.append(role)
                if inside:
                    conn.execute("INSERT INTO private_dungeon_poses VALUES(?,?,1,X'',X'')",
                                 (player, 11101))
            conn.commit()
            initial_changes = conn.total_changes
            start = perf_counter_ns()
            for ix, record in enumerate(records, 1):
                for role in roles:
                    host._movement(role, record)
                if ix % frames_per_checkpoint == 0:
                    host._flush_poses()
            elapsed = perf_counter_ns() - start
            if conn.total_changes - initial_changes != players * checkpoints:
                raise RuntimeError("Unexpected number of persisted pose updates")
            all_rows = conn.execute("SELECT pos,rot FROM private_poses UNION ALL "
                                    "SELECT pos,rot FROM private_dungeon_poses").fetchall()
            if len(all_rows) != players or any(
                    (pb.Vector3.FromString(pos).x, pb.Vector3.FromString(rot).y) != (last_position,last_rotation)
                    for pos, rot in all_rows):
                raise RuntimeError("Final position or rotation differs from the last input")
            return elapsed

    with patch.object(pb.Vector3, "SerializeToString", count_serializations):
        replay()
    for _ in range(repeats):
        intervals.append(replay())
    total = players * len(records)
    return {"players":players, "frames_per_checkpoint":frames_per_checkpoint,
            "movement_updates":total, "pose_rows_written":players * checkpoints,
            "vector_serializations":serializations, "positions_verified":players,
            "median_ms":round(statistics.median(intervals)/1e6,3),
            "ns_per_update":round(statistics.median(intervals)/total,1)}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--server", required=True, type=Path)
    cli.add_argument("--players",type=int,default=24)
    cli.add_argument("--frames-per-checkpoint",type=int,default=30)
    cli.add_argument("--checkpoints",type=int,default=80)
    cli.add_argument("--repeats",type=int,default=7)
    args = cli.parse_args()
    if min(args.players,args.frames_per_checkpoint,args.checkpoints,args.repeats)<1:
        cli.error("All workload dimensions must be positive")
    print(json.dumps(run(args.server.resolve(),args.players,args.frames_per_checkpoint,
                         args.checkpoints,args.repeats),sort_keys=True))
