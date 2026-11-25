"""World and dungeon gathering from the national client's authored spawn points."""
from network.msg_id import MsgId
from private_character_appearance import STARTER, TOOLS
from private_dungeons import _table, config as dungeon_config
from private_equipment import load
from private_events import _day
from private_inventory import receive
import private_shop as shop
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item
from utils.res_loader import res


TYPES = ("flower", "wood", "stone", "butterfly", "water", "pick")
_indexed = None


def setup():
    db.conn.execute("""CREATE TABLE IF NOT EXISTS field_gather_counts(
        player INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
        scope INTEGER NOT NULL, place INTEGER NOT NULL, kind INTEGER NOT NULL,
        total INTEGER NOT NULL, day TEXT NOT NULL, used INTEGER NOT NULL, lucky INTEGER NOT NULL,
        PRIMARY KEY(player,scope,place,kind)) WITHOUT ROWID""")


def points():
    """The client's 1751 index is LifeGatherPointer.Index, including locally spawned entities."""
    global _indexed
    source = res["ScenesConfigAsset"]
    if _indexed is None or _indexed[0] is not source:
        values = {
            (group, scene["ID"]): {
                p.get("Index", 0): p["GatherID"]
                for region in scene["GatherPointSetInfo"] for cell in region.get("GatherPointSets", ())
                for p in cell.get("LifeGatherPointers", ()) if "GatherID" in p
            }
            for group in ("Scenes", "Dungeons") for scene in source[group] if scene.get("GatherPointSetInfo")
        }
        _indexed = source, values
    return _indexed[1]


def location(session):
    did = session.dungeon[0]
    if did:
        return ("Dungeons", did), dungeon_config(did)["scene_i_d"]
    return ("Scenes", session.scene_id), session.scene_id


def limit_row(player, scope, place, kind, day):
    row = db.conn.execute("""SELECT total,day,used,lucky FROM field_gather_counts
        WHERE player=? AND scope=? AND place=? AND kind=?""", (player,scope,place,kind)).fetchone()
    result = pb.GatherLimit(gather_type=kind)
    if row:
        result.gather_num = row[0]
        if row[1] == day:
            result.gather_limit_num, result.lucky_gather_limit_num = row[2:]
    return result


def write_limits(target, player, sid, day):
    for kind,total,written,used,lucky in db.conn.execute("""SELECT kind,total,day,used,lucky FROM field_gather_counts
        WHERE player=? AND scope=0 AND place=? ORDER BY kind""", (player,sid)):
        row=target.add(gather_type=kind,gather_num=total)
        if day==written:
            row.gather_limit_num,row.lucky_gather_limit_num=used,lucky


def fill(session, target):
    _, sid = location(session)
    day = _day(shop.now())
    session.gather_day = day
    # Neither the city login nor re-entry in the available national archive uses a special group.
    target.current_gather_group_id = 0
    write_limits(target.gather_limits,session.player_id,sid,day)


def tick(session):
    day = _day(shop.now())
    if getattr(session, "gather_day", day) == day:
        return
    session.gather_day = day
    out = pb.GatherSceneLimitRecoveryNotice(status=pb.StatusCode_OK)
    _, sid = location(session)
    row = out.scene_gather_limits.add(scene_id=sid)
    write_limits(row.gather_limits,session.player_id,sid,day)
    if row.gather_limits:
        session.send(MsgId.GatherSceneLimitRecoveryNotice,out,0)


def upgrade_tools(player, chars):
    """The old realm wrote starter templates into equipment slots without issuing weapon instances."""
    starters = {slot:template for slot,template in STARTER.items() if TOOLS.get(slot) in (4,5,6,7,8)}
    missing = [(actor,slot,template) for actor in chars for slot,template in starters.items()
               if getattr(actor.character_appearance,slot) == template]
    if not missing:
        return
    keys = sorted({template for _,_,template in missing})
    placeholders = ",".join("?" for _ in keys)
    reserved = {key:pb.ItemDetail.FromString(blob).main_item for key,blob in db.conn.execute(
        "SELECT instance_id,item_detail_blob FROM items_s WHERE player_id=? AND instance_id IN ("+placeholders+")",
        (player,*keys))}
    weapons = _table("Weapon","weapon")
    # On an old high-ID save the number may already denote a genuinely selected upgraded tool.
    missing = [(actor,slot,key) for actor,slot,key in missing if key not in reserved or
               not reserved[key].HasField("weapon") or
               weapons.get(reserved[key].weapon.weapon_id,{}).get("new_weapon_type") != TOOLS[slot]]
    if not missing:
        return
    owned = {key:key for key,item in reserved.items() if item.HasField("weapon") and item.weapon.weapon_id==key}
    high_water = db.get_instance_id(player,no_change=True)-1
    for blob in db.get_item_detail(player,table="items_s"):
        item = pb.ItemDetail.FromString(blob).main_item
        if item.HasField("weapon") and item.weapon.weapon_id in starters.values():
            owned.setdefault(item.weapon.weapon_id,item.weapon.instance_id)
    changed = {}
    for actor,slot,template in missing:
        if template not in owned:
            detail = make_item(template,1,player)
            key = detail.main_item.weapon.instance_id
            # Older clients and saves already reference this number as a UUID. Reserve it only if
            # the player's normal allocator has never reached it and no equipment owns the slot.
            if template not in reserved and high_water < template:
                detail.main_item.weapon.instance_id = key = template
            db.set_item_detail(player,detail.SerializeToString(),instance_id=key)
            owned[template] = key
        key = owned[template]
        if key == template:
            continue
        setattr(actor.character_appearance,slot,key)
        if actor.gather_weapon == template:
            actor.gather_weapon = key
        changed[actor.character_id] = actor
    for cid,actor in changed.items():
        db.set_character(player,cid,actor.SerializeToString())


def equipped_type(session):
    actor = session.dungeon_lead if session.dungeon[0] else session.scene_player.team.char1
    if not actor.char_id or actor.is_dead:
        return 0
    detail = load(session.player_id,actor.gather_weapon,"weapon")
    if detail:
        return _table("Weapon","weapon").get(detail.main_item.weapon.weapon_id,{}).get("new_weapon_type",0)
    return 0


def record_book(session, entries):
    from private_dungeons import progress
    book = _table("LifeGather","life_gather_item")
    rules = _table("Achieve","achieve")
    kind = pb.LIFE_TYPE_Gathering
    out = pb.LifeAchieveNotice(status=pb.StatusCode_OK)
    changes = out.life_achieves.add(life_type=kind)
    for row in entries:
        item,quantity = row["item_i_d"],row["count"]
        definition = book.get(item)
        if not definition:
            continue
        db.conn.execute("""INSERT INTO player_kv VALUES(?,'life_book',?,?) ON CONFLICT(player_id,ns,k)
            DO UPDATE SET v=min(4294967295,CAST(v AS INTEGER)+excluded.v)""",
            (session.player_id,f"{kind}:{item}",quantity))
        for goal in definition.get("life_gather_achieve_rewards",()):
            aid = goal["achieve_condition_i_d"]
            old = progress(session.player_id,aid)
            new = min(old+quantity,rules[aid]["count_param"])
            if new > old:
                entry = changes.life_achieve_.add(achieve_id=aid,count=new)
                db.set_achieve(session.player_id,aid,entry.SerializeToString())
    if changes.life_achieve_:
        session.send(MsgId.LifeAchieveNotice,out,0)


def gather(session, req, rsp):
    point = req.gather_item
    rsp.index = point.index
    world, sid = location(session)
    group_id = points().get(world,{}).get(point.index)
    group = _table("Gather","gather").get(group_id,{})
    info = next((r for r in group.get("gather_group_info",()) if r["reward"] == point.reward), None)
    if info is None:
        return pb.StatusCode_BAD_REQ
    kind = info.get("new_gather_type",0)
    if kind != 5:  # Pick is a bare-hand interaction in the PC build.
        tool = equipped_type(session)
        if not tool:
            return pb.StatusCode_GATHER_WEAPON_NOT_WEAR
        if tool != info["new_weapon_type"]:
            return pb.StatusCode_GATHER_WEAPON_TYPE_DIFFER
    entries = [r for r in _table("Gather","gather_reward")[point.reward]["gather_reward_group_info"]
               if bool(r.get("lucky")) == point.is_lucky]
    if not entries:
        return pb.StatusCode_BAD_REQ
    scene_rule = _table("Scene","scene").get(sid,{}).get("gather_rule_i_d",sid)
    scene_caps = _table("Scene","gather_number").get(scene_rule)
    # Rule zero is an authored row; the generic resource index omits zero-valued IDs.
    group_caps = next((r for r in res["Gather"]["gather_rule"]["datas"] if not r.get("i_d")), None)
    if scene_caps is None or group_caps is None:
        return pb.StatusCode_CFG_NOT_FOUND
    field = TYPES[kind] + "_num"
    pid,day = session.player_id,_day(shop.now())
    group_id = 0
    counters = []
    for scope,place,caps in ((0,sid,scene_caps),(1,group_id,group_caps)):
        counts = limit_row(pid,scope,place,kind,day)
        if (counts.gather_limit_num >= caps.get(field,0) or
                point.is_lucky and counts.lucky_gather_limit_num >= caps.get("lucky_finish"+field,0)):
            return pb.StatusCode_GATHER_LIMIT
        counters.append((scope,place,counts))
    result = receive(session,[make_item(r["item_i_d"],r["count"],pid) for r in entries],rsp.items)
    if result != pb.StatusCode_OK:
        return result
    for scope,place,counts in counters:
        counts.gather_num = min(0xffffffff,counts.gather_num+1)
        counts.gather_limit_num += 1
        counts.lucky_gather_limit_num += int(point.is_lucky)
        db.conn.execute("""INSERT OR REPLACE INTO field_gather_counts VALUES(?,?,?,?,?,?,?,?)""",
                        (pid,scope,place,kind,counts.gather_num,day,counts.gather_limit_num,counts.lucky_gather_limit_num))
    rsp.item_level = 1
    rsp.scene_gather_limit.scene_id = sid
    rsp.scene_gather_limit.gather_limits.add().CopyFrom(counters[0][2])
    rsp.group_gather_limit.gather_group_id = group_id
    rsp.group_gather_limit.gather_limit.CopyFrom(counters[1][2])
    record_book(session, entries)
    from private_activity_fragments import gathered
    gathered(session, kind)
    if kind == 5 and info["new_weapon_type"] == 15:
        from private_bless_tasks import record
        record(session,76)
    return pb.StatusCode_OK
