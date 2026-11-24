"""Persist 1.1.12 exploration slots and transfer items into the character's ordinary inventory."""
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_dungeons import _table
from utils import db


def initialize():
    existing = db.conn.execute("SELECT sql FROM sqlite_schema WHERE name='private_temp_pack'").fetchone()
    old = existing and "CHECK(slot >= 0)" in existing[0]
    if old:
        if not db.conn.in_transaction:
            db.conn.execute("BEGIN")
        db.conn.execute("ALTER TABLE private_temp_pack RENAME TO private_temp_pack_zero_based")
    db.conn.execute("""CREATE TABLE IF NOT EXISTS private_temp_pack (
        player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
        slot INTEGER NOT NULL CHECK(slot >= 1), detail BLOB NOT NULL,
        PRIMARY KEY(player_id,slot)) WITHOUT ROWID""")
    if old:
        for pid,slot,blob in db.conn.execute("SELECT player_id,slot,detail FROM private_temp_pack_zero_based"):
            item = pb.ItemDetail.FromString(blob)
            item.main_item.temp_pack_index = slot+1
            db.conn.execute("INSERT INTO private_temp_pack VALUES(?,?,?)",(pid,slot+1,item.SerializeToString()))
        db.conn.execute("DROP TABLE private_temp_pack_zero_based")


def capacity():
    return int(_table("GlobalConfig", "config", "key")["Player_BattleBag_Max_Count"]["values"][0])


def slots(player):
    return {index: pb.ItemDetail.FromString(data) for index, data in db.conn.execute(
        "SELECT slot,detail FROM private_temp_pack WHERE player_id=? ORDER BY slot", (player,))}


def replace_slots(player, held):
    db.conn.execute("DELETE FROM private_temp_pack WHERE player_id=?", (player,))
    db.conn.executemany("INSERT INTO private_temp_pack(player_id,slot,detail) VALUES (?,?,?)",
                        ((player,index,item.SerializeToString()) for index,item in held.items()))


def fill_pack(pack, held):
    pack.max_size = capacity()
    for index in sorted(held):
        pack.item_infos.add().CopyFrom(held[index].main_item)


def compact(entries):
    held=dict(enumerate(entries,1))
    for index,item in held.items():
        item.main_item.temp_pack_index=index
    return held


def append_slots(held, item, limit):
    """Plan against an in-memory copy. On overflow nothing has been written."""
    info = item.main_item
    base = info.WhichOneof("item") == "base_item"
    remaining = info.base_item.num if base else 1
    added = []
    def record(index, count):
        entry = pb.ItemDetail.FromString(item.SerializeToString())
        entry.pack_type = pb.ItemDetail.PackType_TempPack
        entry.main_item.temp_pack_index = index
        if base:
            entry.main_item.base_item.num = count
        added.append(entry)
        return entry
    stack_limit = _table("Item","item")[info.item_id].get("stack_count") or 1
    if remaining < 1:
        raise ValueError("Empty item in exploration rewards")
    if base and stack_limit > 1:
        for index, entry in held.items():
            other = entry.main_item
            if other.item_id == info.item_id and other.WhichOneof("item") == "base_item":
                count = min(remaining, max(0, stack_limit - other.base_item.num))
                if count:
                    record(index,count)
                    other.base_item.num += count
                    remaining -= count
    for index in range(1,limit+1):
        if not remaining:
            break
        if index in held:
            continue
        count = min(remaining, stack_limit) if base else 1
        held[index] = pb.ItemDetail.FromString(record(index,count).SerializeToString())
        remaining -= count
    return None if remaining else added


def deposit(player, source):
    item = pb.ItemDetail.FromString(source.SerializeToString())
    item.pack_type = pb.ItemDetail.PackType_Inventory
    info = item.main_item
    info.ClearField("temp_pack_index")
    kind = info.WhichOneof("item")
    if kind in ("weapon","armor"):
        instance = getattr(info,kind).instance_id
        if not instance or db.get_item_detail(player,instance_id=instance):
            raise ValueError("An equipment instance must be new to its owner's inventory")
        db.set_item_detail(player,item.SerializeToString(),instance_id=instance)
    elif kind in ("base_item","outfit"):
        previous = db.get_item_detail(player,info.item_id)
        if kind == "base_item":
            if previous:
                info.base_item.num += pb.ItemDetail.FromString(previous).main_item.base_item.num
        elif previous:
            return pb.ItemDetail.FromString(previous)
        db.set_item_detail(player,item.SerializeToString(),item_id=info.item_id)
    else:
        raise ValueError("Unsupported exploration reward")
    return item


def publish(session, items, clear=False):
    notice = pb.PackNotice(status=pb.StatusCode_OK, temp_pack_max_size=capacity(), is_clear_temp_pack=clear)
    notice.items.extend(items)
    session.send(MsgId.PackNotice, notice, 0)


def receive(session, items, response_items):
    """Apply a loot batch in the caller's gameplay transaction."""
    tags = _table("Item", "item_tag", "new_tag")
    pid = session.player_id
    outside = session.dungeon[0] or session.scene_id != 9999
    held = slots(pid)
    direct, acquired = [], []
    modified = False
    for item in items:
        copy = pb.ItemDetail.FromString(item.SerializeToString())
        if outside and tags.get(copy.main_item.item_tag, {}).get("in_battle_bag"):
            placed = append_slots(held, copy, capacity())
            if placed is None:
                return pb.StatusCode_Temp_Pack_Is_Full
            acquired.extend(placed)
            modified = True
        else:
            copy.pack_type = pb.ItemDetail.PackType_Inventory
            direct.append(copy)
            acquired.append(copy)
    notice = [deposit(pid, item) for item in direct]
    if modified:
        replace_slots(pid, held)
        notice.extend(held.values())
    response_items.extend(acquired)
    if notice:
        publish(session, notice, clear=modified)
    return pb.StatusCode_OK


def store(session, req=None, rsp=None):
    pid = session.player_id
    held = slots(pid)
    received = []
    for item in held.values():
        received.append(deposit(pid, item))
        if rsp is not None:
            acquired = rsp.items.add()
            acquired.CopyFrom(item)
            acquired.pack_type = pb.ItemDetail.PackType_Inventory
            acquired.main_item.ClearField("temp_pack_index")
    if held:
        replace_slots(pid,{})
    publish(session, received, clear=True)
    return pb.StatusCode_OK


def sort(session, req, rsp):
    ordered = sorted(slots(session.player_id).values(), key=lambda item: (
        item.main_item.item_id, item.main_item.temp_pack_index))
    held = compact(ordered)
    replace_slots(session.player_id, held)
    fill_pack(rsp.temp_pack, held)
    return pb.StatusCode_OK


def drop(session, req, rsp):
    held=slots(session.player_id)
    if held.pop(req.index,None) is None:
        fill_pack(rsp.temp_pack,held)
        return pb.StatusCode_BAD_REQ
    held=compact(held.values())
    replace_slots(session.player_id,held)
    fill_pack(rsp.temp_pack,held)
    return pb.StatusCode_OK


def wear(session, req, rsp):
    pid = session.player_id
    held = slots(pid)
    item = held.get(req.index)
    rows = db.get_characters(pid,req.char_id) if req.char_id else []
    if item is None or not rows:
        return pb.StatusCode_BAD_REQ
    char = pb.Character.FromString(rows[0])
    kind = item.main_item.WhichOneof("item")
    if kind not in ("weapon","armor") or not char.equipment_presets:
        return pb.StatusCode_BAD_REQ
    data = _table(kind.title(), kind).get(item.main_item.item_id)
    actor = _table("Character","character").get(req.char_id, {})
    if not data or data.get("new_weapon_system_type",0) != actor.get("new_weapon_system_type",0):
        return pb.StatusCode_BAD_REQ
    preset = next((p for p in char.equipment_presets if p.preset_index==char.in_use_equipment_preset_index),None)
    if preset is None:
        return pb.StatusCode_BAD_REQ
    instance = getattr(item.main_item,kind)
    if kind == "weapon":
        old, preset.weapon = preset.weapon, instance.instance_id
    else:
        part = next((a for a in preset.armors if a.equip_type == data.get("new_equip_type",0)),None)
        if part is None:
            return pb.StatusCode_BAD_REQ
        old,part.armor_id = part.armor_id,instance.instance_id
    changed = []
    if old:
        previous = db.get_item_detail(pid,instance_id=old)
        if not previous:
            return pb.StatusCode_BAD_REQ
        old_item = pb.ItemDetail.FromString(previous)
        if old_item.main_item.WhichOneof("item") != kind:
            return pb.StatusCode_BAD_REQ
        getattr(old_item.main_item,kind).wearer_id = 0
        db.set_item_detail(pid,old_item.SerializeToString(),instance_id=old)
        changed.append(old_item)
    instance.wearer_id=req.char_id
    changed.append(deposit(pid,item))
    db.set_character(pid,req.char_id,char.SerializeToString())
    del held[req.index]
    held=compact(held.values())
    replace_slots(pid,held)
    fill_pack(rsp.temp_pack,held)
    publish(session,changed)
    from private_equipment_presets import sync_scene_equipment
    sync_scene_equipment(session, characters={req.char_id:char})
    return pb.StatusCode_OK


def page(session, req, rsp, kind):
    start, end = req.start_index, req.end_index
    if end and start > end:
        return pb.StatusCode_BAD_REQ
    system = getattr(req, "weapon_system_type", 0)
    entries = []
    for (data,) in db.conn.execute("SELECT item_detail_blob FROM items_s WHERE player_id=? ORDER BY instance_id", (session.player_id,)):
        info = pb.ItemDetail.FromString(data).main_item
        if info.WhichOneof("item") != kind:
            continue
        if system and _table(kind.title(),kind).get(info.item_id,{}).get("new_weapon_system_type",0) != system:
            continue
        entries.append(getattr(info,kind))
    rsp.total_num = len(entries)
    end = min(end or len(entries), len(entries))
    values = entries[start:end]
    rsp.end_index = min(start,len(entries)) + len(values)
    getattr(rsp, {"weapon":"weapons","armor":"armors","poster":"posters","pet":"pets"}[kind]).extend(values)
    return pb.StatusCode_OK

