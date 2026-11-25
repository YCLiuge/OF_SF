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
                    db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'import_area_goals:1',?,?)",
                                    (pid,str(goal),str(count)))
                with host.lock, db.conn:
                    for goal in goals:
                        record(goal, achieves[goal]["count_param"])
                    last = goals[-1]
                    record(last, achieves[last]["count_param"] - 1)
                    if achieves[last]["new_achieve_condition"] in range(12,18):
                        # Legacy global counters from scene 1001 must not satisfy device 1001.
                        db.set_achieve(pid,last,pb.Achieve(achieve_id=last,count=999).SerializeToString())
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
            def torii(name, seq):
                return call(b, name, seq, area_id=1006)

            opened = torii("AreaUnlock", 600)
            assert opened.status == pb.StatusCode_OK and opened.area.level == 1
            assert dict((r.achieve_id, r.count) for r in torii("AreaAchieveList", 601).achieves)[10612] == 0
            assert call(b, "RiddleStateUpdate", 602, riddle_id=400003, battle_state=pb.Start).status == pb.StatusCode_OK
            win = call(b, "RiddleStateUpdate", 603, riddle_id=400003, battle_state=pb.Finish, is_win=True)
            assert win.status == pb.StatusCode_OK and win.dynamic_treasure_box_base_info.index
            assert dict((r.achieve_id, r.count) for r in torii("AreaAchieveList", 604).achieves)[10612] == 1
            assert call(b, "ChallengeStateUpdate", 605, challenge_id=200003, battle_state=pb.Start).status == pb.StatusCode_OK
            assert call(b, "ChallengeStateUpdate", 606, challenge_id=200003, battle_state=pb.Finish, use_time=30000).status == pb.StatusCode_OK
            assert dict((r.achieve_id, r.count) for r in torii("AreaAchieveList", 607).achieves)[10611] == 1
            # A returning player's Torii device already reached level four before import.
            with host.lock, db.conn:
                device = pb.AreaData(area_id=1006, level=4, area_state=pb.AreaData.Unlock)
                db.set_area(peer, 1, 1006, device.SerializeToString())
            items = [10201027, 10201021, 10201026, 10401017, 10401018, 10401010,
                     10601003, 10601002, 10601012, 10601022]
            for index, item in enumerate(items):
                assert call(b, "Collecting", 610 + index, item_id=item).status == pb.StatusCode_OK
            # The corresponding item at the next device does not count.
            assert call(b, "Collecting", 621, item_id=10201013).status == pb.StatusCode_OK
            assert {v.achieve_id:v.count for v in torii("AreaAchieveList", 622).achieves} == {10641:3,10642:3,10643:4}
            locked = torii("AreaLevelUp", 623)
            assert locked.status == pb.StatusCode_AREA_LEVEL_QUEST_NOT_FINISH and not locked.items
            assert {i.item_id:i.num for i in locked.area.items} == {102:10,192:10,1:300}
            for index, item in enumerate((10101017,10101004,10301002,10301010,10501014,10501006)):
                assert call(b, "Collecting", 640 + index, item_id=item).status == pb.StatusCode_OK
            assert call(b, "RiddleStateUpdate", 650, riddle_id=100002, battle_state=pb.Start).status == pb.StatusCode_OK
            assert call(b, "RiddleStateUpdate", 651, riddle_id=100002, battle_state=pb.Finish, is_win=True).status == pb.StatusCode_OK
            from private_region_devices import catalog, completed
            points = catalog()[0][1,1006][0]
            with host.lock:
                assert sum(map(len,points.values())) == 20
                assert sum(completed(peer,1,kind,ids) for kind,ids in points.items()) == 19
            assert call(b, "Collecting", 624, item_id=10201001).status == pb.StatusCode_OK
            before = {item:balance(peer,item) for item in (102,192,1)}
            promoted = torii("AreaLevelUp", 625)
            assert promoted.status == pb.StatusCode_OK and promoted.area.level == 5 and not promoted.area.items
            assert promoted == torii("AreaLevelUp", 625)
            expected = {102:10,192:10,1:300}
            assert {i.main_item.item_id:i.main_item.base_item.num for i in promoted.items} == expected
            assert {item:balance(peer,item)-old for item,old in before.items()} == expected
            with host.lock:
                assert sum(completed(peer,1,kind,ids) for kind,ids in points.items()) == 20
            assert torii("AreaLevelUp", 626).status == pb.StatusCode_AREA_LEVEL_LIMIT
            assert torii("AreaClose", 627).area.level == 5 and torii("AreaUnlock", 628).area.level == 5
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
    print(f"PASS {len(rows)} abilities, {seq - 100} priced tiers, scoped Torii device goals, Find Statue, "
          "fourth collectible, one-time level-five bundle, legacy scope, retry, isolation and restart.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp), parser.parse_args().resources)
