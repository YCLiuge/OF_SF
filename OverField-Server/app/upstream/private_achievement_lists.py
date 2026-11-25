"""Read national area and character achievement configuration against the current private role."""
from private_dungeons import _table, progress
from private_region_devices import objectives
from proto import net_pb2 as pb
from utils import db


def area_list(session, req, rsp):
    aid = req.area_id
    links = {scene: link["area_level_i_d"] for scene,row in _table("Area","area_data").items()
             for link in row["area_data_group"] if link["area_i_d"] == aid}
    if not links:
        return pb.StatusCode_BAD_REQ
    level = 1
    if session.scene_id in links:
        owner = session.scene_id
        blob = db.get_area(session.player_id, owner, aid)
        if blob:
            level = pb.AreaData.FromString(blob).level or 1
    else:
        # Eight devices use the same IDs in scenes 3 and 6. An off-map query only supplies
        # an area ID; do not attribute one scene's divergent save to its counterpart.
        owner = next(iter(links))
        levels = set()
        for sid in links:
            blob = db.get_area(session.player_id, sid, aid)
            levels.add((pb.AreaData.FromString(blob).level or 1) if blob else 1)
        if len(set(links.values())) != 1 or len(levels) != 1:
            return pb.StatusCode_BAD_REQ
        level = levels.pop()
    rules = _table("Area","area_level_data")[links[owner]]["area_level_data_group"]
    tier = next((r for r in rules if r["level"] == level), None)
    if tier is None:
        return pb.StatusCode_CFG_NOT_FOUND
    rsp.area_id = aid
    for objective, count, _ in objectives(session.player_id, owner, aid, level):
        rsp.achieves.add(achieve_id=objective, count=count)
    return pb.StatusCode_OK


def character_list(session, req, rsp):
    cid = req.character_id
    config = _table("Character","character").get(cid)
    raw = db.get_characters(session.player_id, cid) if cid else ()
    if not config or not raw:
        return pb.StatusCode_CharNotExist
    char = pb.Character.FromString(raw[0])
    achievement = _table("Character","character_achieve").get(config.get("character_achieve_i_d"), {})
    reward = _table("Character","character_achieve_reward").get(config.get("achieve_reward_i_d"), {})
    rule = _table("Character","level_rule").get(config.get("rule_i_d"), {})
    breaks = sum(r["top_max_level"] <= char.max_level for r in rule.get("level_rule_info", ()))
    facts = {35: char.level, 87: char.star, 90: breaks}
    rsp.character_id = cid
    rsp.is_unlocked_payment = char.is_unlock_payment
    # 1.1.12 ConditionSystem dispatches field 6 to UpdateAchieve, field 3 to the character reward UI.
    badges = set(char.rewarded_achievement_id_lst)
    indexes = set(char.reward_index_lst)
    for system, collection in (("chr_badge", badges), ("chr_achieve", indexes)):
        for key, in db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system=? AND key LIKE ?",
                                    (session.player_id, system, f"{cid}:%")):
            suffix = key.split(":",1)[1]
            if 0 < len(suffix) <= 10 and suffix.isascii() and suffix.isdigit():
                collection.add(int(suffix))
    accepted_badges = set()
    for entry in achievement.get("achieve_info", ()):
        objective = entry["achieve_i_d"]
        cfg = _table("Achieve","achieve")[objective]
        target = cfg["count_param"]
        current = progress(session.player_id, objective)
        if cfg.get("param") == [cid]:
            current = max(current, facts.get(cfg.get("new_achieve_condition"), 0))
        if objective in badges:
            current = target
            accepted_badges.add(objective)
        rsp.character_achievement_lst.add(achieve_id=objective, count=min(current,target))
    rsp.rewarded_id_lst.extend(sorted(accepted_badges))
    rsp.has_rewarded_ids.extend(sorted(indexes & {r["num"] for r in reward.get("achieve_reward_info", ())}))
    return pb.StatusCode_OK
