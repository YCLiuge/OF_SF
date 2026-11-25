"""Persistent decor attached to the original home's five native room anchors."""
from private_channels import HOME, owner_for
from utils.res_loader import res
from private_garden_actions import publish
from proto import net_pb2 as pb
from utils import db


def valid_slot(key, pos):
    return any(row.get("pos_i_d") == pos for row in
               res.index("HomeItem", "room_decor").get(key, {}).get("pos_decor_info", ()))


def owns_decor(player, key):
    raw = db.get_item_detail(player, key)
    if raw and pb.ItemDetail.FromString(raw).main_item.base_item.num > 0:
        return True
    # The original panel sends the template, and can call TakeBack immediately
    # before applying it. The user's own held or placed furniture is also owned.
    if db.conn.execute("SELECT 1 FROM realm_hand_furniture WHERE player=? AND item=?", (player, key)).fetchone():
        return True
    rows = db.conn.execute("SELECT furniture_detail_blob FROM furnitures WHERE player_id=? AND scene_id=?",
                           (player, HOME))
    return any(pb.FurnitureDetailsInfo.FromString(row[0]).furniture_item_id == key for row in rows)


def fill(session, data):
    if session.scene_id != HOME or session.dungeon[0]:
        return
    owner = owner_for(session.channel_id)
    if owner is None:
        return
    rows = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='room_decor' ORDER BY k", (owner,))
    for (raw,) in rows:
        data.room_decor_list.add().ParseFromString(raw)


def set_decor(session, req, rsp):
    owner = session.player_id
    if session.dungeon[0] or session.scene_id != HOME or owner_for(session.channel_id) != owner:
        return pb.StatusCode_BAD_REQ
    table = res.index("HomeItem", "room_decor")
    if not req.decor_list:
        return pb.StatusCode_BAD_REQ
    changed = {}
    checked = set()
    for item in req.decor_list:
        pos, key = item.pos_id, item.decor_id
        if pos in changed or key != item.item_id:
            return pb.StatusCode_BAD_REQ
        if key:
            if not valid_slot(key, pos):
                return pb.StatusCode_BAD_REQ
            if key not in checked and not owns_decor(owner, key):
                return pb.StatusCode_ITEM_NOT_ENOUGH
            checked.add(key)
        elif not any(valid_slot(template, pos) for template in table):
            return pb.StatusCode_BAD_REQ
        changed[pos] = item
    notice = pb.RoomDecorNotice(status=pb.StatusCode_OK)
    for pos, item in changed.items():
        if item.item_id:
            db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'room_decor',?,?)",
                            (owner,str(pos),item.SerializeToString()))
        else:
            db.conn.execute("DELETE FROM player_kv WHERE player_id=? AND ns='room_decor' AND k=?", (owner,str(pos)))
        notice.decor_list.add().CopyFrom(item)
    publish(session, notice)
    return pb.StatusCode_OK
