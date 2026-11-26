"""Native recruiting. The game host commits currency, roster, results and history together."""
import json
import secrets
from functools import lru_cache

from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item
from utils.res_loader import res
from private_first_quest import STEPS, saved
from private_request import cache_after_commit
import private_tutorial_recruit as tutorial
import private_gacha_rewards as rewards
import private_gameplay_transaction as gameplay
from realm_gacha import FASHION, defaults, upgrade
import private_fashion_gacha as fashion


def setup():
    rewards.setup(db.conn)
    upgrade(db.conn,"authority.")
    fashion.setup()
    db.conn.execute("""CREATE TABLE IF NOT EXISTS private_gacha_picks (
        player INTEGER NOT NULL, pool INTEGER NOT NULL, item INTEGER NOT NULL,
        PRIMARY KEY(player,pool)) WITHOUT ROWID""")
    db.conn.execute("""CREATE TABLE IF NOT EXISTS private_gacha_choices (
        player INTEGER PRIMARY KEY, selected INTEGER NOT NULL)""")
    db.conn.execute("CREATE INDEX IF NOT EXISTS private_gacha_history ON gacha_record(player_id,gacha_id,id)")


def active():
    row = db.conn.execute("SELECT settings FROM authority.private_gacha_config WHERE id=1").fetchone()
    return _enabled_pools(row[0] if row else None)


@lru_cache(maxsize=1)
def _enabled_pools(settings):
    # Cache only decoded configuration. Each request still reads the owner's current revision.
    config = json.loads(settings) if settings is not None else defaults()
    return {p["id"]: p for p in config["pools"] if p["enabled"]}


def onboarding(session):
    """Only the active 'recruit one teammate' objective diverts a plaza draw to the lesson."""
    if session.scene_id != 9999 or session.dungeon[0]:
        return False
    step = saved(session.player_id, STEPS[-1])
    return bool(step and step.status == pb.QuestStatus_InProgress and len(step.conditions) == 1
                and step.conditions[0].condition_id == tutorial.CONDITIONS[-1])


def pool_stats(pid, keys):
    # Probe only visible pools through each table's existing index, including on large old saves.
    values = ",".join("(?)" for _ in keys)
    rows = db.conn.execute(f"""WITH listed(pool) AS (VALUES {values})
        SELECT pool,
               (SELECT COUNT(*) FROM gacha_record WHERE player_id=? AND gacha_id=listed.pool),
               EXISTS(SELECT 1 FROM private_gacha_picks p WHERE p.player=? AND p.pool=listed.pool),
               (SELECT guarantee FROM gacha_guarantee WHERE player_id=? AND gacha_id=listed.pool)
          FROM listed""", (*keys, pid, pid, pid))
    return {key: (total, picked, misses) for key, total, picked, misses in rows}


def info(pid, pool, stats=None):
    key = pool["id"]
    if stats is None:
        stats = pool_stats(pid, (key,))
    count, picked, misses = stats[key]
    if key==FASHION:
        return fashion.info(pid,count)
    if misses is None:
        misses = recent_misses(pid, pool["pity"] - 1, 4) if key == 2000 else 0
    return pb.GachaInfo(gacha_id=key, gacha_times=count, has_full_pick=bool(picked),
                        guarantee=max(1, pool["pity"] - misses))


CHOICE = 9000
CHOICE_BANNERS = (9000, 9001)


def choices(pools, banner=CHOICE):
    allowed = res.index("Gacha", "info")[banner]["card_pool_range"]
    return [key for key in allowed if key in pools]


def selected(pid, pools, banner=CHOICE):
    if banner == CHOICE:
        stored = db.conn.execute("SELECT selected FROM private_gacha_choices WHERE player=?", (pid,)).fetchone()
    else:
        stored = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='recruit_choice' AND k=?",
                                 (pid, str(banner))).fetchone()
    return int(stored[0]) if stored and int(stored[0]) in choices(pools, banner) else 0


def choice_info(pid, pools, stats=None, banner=CHOICE):
    pick = selected(pid, pools, banner)
    count = stats[banner][0] if stats is not None else db.get_total_gacha_num(pid, banner)
    result = pb.GachaInfo(gacha_id=banner, choose_pool_id=pick, gacha_times=count)
    if pick:
        misses = (stats[pick][2] or 0) if stats is not None else db.get_gacha_guarantee(pid, pick)
        result.guarantee = max(1, pools[pick]["pity"] - misses)
    return result


def choose(session, req, rsp):
    pools = active()
    if req.gacha_id not in CHOICE_BANNERS or req.choose_gacha_pool not in choices(pools, req.gacha_id):
        return pb.StatusCode_BAD_REQ
    if req.gacha_id == CHOICE:
        db.conn.execute("INSERT OR REPLACE INTO private_gacha_choices VALUES(?,?)",
                        (session.player_id, req.choose_gacha_pool))
    else:
        db.conn.execute("INSERT OR REPLACE INTO player_kv(player_id,ns,k,v) VALUES(?,'recruit_choice',?,?)",
                        (session.player_id, str(req.gacha_id), str(req.choose_gacha_pool)))
    rsp.gacha_id, rsp.choose_gacha_pool = req.gacha_id, req.choose_gacha_pool
    return pb.StatusCode_OK


def list_pools(session, req, rsp):
    pools = active()
    if pools:
        banners = [key for key in CHOICE_BANNERS if choices(pools, key)]
        stats = pool_stats(session.player_id, (*pools, *banners))
        for pool in pools.values():
            rsp.gachas.add().CopyFrom(info(session.player_id, pool, stats))
        for key in banners:
            rsp.gachas.add().CopyFrom(choice_info(session.player_id, pools, stats, key))
    if onboarding(session) and 2000 not in pools:
        rsp.gachas.add().CopyFrom(tutorial.pool_info(session.player_id))
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
    members = res.index("Gacha", "reward_pool")[group]["items"]
    item = members[randbelow(len(members))]["item_i_d"]
    return item, 0 if group == guarantee_group else misses + 1


def explorer_at_least(item, quality):
    row = res.index("Item", "item").get(item, {})
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


def extra(balances, item_id, amount, delta, *, converted=False):
    item = balances.change(item_id, amount)
    reward = (delta.transformed_item if converted else delta.extras).add()
    reward.CopyFrom(item)
    reward.base_item.num = amount


def duplicate_bonus(balances, prefix, quality, delta, *, converted=False):
    rule = res.index("GlobalConfig", "config", "key")[f"Gacha_{prefix}Quality{quality}_Reward"]
    for value in rule["values"]:
        item_id, amount = map(int, value.split(":"))
        extra(balances, item_id, amount, delta, converted=converted)


def known_poster(pid, template):
    # All new item writes already feed the indexed ownership ledger, including non-draw grants.
    lookup = "SELECT 1 FROM claimed_rewards WHERE player_id=? AND system='got_ids' AND key=?"
    if db.conn.execute(lookup, (pid, str(template))).fetchone():
        return True
    migrated = db.conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,'migration','gacha_posters_v1')",
                               (pid,)).rowcount
    if migrated:
        db.conn.execute("""INSERT OR IGNORE INTO claimed_rewards(player_id,system,key)
            SELECT player_id,'got_ids',CAST(item_id AS TEXT) FROM gacha_record WHERE player_id=?""", (pid,))
        rows = db.conn.execute("SELECT item_detail_blob FROM items_s WHERE player_id=?", (pid,))
        for (raw,) in rows:
            item = pb.ItemDetail.FromString(raw).main_item
            if item.WhichOneof("item") == "poster":
                db.conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,'got_ids',?)",
                                (pid, str(item.item_id)))
        return bool(db.conn.execute(lookup, (pid, str(template))).fetchone())
    return False


def award(pid, item_id, delta, notice, *, balances=None, recruit=False):
    # Activity rewards also use this routine outside recruitment.
    if balances is None:
        balances = gameplay.StackChanges(pid)
        apply_award(pid, item_id, delta, notice, balances, recruit)
        balances.flush(notice)
    else:
        apply_award(pid, item_id, delta, notice, balances, recruit)


def apply_award(pid, item_id, delta, notice, balances, recruit):
    row = res.index("Item", "item")[item_id]
    item = make_item(item_id, 1, pid)
    delta.CopyFrom(item)
    # Only a real random pull receives the appearance vouchers listed in string_gacha (PC 1.1.12).
    quality = row.get("quality", 0)
    if row["new_bag_item_tag"] == pb.EBagItemTag_Poster:
        duplicate = recruit and known_poster(pid, item_id)
        delta.main_item.is_new = not duplicate
        db.set_item_detail(pid, delta.SerializeToString(), instance_id=delta.main_item.poster.instance_id)
        notice.items.add().CopyFrom(delta)
        if recruit:
            if duplicate:
                duplicate_bonus(balances, "Poster_", quality, delta)
            if quality in (3, 4):
                extra(balances, 107, {3: 50, 4: 500}[quality], delta)
        return
    if row["new_bag_item_tag"] != pb.EBagItemTag_Card:
        raise ValueError("Unsupported recruit item type")
    config = res.index("Character", "character")[item_id]
    owned = db.get_characters(pid, item_id)
    if owned:
        delta.main_item.character.ParseFromString(owned[0])
        max_star = delta.main_item.character.star >= 5
        if not max_star:
            extra(balances, config["repeat_item_i_d"], 3 if quality in (3, 4) else 1, delta, converted=True)
        if quality in (3, 4):
            duplicate_bonus(balances, "Character_FullStar" if max_star else "Character_", quality, delta, converted=max_star)
            if recruit:
                extra(balances, 107, {3: 500, 4: 1500}[quality], delta)
        return
    outfit_ids = {config.get("hat_i_d"), config.get("hair_i_d"), config.get("cloth_i_d"),
                  *(config.get("orn_i_d") or [])} - {None, 0}
    missing = [template for template in outfit_ids if not db.get_item_detail(pid, template)]
    char = db.add_character(pid, item_id, delta.main_item.character)
    from private_field_gathering import upgrade_tools
    upgrade_tools(pid, [char], notice.items)
    delta.main_item.is_new = True
    notice.items.add().CopyFrom(delta)
    notice.items.add().ParseFromString(db.get_item_detail(pid, instance_id=char.equipment_presets[0].weapon))
    for outfit in missing:
        notice.items.add().ParseFromString(db.get_item_detail(pid, outfit))
    if recruit and quality in (3, 4):
        extra(balances, 107, {3: 500, 4: 1500}[quality], delta)


def draw(session, req, rsp, packet_id):
    lesson = req.gacha_id == 2000 and req.is_single and onboarding(session)
    cache = getattr(session, "gacha_receipts", None)
    request = (packet_id, req.gacha_id, req.is_single)
    if packet_id and cache is not None and request in cache:
        prior_lesson, raw = cache[request]
        if not lesson or prior_lesson:
            rsp.ParseFromString(raw)
            return pb.StatusCode_OK
    status = gameplay.run(session, req, rsp, tutorial.draw if lesson else regular_draw)
    if status == pb.StatusCode_OK and packet_id:
        rsp.status = status
        cache_after_commit(session, "gacha_receipts", request, (lesson, rsp.SerializeToString()))
    return status


def regular_draw(session, req, rsp, pending):
    pools = active()
    pool_id = selected(session.player_id, pools, req.gacha_id) if req.gacha_id in CHOICE_BANNERS else req.gacha_id
    pool = pools.get(pool_id)
    if pool is None or session.dungeon[0]:
        return pb.StatusCode_BAD_REQ
    pid = session.player_id
    if pool_id==FASHION:
        return fashion.draw(session,req,rsp,pending,pool)
    config = res.index("Gacha", "info")[req.gacha_id]
    key = "1" if req.is_single else "2"
    count = config.get("method" + key + "_count", 0)
    if count not in (1, 10):
        return pb.StatusCode_BAD_REQ
    ticket_id, price = config["consume_item" + key + "_i_d"], config["consume_item" + key + "_num"]
    if config.get("max_times") and db.get_total_gacha_num(pid, req.gacha_id) + count > config["max_times"]:
        return pb.StatusCode_BAD_REQ
    pending.balances.change(ticket_id, -price)
    misses = pity_misses(pid, pool)
    reward_config = res.index("Gacha", "info")[pool_id]
    sr_limit = pool.get("sr_pity", 10) if pool_id == 2000 else 0
    since_sr = recent_misses(pid, sr_limit - 1, 3) if sr_limit else 0
    for _ in range(count):
        item, misses = outcome(pool, misses, reward_config["big_guarantee_pool_i_d"])
        if sr_limit:
            if not explorer_at_least(item, 3) and since_sr + 1 >= sr_limit:
                # Preserve an SSR explorer rolled by the base pool or hard pity on this exact pull.
                item = secrets.choice(res.index("Gacha", "reward_pool")[300]["items"])["item_i_d"]
            since_sr = 0 if explorer_at_least(item, 3) else since_sr + 1
        delta = rsp.items.add()
        award(pid, item, delta, pending.pack, balances=pending.balances, recruit=True)
        rewards.record(db, pid, req.gacha_id, delta)
    db.set_gacha_guarantee(pid, pool_id, misses)
    rsp.info.CopyFrom(choice_info(pid, pools, banner=req.gacha_id) if req.gacha_id in CHOICE_BANNERS else info(pid, pool))
    return pb.StatusCode_OK


def history(session, req, rsp):
    if req.gacha_id not in res.index("Gacha", "info") or req.page < 1:
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
    return gameplay.run(session, req, rsp, grant_pick)


def grant_pick(session, req, rsp, pending):
    pool = active().get(req.gacha_id)
    if not pool:
        return pb.StatusCode_BAD_REQ
    config = res.index("Gacha", "info")[req.gacha_id]
    options = res.index("Gacha", "reward_pool").get(config.get("optional_pool_i_d"), {})
    if req.item_id not in {r["item_i_d"] for r in options.get("items", [])}:
        return pb.StatusCode_BAD_REQ
    pid = session.player_id
    current = info(pid, pool)
    if current.has_full_pick:
        return pb.StatusCode_REPEAT_GET_REWARD
    if current.gacha_times < config["optional_num"]:
        return pb.StatusCode_BAD_REQ
    award(pid, req.item_id, rsp.item.add(), pending.pack, balances=pending.balances)
    db.conn.execute("INSERT INTO private_gacha_picks VALUES(?,?,?)", (pid, req.gacha_id, req.item_id))
    return pb.StatusCode_OK


def trace_draw(session, req, size, status):
    """Public banner identifiers and rule gates only; keep motion traffic out of this capped log."""
    diagnostics = getattr(session, "stages", None)
    if diagnostics is None or not diagnostics.enabled:
        return
    catalog = res.index("Gacha", "info")
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
