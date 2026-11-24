"""Verify every exploration ability tier and a full map-device chain over native TCP."""
import argparse
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_dungeons import _table
from test_game_realm import join, login
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    roles = [identity.create_profile(name) for name in ("探索甲", "探索乙")]
    host = LocalGameService(identity, root, resources, port=0)

    def connect(index):
        sock, _, pid = login(host.port, identity.issue_ticket(*roles[index]))
        join(sock)
        return sock, pid

    def fund(pid, item, quantity):
        db.set_item_detail(pid, make_item(item, quantity, pid).SerializeToString(), item)

    def balance(pid, item):
        with host.lock:
            raw = db.get_item_detail(pid, item)
            return pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0

    def list_levels(sock):
        return {r.ability_id: r.level for r in call(sock, "PlayerAbilityList", 60).ability_list}

    def area(sock, seq):
        return call(sock, "AreaLevelUp", seq, area_id=1001)

    host.start()
    try:
        a, pid = connect(0)
        b, peer = connect(1)
        with a, b:
            rows = _table("Ability", "ability")
            first = rows[1010]["level_info"][0]
            costs = Counter()
            for row in rows.values():
                for tier in row["level_info"]:
                    costs[tier["cost_item_i_d"]] += tier["cost_count"]
            assert call(b, "PlayerAbilityLevelUp", 61, ability_id=1010).status == pb.StatusCode_ABILITY_NOT_UNLOCK
            with host.lock, db.conn:
                for row in rows.values():
                    fund(pid, row["need_item_i_d"], 1)
                fund(pid, first["cost_item_i_d"], 0)
            assert call(a, "PlayerAbilityLevelUp", 62, ability_id=1010).status == pb.StatusCode_ITEM_NOT_ENOUGH
            assert set(list_levels(a)) == set(rows)
            with host.lock, db.conn:
                for item, quantity in costs.items():
                    fund(pid, item, quantity)
            seq = 100
            for aid, row in rows.items():
                prev = None
                for tier in row["level_info"]:
                    item, cost, effect = tier["cost_item_i_d"], tier["cost_count"], tier["reward_item_i_d"]
                    before = balance(pid, item)
                    before_effect = balance(pid, effect)
                    old = balance(pid, prev) if prev is not None else None
                    result = call(a, "PlayerAbilityLevelUp", seq, ability_id=aid)
                    assert (result.status, result.ability.level) == (pb.StatusCode_OK, tier["level"]), (aid, tier, result)
                    if seq == 100:
                        assert call(a, "PlayerAbilityLevelUp", seq, ability_id=aid) == result
                    assert balance(pid, item) == before - cost
                    assert balance(pid, effect) == before_effect + int(prev != effect)
                    if prev and prev != effect:
                        assert balance(pid, prev) == old - 1
                    prev = effect
                    seq += 1
                assert call(a, "PlayerAbilityLevelUp", 90, ability_id=aid).status == pb.StatusCode_ABILITY_LEVEL_LIMIT
            assert all(balance(pid, item) == 0 for item in costs)
            assert not list_levels(b)
            # An older realm could retain a learned tier without its inventory effect.
            with host.lock, db.conn:
                db.conn.execute("INSERT INTO player_kv VALUES(?,'ability','1010',1)", (peer,))
                fund(peer, first["reward_item_i_d"], 0)
            legacy = call(b, "PlayerAbilityLevelUp", 499, ability_id=1010)
            assert legacy.status == pb.StatusCode_OK and legacy.ability.level == 2
            assert balance(peer, rows[1010]["level_info"][1]["reward_item_i_d"]) == 1
            assert call(a, "ChangeSceneChannel", 501, scene_id=1).status == pb.StatusCode_OK
            assert area(a, 502).status == pb.StatusCode_AREA_NOT_UNLOCK
            assert call(b, "ChangeSceneChannel", 501, scene_id=1).status == pb.StatusCode_OK
            with host.lock, db.conn:
                state = pb.AreaData(area_id=1001, level=1, area_state=pb.AreaData.Unlock)
                db.set_area(pid, 1, 1001, state.SerializeToString())
            assert area(a, 503).status == pb.StatusCode_AREA_LEVEL_QUEST_NOT_FINISH
            rules = _table("Area", "area_level_data")[1001]["area_level_data_group"]
            pools = _table("Area", "area_mission_data")
            achieves = _table("Achieve", "achieve")
            for tier in rules[:-1]:
                missions = pools[tier["mission_pool_i_d"]]["area_mission_data_group"]
                goals = [r["achieve_condition_i_d"] for r in missions if r.get("achieve_condition_i_d")]
                def record(goal, count):
                    db.set_achieve(pid, goal, pb.Achieve(achieve_id=goal, count=count).SerializeToString())
                with host.lock, db.conn:
                    for goal in goals:
                        record(goal, achieves[goal]["count_param"])
                    last = goals[-1]
                    record(last, achieves[last]["count_param"] - 1)
                assert area(a, 504).status == pb.StatusCode_AREA_LEVEL_QUEST_NOT_FINISH
                with host.lock, db.conn:
                    record(last, achieves[last]["count_param"])
                reply = area(a, 510 + tier["level"])
                assert reply.status == pb.StatusCode_OK and reply.area.level == tier["level"] + 1
                assert reply == area(a, 510 + tier["level"])
                listing = call(a, "AreaAchieveList", 520, area_id=1001)
                assert {r.achieve_id for r in listing.achieves} == {r.achieve_id for r in reply.achieves}
            assert area(a, 530).status == pb.StatusCode_AREA_LEVEL_LIMIT
            assert area(b, 531).status == pb.StatusCode_AREA_NOT_UNLOCK
    finally:
        host.stop()
    host.start()
    try:
        a, pid = connect(0)
        with a:
            assert list_levels(a) == {aid: len(row["level_info"]) for aid, row in rows.items()}
            assert call(a, "ChangeSceneChannel", 540, scene_id=1).status == pb.StatusCode_OK
            assert area(a, 541).status == pb.StatusCode_AREA_LEVEL_LIMIT
    finally:
        host.stop()
    print(f"PASS {len(rows)} abilities, {seq - 100} priced tiers, a five-level map device, retry, isolation and restart.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp), parser.parse_args().resources)
