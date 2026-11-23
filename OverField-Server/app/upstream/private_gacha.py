"""Native recruiting. The game host commits currency, roster, results and history together."""
from collections import OrderedDict
import json
import secrets

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item
from private_dungeons import _table
from private_first_quest import STEPS, saved
import private_tutorial_recruit as tutorial
from realm_gacha import SCHEMA, defaults


def setup():
    db.conn.execute("CREATE TABLE IF NOT EXISTS authority.private_gacha_config " + SCHEMA)
    db.conn.execute("""CREATE TABLE IF NOT EXISTS private_gacha_picks (
        player INTEGER NOT NULL, pool INTEGER NOT NULL, item INTEGER NOT NULL,
        PRIMARY KEY(player,pool)) WITHOUT ROWID""")
    db.conn.execute("""CREATE TABLE IF NOT EXISTS private_gacha_choices (
        player INTEGER PRIMARY KEY, selected INTEGER NOT NULL)""")
    db.conn.execute("CREATE INDEX IF NOT EXISTS private_gacha_history ON gacha_record(player_id,gacha_id,id)")


def active():
    row = db.conn.execute("SELECT settings FROM authority.private_gacha_config WHERE id=1").fetchone()
    config = json.loads(row[0]) if row else defaults()
    return {p["id"]: p for p in config["pools"] if p["enabled"]}


def onboarding(session):
    """Only the active 'recruit one teammate' objective diverts a plaza draw to the lesson."""
    if session.scene_id != 9999 or session.dungeon[0]:
        return False
    step = saved(session.player_id, STEPS[-1])
    return bool(step and step.status == pb.QuestStatus_InProgress and len(step.conditions) == 1
                and step.conditions[0].condition_id == tutorial.CONDITIONS[-1])


def info(pid, pool):
    key = pool["id"]
    count = db.get_total_gacha_num(pid, key)
    picked = db.conn.execute("SELECT 1 FROM private_gacha_picks WHERE player=? AND pool=?", (pid, key)).fetchone()
    return pb.GachaInfo(gacha_id=key, gacha_times=count, has_full_pick=bool(picked),
                        guarantee=max(1, pool["pity"] - pity_misses(pid, pool)))


CHOICE = 9000


def choices(pools):
    allowed = _table("Gacha", "info")[CHOICE]["card_pool_range"]
    return [key for key in allowed if key in pools]


def selected(pid, pools):
    stored = db.conn.execute("SELECT selected FROM private_gacha_choices WHERE player=?", (pid,)).fetchone()
    return stored[0] if stored and stored[0] in choices(pools) else 0


def choice_info(pid, pools):
    pick = selected(pid, pools)
    result = pb.GachaInfo(gacha_id=CHOICE, choose_pool_id=pick, gacha_times=db.get_total_gacha_num(pid, CHOICE))
    if pick:
        result.guarantee = max(1, pools[pick]["pity"] - db.get_gacha_guarantee(pid, pick))
    return result


def choose(session, req, rsp):
    pools = active()
    if onboarding(session) or req.gacha_id != CHOICE or req.choose_gacha_pool not in choices(pools):
        return pb.StatusCode_BAD_REQ
    db.conn.execute("INSERT OR REPLACE INTO private_gacha_choices VALUES(?,?)",
                    (session.player_id, req.choose_gacha_pool))
    rsp.gacha_id, rsp.choose_gacha_pool = CHOICE, req.choose_gacha_pool
    return pb.StatusCode_OK


def list_pools(session, req, rsp):
    if onboarding(session):
        return tutorial.list_pools(session, req, rsp)
    pools = active()
    for pool in pools.values():
        rsp.gachas.add().CopyFrom(info(session.player_id, pool))
    if choices(pools):
        rsp.gachas.add().CopyFrom(choice_info(session.player_id, pools))
    return pb.StatusCode_OK


def outcome(pool, misses, guarantee_group, randbelow=secrets.randbelow):
    if misses + 1 >= pool["pity"]:
        group = guarantee_group
    else:
        roll = randbelow(10000)
        for key, weight in pool["weights"].items():
            roll -= weight
            if roll < 0:
                group = int(key)
                break
        else:
            raise ValueError("Invalid recruit group weights")
    members = _table("Gacha", "reward_pool")[group]["items"]
    item = members[randbelow(len(members))]["item_i_d"]
    return item, 0 if group == guarantee_group else misses + 1


def explorer_at_least(item, quality):
    row = _table("Item", "item").get(item, {})
    return row.get("new_bag_item_tag") == pb.EBagItemTag_Card and row.get("quality", 0) >= quality


def recent_misses(pid, limit, quality):
    # Use committed draw history so existing saves have the same counter as newly created ones.
    results = db.conn.execute("SELECT item_id FROM gacha_record WHERE player_id=? AND gacha_id=2000 "
                              "ORDER BY id DESC LIMIT ?", (pid, limit))
    misses = 0
    for (item,) in results:
        if explorer_at_least(item, quality):
            break
        misses += 1
    return misses


def pity_misses(pid, pool):
    key = pool["id"]
    if key != 2000:
        return db.get_gacha_guarantee(pid, key)
    row = db.conn.execute("SELECT guarantee FROM gacha_guarantee WHERE player_id=? AND gacha_id=2000", (pid,)).fetchone()
    return row[0] if row else recent_misses(pid, pool["pity"] - 1, 4)


def extra(pid, item_id, amount, delta, notice):
    raw = db.get_item_detail(pid, item_id)
    stack = pb.ItemDetail.FromString(raw) if raw else make_item(item_id, 0, pid)
    if stack.main_item.WhichOneof("item") != "base_item" or stack.main_item.base_item.num + amount > 0xffffffff:
        raise ValueError("Invalid recruit bonus balance")
    stack.main_item.base_item.num += amount
    db.set_item_detail(pid, stack.SerializeToString(), item_id)
    notice.items.add().CopyFrom(stack)
    reward = delta.extras.add()
    reward.CopyFrom(stack.main_item)
    reward.base_item.num = amount


def duplicate_bonus(pid, prefix, quality, delta, notice):
    rule = _table("GlobalConfig", "config", "key")[f"Gacha_{prefix}Quality{quality}_Reward"]
    for value in rule["values"]:
        item_id, amount = map(int, value.split(":"))
        extra(pid, item_id, amount, delta, notice)


def owned_posters(pid):
    # History preserves discovery if the original instance was later consumed.
    posters = {item for (item,) in db.conn.execute("SELECT DISTINCT item_id FROM gacha_record WHERE player_id=?", (pid,))}
    for raw in db.get_item_detail(pid, table="items_s"):
        item = pb.ItemDetail.FromString(raw).main_item
        if item.WhichOneof("item") == "poster":
            posters.add(item.item_id)
    return posters


def award(pid, item_id, delta, notice, posters=None):
    row = _table("Item", "item")[item_id]
    item = make_item(item_id, 1, pid)
    delta.CopyFrom(item)
    # Only a real random pull receives the appearance vouchers listed in string_gacha (PC 1.1.12).
    recruit = posters is not None
    quality = row.get("quality", 0)
    if row["new_bag_item_tag"] == pb.EBagItemTag_Poster:
        delta.main_item.is_new = not recruit or item_id not in posters
        db.set_item_detail(pid, delta.SerializeToString(), instance_id=delta.main_item.poster.instance_id)
        notice.items.add().CopyFrom(delta)
        if recruit:
            if item_id in posters:
                duplicate_bonus(pid, "Poster_", quality, delta, notice)
            posters.add(item_id)
            if quality in (3, 4):
                extra(pid, 107, {3: 50, 4: 500}[quality], delta, notice)
        return
    if row["new_bag_item_tag"] != pb.EBagItemTag_Card:
        raise ValueError("Unsupported recruit item type")
    config = _table("Character", "character")[item_id]
    owned = db.get_characters(pid, item_id)
    if owned:
        delta.main_item.character.ParseFromString(owned[0])
        max_star = delta.main_item.character.star >= 5
        if not recruit or not max_star:
            extra(pid, config["repeat_item_i_d"], 3 if recruit and quality in (3, 4) else 1, delta, notice)
        if recruit:
            duplicate_bonus(pid, "Character_FullStar" if max_star else "Character_", quality, delta, notice)
            if quality in (3, 4):
                extra(pid, 107, {3: 500, 4: 1500}[quality], delta, notice)
        return
    outfit_ids = {config.get("hat_i_d"), config.get("hair_i_d"), config.get("cloth_i_d"),
                  *(config.get("orn_i_d") or [])} - {None, 0}
    previous = {item: db.get_item_detail(pid, item) for item in outfit_ids}
    char = db.add_character(pid, item_id, delta.main_item.character)
    delta.main_item.is_new = True
    notice.items.add().CopyFrom(delta)
    notice.items.add().ParseFromString(db.get_item_detail(pid, instance_id=char.equipment_presets[0].weapon))
    for outfit in outfit_ids:
        if previous[outfit]:
            db.set_item_detail(pid, previous[outfit], outfit)
        else:
            notice.items.add().ParseFromString(db.get_item_detail(pid, outfit))
    if recruit and quality in (3, 4):
        extra(pid, 107, {3: 500, 4: 1500}[quality], delta, notice)


def draw(session, req, rsp, packet_id):
    lesson = onboarding(session)
    cache = getattr(session, "gacha_receipts", None)
    if cache is None:
        cache = session.gacha_receipts = OrderedDict()
    request = (packet_id, req.gacha_id, req.is_single)
    if packet_id and request in cache:
        prior_lesson, raw = cache[request]
        if not lesson or prior_lesson:
            rsp.ParseFromString(raw)
            return pb.StatusCode_OK
    status = tutorial.draw(session, req, rsp) if lesson else regular_draw(session, req, rsp)
    if status == pb.StatusCode_OK and packet_id:
        rsp.status = status
        cache[request] = (lesson, rsp.SerializeToString())
        if len(cache) > 64:
            cache.popitem(last=False)
    return status


def regular_draw(session, req, rsp):
    pools = active()
    pool_id = selected(session.player_id, pools) if req.gacha_id == CHOICE else req.gacha_id
    pool = pools.get(pool_id)
    if pool is None or session.dungeon[0]:
        return pb.StatusCode_BAD_REQ
    pid = session.player_id
    config = _table("Gacha", "info")[req.gacha_id]
    key = "1" if req.is_single else "2"
    count = config["method" + key + "_count"]
    ticket_id, price = config["consume_item" + key + "_i_d"], config["consume_item" + key + "_num"]
    raw = db.get_item_detail(pid, ticket_id)
    ticket = pb.ItemDetail.FromString(raw) if raw else None
    if req.gacha_id == CHOICE and db.get_total_gacha_num(pid, CHOICE) + count > config["max_times"]:
        return pb.StatusCode_BAD_REQ
    if not ticket or ticket.main_item.WhichOneof("item") != "base_item" or ticket.main_item.base_item.num < price:
        return pb.StatusCode_ITEM_NOT_ENOUGH
    notice = pb.PackNotice(status=pb.StatusCode_OK)
    ticket.main_item.base_item.num -= price
    db.set_item_detail(pid, ticket.SerializeToString(), ticket_id)
    notice.items.add().CopyFrom(ticket)
    misses = pity_misses(pid, pool)
    reward_config = _table("Gacha", "info")[pool_id]
    posters = owned_posters(pid)
    sr_limit = pool.get("sr_pity", 10) if pool_id == 2000 else 0
    since_sr = recent_misses(pid, sr_limit - 1, 3) if sr_limit else 0
    for _ in range(count):
        item, misses = outcome(pool, misses, reward_config["big_guarantee_pool_i_d"])
        if sr_limit:
            if not explorer_at_least(item, 3) and since_sr + 1 >= sr_limit:
                # Preserve an SSR explorer rolled by the base pool or hard pity on this exact pull.
                item = secrets.choice(_table("Gacha", "reward_pool")[300]["items"])["item_i_d"]
            since_sr = 0 if explorer_at_least(item, 3) else since_sr + 1
        award(pid, item, rsp.items.add(), notice, posters)
        db.add_gacha_record(pid, req.gacha_id, item)
    db.set_gacha_guarantee(pid, pool_id, misses)
    rsp.info.CopyFrom(choice_info(pid, pools) if req.gacha_id == CHOICE else info(pid, pool))
    session.send(MsgId.PackNotice, notice, 0)
    return pb.StatusCode_OK


def history(session, req, rsp):
    if req.gacha_id not in _table("Gacha", "info") or req.page < 1:
        return pb.StatusCode_BAD_REQ
    rsp.gacha_id, rsp.page = req.gacha_id, req.page
    pid = session.player_id
    rsp.total_page = (db.get_total_gacha_num(pid, req.gacha_id) + 4) // 5
    for item, when in db.conn.execute("""SELECT item_id,gacha_time FROM gacha_record
            WHERE player_id=? AND gacha_id=? ORDER BY id DESC LIMIT 5 OFFSET ?""",
            (pid, req.gacha_id, (req.page - 1) * 5)):
        rsp.records.add(gacha_id=req.gacha_id, item_id=item, gacha_time=when)
    return pb.StatusCode_OK


def pick(session, req, rsp):
    pool = active().get(req.gacha_id)
    if not pool or onboarding(session):
        return pb.StatusCode_BAD_REQ
    config = _table("Gacha", "info")[req.gacha_id]
    options = _table("Gacha", "reward_pool").get(config.get("optional_pool_i_d"), {})
    if req.item_id not in {r["item_i_d"] for r in options.get("items", [])}:
        return pb.StatusCode_BAD_REQ
    pid = session.player_id
    current = info(pid, pool)
    if current.has_full_pick:
        return pb.StatusCode_REPEAT_GET_REWARD
    if current.gacha_times < config["optional_num"]:
        return pb.StatusCode_BAD_REQ
    notice = pb.PackNotice(status=pb.StatusCode_OK)
    award(pid, req.item_id, rsp.item.add(), notice)
    db.conn.execute("INSERT INTO private_gacha_picks VALUES(?,?,?)", (pid, req.gacha_id, req.item_id))
    session.send(MsgId.PackNotice, notice, 0)
    return pb.StatusCode_OK


def trace_draw(session, req, size, status):
    """Public banner identifiers and rule gates only; keep motion traffic out of this capped log."""
    diagnostics = getattr(session, "stages", None)
    if diagnostics is None or not diagnostics.enabled:
        return
    catalog = _table("Gacha", "info")
    configured = active()
    is_tutorial = onboarding(session)
    if is_tutorial:
        step = saved(session.player_id, STEPS[-1])
        mode = "tutorial_ready" if step and step.status == pb.QuestStatus_InProgress else "tutorial_other"
    else:
        mode = "normal"
    if session.dungeon[0]:
        mode = "in_dungeon"
    pick = selected(session.player_id, configured) if req.gacha_id == CHOICE else 0
    record = {"id": req.gacha_id if req.gacha_id == 0 or req.gacha_id in catalog else "unknown",
              "single": req.is_single, "size": size, "status": status, "mode": mode,
              "enabled": req.gacha_id in configured, "choice": pick}
    # The cap, private file permissions and rotation use the same writer as other diagnostics.
    diagnostics._write("gacha", record, diagnostics.path.with_name("stage-gacha.jsonl"),
                       diagnostics.path.with_name("stage-gacha.previous.jsonl"))
