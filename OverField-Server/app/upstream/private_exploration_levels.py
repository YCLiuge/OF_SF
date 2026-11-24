"""Upgrade a player's exploration abilities or a mission-complete device in their current scene."""
from collections import Counter
from proto import net_pb2 as pb
from utils import db
from private_badge_tree import _owned
from private_crafts import payments, save_pack, stack
from private_dungeons import _table, progress, report


def counted_upgrade(session, condition):
    updates = []
    for aid, row in _table("Achieve", "achieve").items():
        if row.get("new_achieve_condition") == condition and not row.get("param"):
            updates.append((aid,min(progress(session.player_id,aid) + 1, row["count_param"])))
    report(session, updates)


def ability(session, req, rsp):
    pid, aid = session.player_id, req.ability_id
    row = _table("Ability", "ability").get(aid)
    if row is None:
        return pb.StatusCode_CFG_NOT_FOUND
    raw = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='ability' AND k=?",
                          (pid,str(aid))).fetchone()
    if not raw and not _owned(pid, row["need_item_i_d"]):
        return pb.StatusCode_ABILITY_NOT_UNLOCK
    if raw and (not str(raw[0]).isdecimal() or int(raw[0]) > max(t["level"] for t in row["level_info"])):
        return pb.StatusCode_BAD_REQ
    level = int(raw[0]) if raw else 0
    rsp.ability.ability_id, rsp.ability.level = aid, level
    tiers = {r["level"]:r for r in row["level_info"]}
    dest = tiers.get(level + 1)
    if dest is None:
        return pb.StatusCode_ABILITY_LEVEL_LIMIT
    effect = dest["reward_item_i_d"]
    costs = Counter({dest["cost_item_i_d"]:dest["cost_count"]})
    old_effect = tiers[level]["reward_item_i_d"] if level else None
    has_old_effect = bool(old_effect and _owned(pid, old_effect))
    if has_old_effect and old_effect != effect:
        costs[old_effect] += 1
    details, status = payments(pid, costs)
    if status != pb.StatusCode_OK:
        return status
    target = stack(pid, effect)
    if target is None:
        return pb.StatusCode_CFG_NOT_FOUND
    if old_effect != effect or not has_old_effect:
        if target.main_item.base_item.num == 0xffffffff:
            return pb.StatusCode_BAD_REQ
        target.main_item.base_item.num += 1
        details[effect] = target
    db.conn.execute("INSERT OR REPLACE INTO player_kv(player_id,ns,k,v) VALUES(?,'ability',?,?)",
                    (pid,str(aid),level + 1))
    save_pack(session, details)
    rsp.items.extend(details.values())
    rsp.ability.level = level + 1
    counted_upgrade(session,138)
    return pb.StatusCode_OK


def area(session, req, rsp):
    pid, scene, aid = session.player_id, session.scene_id, req.area_id
    map_row = _table("Area","area_data").get(scene,{})
    cfg = next((r for r in map_row.get("area_data_group",()) if r["area_i_d"] == aid), None)
    if cfg is None or session.dungeon[0]:
        return pb.StatusCode_BAD_REQ
    blob = db.get_area(pid, scene, aid)
    if not blob:
        return pb.StatusCode_AREA_NOT_UNLOCK
    device = pb.AreaData.FromString(blob)
    rsp.area.CopyFrom(device)
    rsp.area.ClearField("items")
    if device.area_state != pb.AreaData.Unlock:
        return pb.StatusCode_AREA_NOT_UNLOCK
    level = device.level or 1
    rules = {r["level"]:r for r in _table("Area","area_level_data")[cfg["area_level_i_d"]]["area_level_data_group"]}
    if level not in rules:
        return pb.StatusCode_CFG_NOT_FOUND
    if level + 1 not in rules:
        return pb.StatusCode_AREA_LEVEL_LIMIT
    pools = _table("Area","area_mission_data")
    missions = pools[rules[level]["mission_pool_i_d"]]["area_mission_data_group"]
    achievements = _table("Achieve","achieve")
    for mission in missions:
        goal = mission.get("achieve_condition_i_d")
        if goal and progress(pid,goal) < achievements[goal]["count_param"]:
            return pb.StatusCode_AREA_LEVEL_QUEST_NOT_FINISH
    device.level = level + 1
    device.ClearField("items")
    db.set_area(pid,scene,aid,device.SerializeToString())
    rsp.area.CopyFrom(device)
    next_pool = rules[device.level].get("mission_pool_i_d")
    if next_pool:
        for mission in pools[next_pool]["area_mission_data_group"]:
            goal = mission.get("achieve_condition_i_d")
            if goal:
                rsp.achieves.add(achieve_id=goal,count=min(progress(pid,goal),achievements[goal]["count_param"]))
    counted_upgrade(session,140)
    return pb.StatusCode_OK
