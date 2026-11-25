"""Native three-slot furnishing presets for player-owned home channels."""
from collections import Counter
import unicodedata

from network.msg_id import MsgId
from private_channels import HOME, bag_adjust, room_limit, tally
from utils.res_loader import res
from private_garden_actions import fresh_id, publish
from proto import net_pb2 as pb
from realm_uids import public_uid
from utils import db

# UIHomeSystem.SCHEME_SUM; the native selection and rename callbacks use zero-based indices.
SLOTS = range(3)


def home_owner(session):
    return session.scene_id == HOME and not session.dungeon[0] and session.channel_id == public_uid(session.player_id)


def owner_layout(session):
    rows = db.conn.execute("SELECT furniture_detail_blob FROM furnitures WHERE scene_id=? AND channel_id=? "
                           "AND player_id=? ORDER BY furniture_id", (HOME, session.channel_id, session.player_id))
    return [pb.FurnitureDetailsInfo.FromString(raw) for raw, in rows]


def schemes(session, req, rsp):
    if not home_owner(session):
        return pb.StatusCode_BAD_REQ
    # The client only renders keys present in this map; an empty name uses its localized default.
    rsp.schemes.update({i:"" for i in SLOTS})
    for slot, name in db.conn.execute("SELECT slot,name FROM realm_garden_schemes WHERE player=?", (session.player_id,)):
        rsp.schemes[slot] = name
    return pb.StatusCode_OK


def check(session, req, rsp):
    rsp.scheme_id = req.scheme_id
    return home_owner(session) and req.scheme_id in SLOTS


def read_scheme(pid, slot):
    row = db.conn.execute("SELECT layout FROM realm_garden_schemes WHERE player=? AND slot=?", (pid, slot)).fetchone()
    if row is None or row[0] is None:
        return None
    return list(pb.GardenFurnitureBatchUpdateNotice.FromString(row[0]).new_furniture_list)


def save(session, req, rsp):
    if not check(session, req, rsp):
        return pb.StatusCode_BAD_REQ
    snapshot = pb.GardenFurnitureBatchUpdateNotice(new_furniture_list=owner_layout(session))
    db.conn.execute("INSERT INTO realm_garden_schemes(player,slot,layout) VALUES(?,?,?) "
                    "ON CONFLICT(player,slot) DO UPDATE SET layout=excluded.layout",
                    (session.player_id, req.scheme_id, snapshot.SerializeToString()))
    return pb.StatusCode_OK


def rename(session, req, rsp):
    if not check(session, req, rsp):
        return pb.StatusCode_BAD_REQ
    value = req.name.strip()
    if not value or len(value.encode("utf8")) > 128 or any(unicodedata.category(c)=="Cc" for c in value):
        return pb.StatusCode_BAD_REQ
    db.conn.execute("INSERT INTO realm_garden_schemes(player,slot,name) VALUES(?,?,?) "
                    "ON CONFLICT(player,slot) DO UPDATE SET name=excluded.name", (session.player_id, req.scheme_id, value))
    rsp.name = value
    return pb.StatusCode_OK


def preview(session, req, rsp):
    if not check(session, req, rsp):
        return pb.StatusCode_BAD_REQ
    layout = read_scheme(session.player_id, req.scheme_id)
    rsp.furniture_item_id.extend(obj.furniture_item_id for obj in layout or ())
    return pb.StatusCode_OK


def apply(session, req, rsp):
    if not check(session, req, rsp):
        return pb.StatusCode_BAD_REQ
    target = read_scheme(session.player_id, req.scheme_id)
    if target is None:
        return pb.StatusCode_BAD_REQ
    return replace_layout(session, target)


def remove_all(session, req, rsp):
    if not home_owner(session):
        return pb.StatusCode_BAD_REQ
    return replace_layout(session, [])


def replace_layout(session, target):
    pid, channel = session.player_id, session.channel_id
    limit = room_limit(session)
    if limit is not None and len(target) > limit:
        return pb.StatusCode_FurnitureNumLimit
    current = owner_layout(session)
    changes = Counter(obj.furniture_item_id for obj in current)
    changes.subtract(obj.furniture_item_id for obj in target)
    for item, diff in changes.items():
        if not diff:
            continue
        raw = db.get_item_detail(pid, item_id=item)
        balance = 0
        if raw:
            held = pb.ItemDetail.FromString(raw).main_item
            if held.item_tag != pb.EBagItemTag_Furniture or held.WhichOneof("item") != "base_item":
                return pb.StatusCode_CFG_NOT_FOUND
            balance = held.base_item.num
        if balance + diff < 0:
            return pb.StatusCode_ITEM_NOT_ENOUGH
        if balance + diff > 0xffffffff:
            return pb.StatusCode_FurnitureNumLimit

    # Only owner placements enter the ledger. Visitor placements and hand-held items stay separate.
    db.conn.execute("DELETE FROM furnitures WHERE scene_id=? AND channel_id=? AND player_id=?", (HOME, channel, pid))
    blocked = {fid for fid, in db.conn.execute("SELECT furniture_id FROM furnitures WHERE scene_id=? AND channel_id=?",
                                                (HOME, channel))}
    from private_garden_ids import chair_ids
    blocked.update(chair_ids())
    blocked.update(fid for fid, in db.conn.execute("SELECT fid FROM realm_hand_furniture"))
    # A previous application can have remapped a saved ID that now belongs to a visitor.
    # Reuse that live ID on a retry while reserving the IDs of all unblocked saved instances.
    reserved = {obj.furniture_id for obj in target} - blocked
    alternatives = {}
    for obj in current:
        if obj.furniture_id not in reserved and obj.furniture_id not in blocked:
            alternatives.setdefault(obj.furniture_item_id, []).append(obj.furniture_id)
    for obj in target:
        if obj.furniture_id in blocked:
            available = alternatives.get(obj.furniture_item_id, [])
            while available and available[-1] in blocked:
                available.pop()
            if available:
                obj.furniture_id = available.pop()
            else:
                obj.furniture_id = fresh_id(pid, HOME, channel)
                while obj.furniture_id in reserved:
                    obj.furniture_id = fresh_id(pid, HOME, channel)
        db.set_furniture(HOME, channel, pid, obj.furniture_id, obj.SerializeToString())
        blocked.add(obj.furniture_id)
    from private_garden_residents import clear_furniture
    kept={(obj.furniture_id,obj.furniture_item_id) for obj in target}
    for obj in current:
        if (obj.furniture_id,obj.furniture_item_id) not in kept:
            clear_furniture(session,obj.furniture_id)
    pack = pb.PackNotice(status=pb.StatusCode_OK)
    for item, diff in changes.items():
        if diff:
            pack.items.add().CopyFrom(bag_adjust(pid, item, diff))
    tally(pid)
    if pack.items:
        session.send(MsgId.PackNotice, pack, 0)
    catalog = res.index("HomeItem", "home_item", "item_i_d")
    all_items = (pb.FurnitureDetailsInfo.FromString(row[-1]).furniture_item_id for row in db.get_furniture(HOME,channel))
    points = sum(catalog.get(item,{}).get("home_item_point_num",0) for item in all_items)
    publish(session, pb.GardenFurnitureBatchUpdateNotice(status=pb.StatusCode_OK, player_id=pid,
            furniture_point_num=min(points,0xffffffff), new_furniture_list=target))
    return pb.StatusCode_OK
