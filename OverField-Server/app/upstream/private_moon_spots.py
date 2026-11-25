"""Scene-scoped decorative wish beacons placed by this realm's operators."""
import time

from network.msg_id import MsgId
from private_vehicle_seats import available, world
from proto import net_pb2 as pb
from realm_gm import audit
from realm_uids import public_uid
from utils import db
from utils.res_loader import res


def setup(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS private_moon_spots (
        instance INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER NOT NULL, scene INTEGER NOT NULL,
        channel INTEGER NOT NULL, expires INTEGER NOT NULL, details BLOB NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS moon_spots_room ON private_moon_spots(scene,channel,expires)")
    conn.execute("CREATE INDEX IF NOT EXISTS moon_spots_expiry ON private_moon_spots(expires)")


def now_ms():
    return int(time.time() * 1000)


def snapshot(session, scene):
    if session.dungeon[0] or getattr(session, "mp_room_team", 0):
        return
    for details, in db.conn.execute("""SELECT details FROM private_moon_spots
            WHERE scene=? AND channel=? AND expires>? ORDER BY instance""", (*world(session), now_ms())):
        scene.moon_spots.add().ParseFromString(details)


def publish(rooms, key, operator_id, operation, data):
    notice = pb.PlaceMoonSpotNotice(status=pb.StatusCode_OK, operate=operation, player_id=operator_id, data=data)
    for peer in rooms.get(key, ()):
        if available(peer) and world(peer) == key:
            peer.send(MsgId.PlaceMoonSpotNotice, notice, 0)


def expire(rooms):
    stamp = now_ms()
    rows = db.conn.execute("""SELECT owner,scene,channel,details FROM private_moon_spots
                              WHERE expires<=? ORDER BY expires,instance""", (stamp,)).fetchall()
    if rows:
        db.conn.execute("DELETE FROM private_moon_spots WHERE expires<=?", (stamp,))
        for owner, scene, channel, details in rows:
            publish(rooms, (scene, channel), owner, pb.OperateType_Remove, pb.MoonSpotData.FromString(details))


def change(session, operation, source):
    if not available(session) or session.scene_membership != world(session):
        return pb.StatusCode_BAD_REQ, None
    scene, channel = world(session)
    stamp = now_ms()
    if operation in (pb.OperateType_Place, pb.OperateType_Move) and (
            not source.HasField("pos") or not source.HasField("rot") or
            source.pos.decimal_places > 6 or source.rot.decimal_places > 6):
        return pb.StatusCode_BAD_REQ, None
    if operation == pb.OperateType_Place:
        template = res.index("MoonSpot", "moon_spot").get(source.id)
        if not template or source.instance_id or source.create_time:
            return pb.StatusCode_BAD_REQ, None
        count = db.conn.execute("SELECT count(*) FROM private_moon_spots WHERE scene=? AND channel=? AND expires>?",
                                (scene, channel, stamp)).fetchone()[0]
        if count >= 50:
            return pb.StatusCode_BAD_REQ, None
        expires = stamp + 1000 * template["recycle_seconds"]
        cursor = db.conn.execute("""INSERT INTO private_moon_spots(owner,scene,channel,expires,details)
                                    VALUES(?,?,?,?,?)""", (session.player_id, scene, channel, expires, b""))
        data = pb.MoonSpotData(id=source.id, instance_id=cursor.lastrowid, create_time=stamp)
    elif operation in (pb.OperateType_Remove, pb.OperateType_Move):
        row = db.conn.execute("""SELECT details FROM private_moon_spots
                              WHERE instance=? AND owner=? AND scene=? AND channel=? AND expires>?""",
                              (source.instance_id, session.player_id, scene, channel, stamp)).fetchone()
        if not row:
            return pb.StatusCode_BAD_REQ, None
        data = pb.MoonSpotData.FromString(row[0])
    else:
        return pb.StatusCode_BAD_REQ, None
    if operation == pb.OperateType_Remove:
        db.conn.execute("DELETE FROM private_moon_spots WHERE instance=?", (data.instance_id,))
    else:
        data.pos.CopyFrom(source.pos)
        data.rot.CopyFrom(source.rot)
        db.conn.execute("UPDATE private_moon_spots SET details=? WHERE instance=?", (data.SerializeToString(), data.instance_id))
    publish(session.social_rooms, (scene, channel), session.player_id, operation, data)
    return pb.StatusCode_OK, data


def native(session, body, packet_id):
    from private_gm import current_grant
    from private_request import cache_after_commit
    from hashlib import blake2s
    grant = current_grant(session)
    res_code = pb.StatusCode_FAIL
    if grant:
        key = (grant[0], packet_id, blake2s(body).digest())
        if packet_id and key in getattr(session, "moon_receipts", {}):
            res_code = pb.StatusCode_OK
        else:
            req = pb.PlaceMoonSpotReq.FromString(body)
            res_code, data = change(session, req.operate, req.data)
            audit(db.conn, public_uid(session.player_id), "native", "moonspot", "ok" if data else "rejected",
                  f"operation={req.operate} instance={data.instance_id}" if data else "")
            if data and packet_id:
                cache_after_commit(session, "moon_receipts", key, True)
    session.send(MsgId.PlaceMoonSpotRsp, pb.PlaceMoonSpotRsp(status=res_code), packet_id)


def command(session, arg, reply):
    parts = arg.split()
    if parts == ["place"]:
        operation, source = pb.OperateType_Place, pb.MoonSpotData(id=1)
    elif len(parts) == 2 and parts[0] in ("move", "remove") and parts[1].isascii() and parts[1].isdecimal() and len(parts[1]) <= 10 and 0 < int(parts[1]) <= 0xffffffff:
        operation = pb.OperateType_Move if parts[0] == "move" else pb.OperateType_Remove
        source = pb.MoonSpotData(instance_id=int(parts[1]))
    else:
        return None
    if operation != pb.OperateType_Remove:
        actor = session.scene_player.team.char1
        source.pos.CopyFrom(actor.pos)
        source.rot.CopyFrom(actor.rot)
    status, data = change(session, operation, source)
    if status != pb.StatusCode_OK:
        return None
    reply.result = f"祈愿石信标 #{data.instance_id}"
    return f"operation={operation} instance={data.instance_id}"
