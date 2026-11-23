"""Verify native dungeon objective notices, entry prerequisites and random equipment filters."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from test_game_realm import join, login
from private_boxes import loot
from utils import db, pb_create
from utils.res_loader import res


def call(sock, name, **fields):
    req = getattr(pb, name + "Req")(**fields)
    sock.sendall(encode(getattr(MsgId, name + "Req"), req.SerializeToString(), 29))
    changes = {}
    for _ in range(160):
        head, raw = receive(sock)
        if head.msg_id == MsgId.AchieveNotice:
            changes.update((a.achieve_id, a.count) for a in pb.AchieveNotice.FromString(raw).achieves)
        if head.msg_id == getattr(MsgId, name + "Rsp"):
            return getattr(pb, name + "Rsp").FromString(raw), changes
    raise AssertionError(name)


def run(root, resources):
    ids, expected = {1000001, 1000010, 1101107, 2111008, 1104306, 1104310}, {1101101, 1201104, 1402202, 2121006}
    identity = LocalIdentity(root / "identity.sqlite3")
    profile = identity.create_profile("目标进度测试")
    service = LocalGameService(identity, root, resources, port=0)
    service.start()
    try:
        sock, _, pid = login(service.port, identity.issue_ticket(*profile))
        with sock:
            join(sock)
            allowed = set(res["ReleasedEquipment"]["equipment_ids"])
            assert len(allowed) == 865 and expected <= allowed and not ids & allowed
            sampled = set()
            def sample(rows, count):
                sampled.update(rows)
                return list(rows[:count])
            with service.lock, db.conn, patch("private_boxes.random.sample", side_effect=sample):
                fake = SimpleNamespace(player_id=pid, scene_id=9999)
                for level in range(1, 6):
                    with patch("private_boxes.db.get_players_info", return_value=[level]):
                        assert loot(fake, 0, False)
            by_quality = {r["i_d"] for r in res["Item"]["item"]["datas"] if 0 < r.get("quality", 0) <= 5}
            assert sampled == allowed & by_quality and 1402202 in sampled
            with service.lock, db.conn:
                for _ in range(4):
                    generated = pb_create.make_treasure_box_item(pid, 3, num=3)
                    assert generated and all(i.main_item.item_id in allowed for i in generated)
            assert call(sock, "DungeonEnter", dungeon_id=120102, char1=101001)[0].status == pb.StatusCode_DungeonUnlock
            assert call(sock, "DungeonEnter", dungeon_id=120101, char1=101001)[0].status == pb.StatusCode_OK
            assert call(sock, "DungeonFinish")[0].dungeon_data.finish_times == 1
            again, changes = call(sock, "DungeonFinish")
            assert again.dungeon_data.finish_times == 1 and not changes
            assert call(sock, "DungeonRepeatEnter", char1=101001)[0].dungeon_data.enter_times == 2
            assert call(sock, "DungeonFinish")[0].dungeon_data.finish_times == 2
            call(sock, "DungeonExit")
            assert call(sock, "DungeonEnter", dungeon_id=120102, char1=101001)[0].status == pb.StatusCode_OK
            call(sock, "DungeonExit")
            call(sock, "DungeonEnter", dungeon_id=11101, char1=101001)
            scene = next(d for d in res["ScenesConfigAsset"]["Dungeons"] if d["ID"] == 11101)
            point = next(p for p in scene["MonsterInfos"] if p["UnitID"] == 1014)
            params = {"monster_index": point.get("Index", 0), "monster_id": 1014}
            _, changes = call(sock, "MonsterDead", **params)
            assert changes[20101013] == 1
            assert not call(sock, "MonsterDead", **params)[1]
            _, changes = call(sock, "DungeonFinish")
            assert changes[60000204] == 1 and not call(sock, "DungeonFinish")[1]
            assert pb.Achieve.FromString(db.get_achieve(pid, 20101013)).count == 1
        service.stop()
        service.start()
        assert pb.Achieve.FromString(db.get_achieve(pid, 20101013)).count == 1
    finally:
        service.stop()
    print("PASS native dungeon objectives, prerequisites, repeated visits, deduplication, restart and 1.1.12 equipment allowlist.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory() as temp:
        run(Path(temp), args.resources)
