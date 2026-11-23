"""Native recruiting. The game host commits currency, roster, results and history together."""
from collections import OrderedDict
import json
import secrets

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item
from private_dungeons import _table
from private_first_quest import STEPS, STORY, saved
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
    if session.scene_id != 9999 or not saved(session.player_id, STEPS[0]):
        return False
    last = saved(session.player_id, STEPS[-1])
    if not last or last.status != pb.QuestStatus_Finish:
        return True
    chapter = db.get_chapter(session.player_id, 10000)
    return not chapter or STORY not in pb.Chapter.FromString(chapter).rewarded_story_ids


def info(pid, pool):
    key = pool["id"]
    count = db.get_total_gacha_num(pid, key)
    picked = db.conn.execute("SELECT 1 FROM private_gacha_picks WHERE player=? AND pool=?", (pid, key)).fetchone()
    return pb.GachaInfo(gacha_id=key, gacha_times=count, has_full_pick=bool(picked),
                        guarantee=max(1, pool["pity"] - db.get_gacha_guarantee(pid, key)))


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


def award(pid, item_id, delta, notice):
    row = _table("Item", "item")[item_id]
    item = make_item(item_id, 1, pid)
    delta.CopyFrom(item)
    if row["new_bag_item_tag"] == pb.EBagItemTag_Poster:
        delta.main_item.is_new = True
        db.set_item_detail(pid, delta.SerializeToString(), instance_id=delta.main_item.poster.instance_id)
        notice.items.add().CopyFrom(delta)
        return
    if row["new_bag_item_tag"] != pb.EBagItemTag_Card:
        raise ValueError("Unsupported recruit item type")
    config = _table("Character", "character")[item_id]
    owned = db.get_characters(pid, item_id)
    if owned:
        delta.main_item.character.ParseFromString(owned[0])
        shard = config["repeat_item_i_d"]
        raw = db.get_item_detail(pid, shard)
        balance = pb.ItemDetail.FromString(raw) if raw else make_item(shard, 0, pid)
        if balance.main_item.WhichOneof("item") != "base_item":
            raise ValueError("Invalid character shard balance")
        balance.main_item.base_item.num += 1
        db.set_item_detail(pid, balance.SerializeToString(), shard)
        notice.items.add().CopyFrom(balance)
        extra = delta.extras.add()
        extra.CopyFrom(balance.main_item)
        extra.base_item.num = 1
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


def draw(session, req, rsp, packet_id):
    if onboarding(session):
        return tutorial.draw(session, req, rsp)
    cache = getattr(session, "gacha_receipts", None)
    if cache is None:
        cache = session.gacha_receipts = OrderedDict()
    request = (packet_id, req.gacha_id, req.is_single)
    if packet_id and request in cache:
        rsp.ParseFromString(cache[request])
        return pb.StatusCode_OK
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
    misses = db.get_gacha_guarantee(pid, pool_id)
    reward_config = _table("Gacha", "info")[pool_id]
    for _ in range(count):
        item, misses = outcome(pool, misses, reward_config["big_guarantee_pool_i_d"])
        award(pid, item, rsp.items.add(), notice)
        db.add_gacha_record(pid, req.gacha_id, item)
    db.set_gacha_guarantee(pid, pool_id, misses)
    rsp.info.CopyFrom(choice_info(pid, pools) if req.gacha_id == CHOICE else info(pid, pool))
    session.send(MsgId.PackNotice, notice, 0)
    if packet_id:
        rsp.status = pb.StatusCode_OK
        cache[request] = rsp.SerializeToString()
        if len(cache) > 64:
            cache.popitem(last=False)
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
