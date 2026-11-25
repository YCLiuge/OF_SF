"""Persistent unlocked difficulty tiers for the native world-level menu."""
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res


def state(player):
    captain, current = db.get_players_info(player, "level,world_level")
    row = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='world_level' AND k='highest'",
                          (player,)).fetchone()
    # Earlier saves stored the selected tier only. Preserve it before lowering difficulty.
    return captain, current, max(current, int(row[0]) if row else 1)


def remember(player, highest):
    db.conn.execute("INSERT OR REPLACE INTO player_kv(player_id,ns,k,v) VALUES(?,'world_level','highest',?)",
                    (player, highest))


def objectives(player, captain, config):
    for row in config.get("world_level_info", ()):
        aid = row.get("achieve_i_d")
        if not aid:
            continue
        rule = res.index("Achieve", "achieve")[aid]
        raw = db.get_achieve(player, aid)
        count = pb.Achieve.FromString(raw).count if raw else 0
        if rule["new_achieve_condition"] == 36:
            count = max(count, captain)
        elif rule["new_achieve_condition"] == 6 and len(rule.get("param", ())) == 1:
            dungeon = db.get_dungeon(player, rule["param"][0])
            if dungeon:
                count = max(count, pb.DungeonData.FromString(dungeon).finish_times)
        goal = rule["count_param"]
        yield aid, min(count, goal), goal


def list_levels(session, req, rsp):
    captain, _, highest = state(session.player_id)
    for tier, row in sorted(res.index("Level", "world_level").items()):
        if tier <= highest:
            rsp.unlock_world_levels.append(tier)
        for aid, count, _ in objectives(session.player_id, captain, row):
            rsp.achieves.add(achieve_id=aid, count=count)
    return pb.StatusCode_OK


def unlock(session, req, rsp):
    player, tier = session.player_id, req.world_level
    captain, current, highest = state(player)
    rsp.world_level = current
    config = res.index("Level", "world_level").get(tier)
    if not config:
        return pb.StatusCode_BAD_REQ
    if tier <= highest:
        return pb.StatusCode_OK
    if config.get("level", 1) > highest or tier != highest + 1:
        return pb.StatusCode_WORLD_LEVEL_LOCKED
    if any(count < goal for _, count, goal in objectives(player, captain, config)):
        return pb.StatusCode_WORLD_LEVEL_ACHIEVE_NOT_FINISH
    remember(player, tier)
    db.set_players_info(player, "world_level", tier)
    rsp.world_level = tier
    return pb.StatusCode_OK


def change(session, req, rsp):
    player, tier = session.player_id, req.world_level
    _, current, highest = state(player)
    rsp.world_level = current
    if tier not in res.index("Level", "world_level"):
        return pb.StatusCode_BAD_REQ
    if tier > highest:
        return pb.StatusCode_WORLD_LEVEL_LOCKED
    if tier == current:
        return pb.StatusCode_WORLD_LEVEL_NOW_IN
    remember(player, highest)
    db.set_players_info(player, "world_level", tier)
    rsp.world_level = tier
    return pb.StatusCode_OK
