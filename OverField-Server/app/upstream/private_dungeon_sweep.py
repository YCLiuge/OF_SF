"""Material dungeon settlement for the national PC client and the community drop model."""
from collections import Counter
import random
import time

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item
from utils.res_loader import res
from private_dungeons import _table, config, credit_player_experience, finish_achievements, load, save
from private_gameplay_transaction import run


def multiplier(player, dungeon):
    row = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='dungeon_multi' AND k=?",
                          (player, str(dungeon))).fetchone()
    return int(row[0]) if row and int(row[0]) in (1, 2, 3) else 1


def preference(session, request, response):
    response.generic_msg_id = request.generic_msg_id
    if request.param_map or len(request.params) not in (1, 2) or any(
        p.param_type not in (pb.COMMON_PARAM_TYPE_NONE, pb.COMMON_PARAM_TYPE_INT) for p in request.params
    ):
        return pb.StatusCode_BAD_REQ
    did = request.params[0].int_value
    cfg = config(did)
    if not cfg or not cfg.get("is_can_sweep"):
        return pb.StatusCode_BAD_REQ
    response.params.extend(request.params)
    if len(request.params) == 1:
        response.params.add().int_value = multiplier(session.player_id, did)
    else:
        multiple = request.params[1].int_value
        if multiple not in (1, 2, 3):
            return pb.StatusCode_BAD_REQ
        db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'dungeon_multi',?,?)",
                        (session.player_id, str(did), multiple))
    return pb.StatusCode_OK


_plan_tables = None
_plans = {}


def drop_plan(cfg):
    global _plan_tables
    shows = _table("Manual","manual_show_reward")
    dungeons = _table("Dungeon","dungeon")
    items = _table("Item","item")
    published = res["ReleasedEquipment"]["equipment_ids"]
    sources = (shows, dungeons, items, published)
    if _plan_tables is None or any(a is not b for a,b in zip(sources, _plan_tables)):
        _plans.clear()
        _plan_tables = sources
    did = cfg["i_d"]
    if did not in _plans:
        _plans[did] = _build_plan(cfg, shows, dungeons, items, frozenset(published))
    return _plans[did]


def _build_plan(cfg, shows, dungeons, items, released):
    group = None
    visited = set()
    row = cfg
    while row and row["i_d"] not in visited:
        visited.add(row["i_d"])
        if group is None:
            group = shows.get(row.get("show_reward_i_d"))
        row = dungeons.get(row.get("dungeon_i_d"))
    # The PC table gives preview items. of-ps uses one to six rolls according to prerequisite depth.
    rolls = min(len(visited), 6)
    if group is None:
        return {}, (), 0
    stacks = Counter()
    equipment = []
    for prize in group["manual_reward_items"]:
        key = prize.get("item_i_d")
        if key in released:
            equipment.append(key)
        elif key in items and items[key]["new_bag_item_tag"] not in (pb.EBagItemTag_Weapon, pb.EBagItemTag_Armor) and not prize.get("need_items"):
            stacks[key] += prize.get("show_num", 1) * (1 if key in (1, 101) else rolls)
    return stacks, tuple(equipment), rolls


def settle(session, req, rsp, pending):
    pid, did = session.player_id, req.dungeon_id
    row = config(did)
    if not did or not row:
        return pb.StatusCode_BAD_REQ
    if not row.get("is_can_sweep") or session.dungeon[0]:
        return pb.StatusCode_DungeonUnlock
    level, = db.get_players_info(pid, "level")
    previous = row.get("dungeon_i_d")
    if level < row.get("need_player_level", 0) or (previous and not load(pid, previous).finish_times):
        return pb.StatusCode_DungeonUnlock
    stacks, pool, count = drop_plan(row)
    if not stacks or (set(stacks) == {1} and not pool):
        return pb.StatusCode_CFG_NOT_FOUND
    multiple = multiplier(pid, did)
    pending.balances.change(row["item_i_d"], -row["item_num"] * multiple)
    amounts = {key: quantity * multiple for key, quantity in stacks.items()}
    pending.grant(amounts, rsp.rewards)
    for _ in range(count * multiple if pool else 0):
        template = random.choice(pool)
        item = make_item(template, 1, pid, equipment_level=row["monster_lv"])
        kind = item.main_item.WhichOneof("item")
        if kind not in ("weapon", "armor"):
            return pb.StatusCode_CFG_NOT_FOUND
        key = getattr(item.main_item, kind).instance_id
        db.set_item_detail(pid, item.SerializeToString(), instance_id=key)
        rsp.rewards.add().CopyFrom(item)
        pending.pack.items.add().CopyFrom(item)
    if 1 in amounts:
        pending.notices.append((MsgId.PlayerLevelExpNotice, credit_player_experience(pid, amounts[1])))
    progress = load(pid, did)
    progress.finish_times += 1
    progress.last_finish_time = int(time.time())
    save(pid, progress)
    finish_achievements(session, did)
    return pb.StatusCode_OK


def sweep(session, request, response):
    return run(session, request, response, settle)
