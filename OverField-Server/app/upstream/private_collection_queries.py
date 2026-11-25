"""Private ownership history and the two native moon collection operations."""
from google.protobuf.message import DecodeError
from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils.res_loader import res
from utils import db


def positive_item(blob):
    try:
        item=pb.ItemDetail.FromString(blob).main_item
    except DecodeError:
        return 0
    group=item.WhichOneof("item")
    if not group or (group=="base_item" and item.base_item.num<=0):
        return 0
    return item.item_id


def remember_item(player, blob):
    item=positive_item(blob)
    if item:
        db.conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,'got_ids',?)",
                        (player,str(item)))


def owned_ids(session,req,rsp):
    player=session.player_id
    known={int(k) for k, in db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system='got_ids'",
                                            (player,)) if str(k).isdecimal() and 0<int(k)<=0xffffffff}
    # Migrate positive current inventory and the roster even if they predate the history ledger.
    actual={positive_item(b) for b in db.get_item_detail(player)}
    actual.update(pb.Character.FromString(b).character_id for b in db.get_characters(player))
    actual.discard(0)
    db.conn.executemany("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,'got_ids',?)",
                        [(player,str(i)) for i in actual-known])
    rsp.item_ids.extend(sorted(actual | known))
    return pb.StatusCode_OK


def moon_scene(mid, kind):
    config=res.index("CollectionItem","collection_item").get(mid)
    if not config or config.get("new_collection_type")!=kind:
        return None
    # The original 11001xxx–11005xxx world families are verified by of-ps and the PC table.
    family=mid//1000
    if kind==10 and 11001<=family<=11005:
        return family-11000
    if kind==11 and family//100 in (111,112) and 1<=family%100<=5:
        return family%100
    return None


def mark(session,category,mid):
    return db.conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,?,?)",
                           (session.player_id,category,str(mid))).rowcount


def collect_moon(session,req,rsp):
    mid=req.moon_id
    rsp.moon_id=mid
    if session.dungeon[0] or moon_scene(mid,10)!=session.scene_id:
        return pb.StatusCode_BAD_REQ
    if mid in moon_ids(session.player_id,"collected_moons") or not mark(session,"collected_moons",mid):
        return pb.StatusCode_BAD_REQ
    # Of-ps' national PC collection result is five of template 124.
    from private_shop import award
    delivery=pb.ShopBuyRsp()
    pack=pb.PackNotice(status=pb.StatusCode_OK)
    award(session.player_id,{124:5},delivery,pack)
    rsp.rewards.extend(delivery.items)
    session.send(MsgId.PackNotice,pack,0)
    return pb.StatusCode_OK


def interact_moon(session,req,rsp):
    mid=req.emotion_moon_id
    if session.dungeon[0] or moon_scene(mid,11)!=session.scene_id:
        return pb.StatusCode_BAD_REQ
    mark(session,"emotion_moons",mid)
    rsp.info.moon_id=mid
    rsp.info.interacted=True
    return pb.StatusCode_OK


def moon_ids(player,category):
    values={int(k) for k, in db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system=?",
                                             (player,category)) if str(k).isdecimal()}
    old=db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns=? AND k='ids'",(player,category)).fetchone()
    if old:
        raw=old[0].decode("ascii",errors="ignore") if isinstance(old[0],bytes) else str(old[0])
        values.update(int(i) for i in raw.split(",") if i.isascii() and i.isdecimal() and len(i)<=10)
    return values


def moon_info(session,req,rsp):
    rsp.scene_id=req.scene_id
    for mid in sorted(moon_ids(session.player_id,"collected_moons")):
        if moon_scene(mid,10)==req.scene_id:
            rsp.collected_moon_ids.append(mid)
    for mid in sorted(moon_ids(session.player_id,"emotion_moons")):
        if moon_scene(mid,11)==req.scene_id:
            rsp.emotion_moons.add(moon_id=mid,interacted=True)
    return pb.StatusCode_OK
