"""Regional device objectives are bound to the points in each device's relation table."""
from collections import defaultdict

from private_dungeons import _table, progress, report
from proto import net_pb2 as pb
from utils import db


RELATIONS = {11: "scenic_i_d", 12: "collect_file_i_d", 13: "collect_item_i_d",
             14: "collect_book_i_d", 15: "collect_tape_i_d", 16: "collect_portal_i_d",
             17: "collect_data_i_d", 18: "challenge_i_d", 19: "riddle_i_d"}
_cache = None
# Original level-four Torii device reward display, keyed by the current level.
UPGRADE_REWARDS = {(1, 1006, 4): {102: 10, 192: 10, 1: 300}}


def show_upgrade_reward(scene, area):
    amounts = UPGRADE_REWARDS.get((scene, area.area_id, area.level))
    if amounts:
        area.ClearField("items")
        for item, quantity in amounts.items():
            area.items.add(item_id=item, num=quantity)


def catalog():
    global _cache
    areas = _table("Area", "area_data")
    if _cache and _cache[0] is areas:
        return _cache[1]
    relations = _table("Area", "area_relation_data")
    levels = _table("Area", "area_level_data")
    missions = _table("Area", "area_mission_data")
    devices, by_point, scoped_goals = {}, defaultdict(list), set()
    for scene, row in areas.items():
        for device in row["area_data_group"]:
            aid = device["area_i_d"]
            points = relations[device["relevance_pool_i_d"]]["area_relation_data_group"]
            grouped = {condition: tuple(sorted({p[field] for p in points if p.get(field)}))
                       for condition, field in RELATIONS.items()}
            tiers = {}
            for tier in levels[device["area_level_i_d"]]["area_level_data_group"]:
                entries = missions.get(tier.get("mission_pool_i_d"), {}).get("area_mission_data_group", ())
                goals = tuple(e["achieve_condition_i_d"] for e in entries if e.get("achieve_condition_i_d"))
                tiers[tier["level"]] = goals
                scoped_goals.update(goals)
            devices[scene, aid] = (grouped, tiers)
            for condition, identifiers in grouped.items():
                for identifier in identifiers:
                    by_point[scene, condition, identifier].append(aid)
    _cache = (areas, (devices, by_point, scoped_goals))
    return _cache[1]


def completed(pid, scene, condition, ids):
    if not ids:
        return 0
    slots = ",".join("?" for _ in ids)
    if 12 <= condition <= 17:
        return db.conn.execute(
            "SELECT COUNT(*) FROM collections WHERE player_id=? AND collection_type=? AND item_id IN (" +
            slots + ")", (pid, condition - 11, *ids)).fetchone()[0]
    if condition == 11:
        return db.conn.execute(
            "SELECT COUNT(*) FROM player_kv WHERE player_id=? AND ns=? AND k IN (" + slots + ")",
            (pid, f"scenic_spots:{scene}", *(str(i) for i in ids))).fetchone()[0]
    if condition == 18:
        rows = db.conn.execute(
            "SELECT challenge_blob FROM challenges WHERE player_id=? AND scene_id=? AND challenge_id IN (" +
            slots + ")", (pid, scene, *ids))
        return sum(pb.ChallengeData.FromString(blob).state == pb.Finish for (blob,) in rows)
    if condition == 19:
        return db.conn.execute(
            "SELECT COUNT(*) FROM realm_field_activities WHERE player=? AND scene=? AND kind=1 AND wins>0 AND id IN (" +
            slots + ")", (pid, scene, *ids)).fetchone()[0]
    return 0


def objectives(pid, scene, aid, level):
    grouped, tiers = catalog()[0][scene, aid]
    achievements = _table("Achieve", "achieve")
    totals = {}
    saved = dict(db.conn.execute("SELECT k,v FROM player_kv WHERE player_id=? AND ns=?",
                                 (pid,f"import_area_goals:{scene}")))
    for goal in tiers.get(level, ()):
        rule = achievements[goal]
        kind, target = rule["new_achieve_condition"], rule["count_param"]
        if kind not in totals:
            totals[kind] = completed(pid, scene, kind, grouped.get(kind, ()))
        # Older realms confused area and scene IDs 1001–1003 when crediting collections.
        # These counters cannot prove a device discovery; use its points or scoped archive.
        historical = 0 if aid in _table("Scene","scene") and 12 <= kind <= 17 else progress(pid,goal)
        yield goal, min(target,max(historical,totals[kind],int(saved.get(str(goal),0)))), target


def changed(session, condition, point):
    devices, by_point, _ = catalog()
    pid, scene = session.player_id, session.scene_id
    for aid in by_point.get((scene, condition, point), ()):
        raw = db.get_area(pid, scene, aid)
        level = (pb.AreaData.FromString(raw).level or 1) if raw else 1
        goals = devices[scene, aid][1].get(level, ())
        rules = _table("Achieve", "achieve")
        matching = [g for g in goals if rules[g]["new_achieve_condition"] == condition]
        if matching:
            count = completed(pid, scene, condition, devices[scene, aid][0][condition])
            report(session, ((g, min(count, rules[g]["count_param"])) for g in matching))


def toggle(session, aid, state, rsp):
    from private_bottle_intro import SCENE, set_device as tutorial_device
    from private_story_entry import SAKURA, SMALL_PLAZA_DEVICE, set_device as plaza_device
    from private_map_progress import device_opened, rebuild
    scene = session.scene_id
    if scene == SCENE:
        return tutorial_device(session, aid, state, rsp)
    if scene == SAKURA and aid == SMALL_PLAZA_DEVICE:
        return plaza_device(session, aid, state, rsp)
    if session.dungeon[0] or (scene, aid) not in catalog()[0]:
        return pb.StatusCode_BAD_REQ
    pid = session.player_id
    rebuild(pid)
    raw = db.get_area(pid, scene, aid)
    device = pb.AreaData.FromString(raw) if raw else pb.AreaData(area_id=aid)
    if not raw and state == pb.AreaData.Close:
        return pb.StatusCode_AREA_NOT_UNLOCK
    device.ClearField("items")
    device.level = device.level or 1
    if not raw or device.area_state != state:
        device.area_state = state
        db.set_area(pid, scene, aid, device.SerializeToString())
    rsp.area.CopyFrom(device)
    show_upgrade_reward(scene, rsp.area)
    if state == pb.AreaData.Unlock:
        device_opened(session, aid)
    return pb.StatusCode_OK
