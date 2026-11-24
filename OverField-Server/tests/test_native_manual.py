"""1.1.12 handbook list, map discovery, item credit, dungeon task count and persistence."""
import argparse
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_manual import catalog
from test_game_realm import join, login
from test_native_gacha import call
from utils import db


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profiles = [identity.create_profile(name) for name in ("手册甲", "手册乙")]
    server = LocalGameService(identity, root, resources, port=0)

    def connect(profile):
        sock, _, pid = login(server.port, identity.issue_ticket(*profile))
        join(sock)
        return sock, pid

    def read(sock):
        result = call(sock, "ManualList", 4)
        assert result.status == pb.StatusCode_OK
        return {f.flag_id: f for f in result.flags}

    def coins(pid):
        with server.lock:
            raw = db.get_item_detail(pid, 101)
            return pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0

    def discover(sock, fid):
        return call(sock, "ManualFlagUnlock", 5, flag_id=fid)

    def claim(sock, fid):
        return call(sock, "ManualFlagReward", 6, flag_id=fid)

    server.start()
    try:
        a, pid = connect(profiles[0])
        b, other = connect(profiles[1])
        with a, b:
            assert not read(a) and not read(b)
            flags = catalog()
            assert len(flags) == 120 and sum(bool(v[2]) for v in flags.values()) == 102
            assert {r[1] for r in flags.values()} == {201}
            assert discover(a, 0).status == claim(a, 0).status == pb.StatusCode_CFG_NOT_FOUND
            assert discover(a, 15006).status == pb.StatusCode_MANUAL_NOT_UNLOCK
            assert claim(a, 15006).status == pb.StatusCode_MANUAL_NOT_UNLOCK
            by_scene = defaultdict(list)
            for fid, (scene, _, _) in flags.items():
                by_scene[scene].append(fid)
            before = coins(pid)
            for scene, ids in by_scene.items():
                assert call(a, "ChangeSceneChannel", 7, scene_id=scene).status == pb.StatusCode_OK
                for fid in ids:
                    unlocked = discover(a, fid)
                    assert unlocked.status == pb.StatusCode_OK and unlocked.flag.status == pb.NotReward
                    assert unlocked.flag.flag_id == fid
            assert set(read(a)) == set(flags) and coins(pid) == before
            for fid in (15006, 15101):
                received = claim(a, fid)
                assert received.status == pb.StatusCode_OK and received.flag.status == pb.Reward
                assert [(r.main_item.item_id, r.main_item.base_item.num) for r in received.items] == [(101,1000)]
                assert discover(a, fid).flag.status == pb.Reward
                assert claim(a, fid).status == pb.StatusCode_MANUAL_REWARDED
                assert claim(b, fid).status == pb.StatusCode_MANUAL_NOT_UNLOCK
            assert coins(pid) == before + 2000 and not read(b)
            assert read(a)[15006].dungeon_task_finish_num == 0
            assert call(a, "DungeonEnter", 8, dungeon_id=11101, char1=101001).status == pb.StatusCode_OK
            for idx in (0,1,2,3,4,1):
                call(a, "MonsterDead", 9, monster_index=idx, monster_id=1014)
            assert read(a)[15006].dungeon_task_finish_num > 0
            assert call(a, "DungeonExit", 10).status == pb.StatusCode_OK
            # A real older imported role can have a discovery list or only a paid reward receipt.
            with server.lock, db.conn:
                db.conn.execute("INSERT INTO player_kv(player_id,ns,k,v) VALUES(?,'manual_flags','ids',?)",
                                (other, b"15001"))
                db.conn.execute("INSERT INTO claimed_rewards(player_id,system,key) VALUES(?,'manual_flag','15002')",
                                (other,))
    finally:
        server.stop()
    server.start()
    try:
        a, _ = connect(profiles[0])
        b, _ = connect(profiles[1])
        with a, b:
            stored = read(a)
            assert len(stored) == 120 and stored[15006].dungeon_task_finish_num > 0
            assert {k for k, f in stored.items() if f.status == pb.Reward} == {15006,15101}
            assert claim(a,15006).status == pb.StatusCode_MANUAL_REWARDED
            assert coins(pid) == before + 2000
            assert {k:f.status for k,f in read(b).items()} == {15001:pb.NotReward,15002:pb.Reward}
            assert claim(b,15002).status == pb.StatusCode_MANUAL_REWARDED
            assert claim(b,15001).status == pb.StatusCode_OK
    finally:
        server.stop()
    print("PASS 120 handbook discoveries in eight scenes, both reward paths, earned native dungeon objectives, two roles, migration and restart.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    with TemporaryDirectory() as state:
        run(Path(state), parser.parse_args().resources)
