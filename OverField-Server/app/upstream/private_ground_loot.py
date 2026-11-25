"""Native personal ground loot from monsters placed in the client's encounter prefabs."""
import random

from private_inventory import receive
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item
from utils.res_loader import res


def setup(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS realm_ground_items(
            player INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            scene INTEGER NOT NULL, channel INTEGER NOT NULL, idx INTEGER NOT NULL,
            items BLOB NOT NULL, PRIMARY KEY(player,scene,channel,idx)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS realm_encounter_kills(
            player INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            scene INTEGER NOT NULL, encounter INTEGER NOT NULL, unit INTEGER NOT NULL,
            idx INTEGER NOT NULL, next_roll INTEGER NOT NULL,
            PRIMARY KEY(player,scene,encounter,unit,idx)) WITHOUT ROWID;
    """)


def source(session, req):
    from private_field_activities import ENCOUNTER, now
    if session.dungeon[0]:
        return None
    bindings = res["EncounterMonsterNodes"]["encounters"]
    encounters = res.index("BattleEncounter","battle_encounter")
    stamp = int(now().timestamp())
    signature = [req.monster_id, req.monster_index]
    candidates = [(key,started,encounters[key]["c_d"],cycle) for key,started,cycle in db.conn.execute(
        "SELECT id,started,available FROM realm_field_activities WHERE player=? AND scene=? AND kind=? "
        "AND state=? AND started<>0", (session.player_id,session.scene_id,ENCOUNTER,pb.Start))
        if signature in bindings.get(str(key), ()) and 0 <= stamp-started <= encounters[key]["live_timer"]]
    return candidates[0] if len(candidates)==1 else None


_pool_cache = None


def drop_pools(world):
    global _pool_cache
    items = res.index("Item","item")
    if _pool_cache is None or _pool_cache[0] is not items:
        equipment = res["ReleasedEquipment"]["equipment_ids"]
        mats = {row["get_item_i_d"] for row in res["Spin"]["spin_item"]["datas"]
                if row.get("new_spin_type")==1 and row.get("spin_level")==1 and row.get("get_item_i_d")}
        pools = {2:[], 3:[], 8:[]}
        for item_id in sorted(set(equipment) | mats):
            row = items.get(item_id,{})
            tag = row.get("new_bag_item_tag")
            if tag in pools:
                pools[tag].append((item_id,row.get("quality",0)))
        _pool_cache = (items, pools, {})
    _, pools, levels = _pool_cache
    if world not in levels:
        levels[world] = {tag:[key for key,quality in values if tag==8 or quality <= world]
                         for tag,values in pools.items()}
    return levels[world]


def equipment_level(item, tag, maximum):
    kind = "weapon" if tag==2 else "armor"
    table = kind.title()
    definition = res.index(table,kind,"item_i_d")[item]
    key = definition.get(kind+"_property_i_d")
    record = res.index(table,kind+"_property").get(key,{})
    groups = record.get(kind+"_property_group_info",())
    # Some published weapons have a single level-one attribute group even for
    # a level-100 player. Only roll inside a range the template can represent.
    maximum = min(maximum,max((g["max_level"] for g in groups),default=1))
    return random.randint(1,maximum)


def roll(session):
    # of-ps cc4dc52e ordinary monster policy. Reward pools for 9007 and 9010
    # have no verified spawn in these prefabs and cannot enter this path.
    if random.randrange(100) >= session.realm_rules.ordinary_ground_drop_percent:
        return []
    player = session.player_id
    level,world = db.conn.execute("SELECT level,world_level FROM players WHERE player_id=?", (player,)).fetchone()
    world = max(1,world)
    pools = drop_pools(world)
    maximum = max(1,min(level,res.index("Level","world_level").get(world,{}).get("monster_level",level)))
    categories = [(tag,weight) for tag,weight in ((2,1),(3,4),(8,2)) if pools[tag]]
    if not categories:
        return []
    tags,weights = zip(*categories)
    items = []
    for _ in range(random.randint(1,2)):
        tag = random.choices(tags,weights)[0]
        template = random.choice(pools[tag])
        item = make_item(template,random.randint(1,3) if tag==8 else 1,player,
                         equipment_level=None if tag==8 else equipment_level(template,tag,maximum))
        item.pack_type = pb.ItemDetail.PackType_TempStorageArea
        items.append(item)
    return items


def monster(session,req,rsp):
    origin = source(session,req)
    if origin is None:
        return pb.StatusCode_PlayerNotInDungeon
    encounter,started,cooldown,cycle = origin
    rsp.monster_index = req.monster_index
    # The client repeats the same index for some siblings. A scene change or a
    # reconnect can also restart the encounter before its normal cooldown.
    first = db.conn.execute(
        "INSERT INTO realm_encounter_kills VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(player,scene,encounter,unit,idx) DO UPDATE SET next_roll=excluded.next_roll "
        "WHERE realm_encounter_kills.next_roll<=?",
        (session.player_id,session.scene_id,encounter,req.monster_id,req.monster_index,started+max(1,cooldown),started)
    ).rowcount
    if not first:
        return pb.StatusCode_OK
    from private_bless_tasks import record
    record(session,86)
    if not session.realm_rules.ordinary_ground_drop_percent:
        return pb.StatusCode_OK
    from private_encounter_gifts import for_battle
    if for_battle(session,encounter,cycle,started):
        return pb.StatusCode_OK
    items = roll(session)
    if items:
        row = db.conn.execute("INSERT INTO player_kv(player_id,ns,k,v) VALUES(?,'ground','seq',1) "
                              "ON CONFLICT(player_id,ns,k) DO UPDATE SET v=v+1 RETURNING v",
                              (session.player_id,)).fetchone()
        gift = pb.DropItem(index=row[0],items=items)
        db.conn.execute("INSERT INTO realm_ground_items VALUES(?,?,?,?,?)",
                        (*scope(session),gift.index,gift.SerializeToString()))
        rsp.drop_item.CopyFrom(gift)
    return pb.StatusCode_OK


def scope(session):
    return session.player_id, session.scene_id, session.channel_id


def fill(session, data):
    if not session.dungeon[0]:
        for blob, in db.conn.execute("SELECT items FROM realm_ground_items WHERE player=? AND scene=? AND channel=? "
                                     "ORDER BY idx",scope(session)):
            data.drop_items.add().ParseFromString(blob)


_GIFT_WHERE = " WHERE player=? AND scene=? AND channel=? AND idx=?"


def pending(location,index):
    row = db.conn.execute("SELECT items FROM realm_ground_items"+_GIFT_WHERE,(*location,index)).fetchone()
    return pb.DropItem.FromString(row[0]) if row else None


def pickup(session, req, rsp):
    if session.dungeon[0]:
        return pb.StatusCode_BAD_REQ
    key = (*scope(session),req.drop_item_index)
    gift = pending(scope(session),req.drop_item_index)
    if gift is None:
        return pb.StatusCode_BAD_REQ
    if req.pick_index == -1:
        selected = list(gift.items)
        del gift.items[:]
    elif 1 <= req.pick_index <= len(gift.items):
        selected = [gift.items[req.pick_index-1]]
        del gift.items[req.pick_index-1]
    else:
        return pb.StatusCode_BAD_REQ
    status = receive(session,selected,rsp.items)
    if status != pb.StatusCode_OK:
        return status
    if gift.items:
        db.conn.execute("UPDATE realm_ground_items SET items=?"+_GIFT_WHERE,(gift.SerializeToString(),*key))
    else:
        db.conn.execute("DELETE FROM realm_ground_items"+_GIFT_WHERE,key)
    return pb.StatusCode_OK
