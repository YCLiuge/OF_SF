"""Shared campfires, using the national action materials, lifetimes and scene range."""
from collections import defaultdict
import time

from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_request import cached_mutation
from private_crafts import payments, save_pack
from utils.res_loader import res
from proto import net_pb2 as pb
from utils import db


def now_ms():
    return time.time_ns() // 1_000_000


class Campfires:
    def __init__(self, broadcast):
        self.broadcast = broadcast
        config = res.index("GlobalConfig", "config", "key")
        lifetimes = dict(map(int, pair.split(":")) for pair in config["BonfireTime"]["values"])
        self.materials = {}
        for row in res.index("Expression", "expression").values():
            for group in row["expression_group_info"]:
                action = group.get("action_i_d")
                if action in lifetimes:
                    self.materials[action] = (group["need_item_i_d"], group["need_item_count"], lifetimes[action])
        if self.materials.keys() != lifetimes.keys():
            raise ValueError("Missing expression materials for a campfire action")
        # Scene coordinates are stored as hundredths of a game unit.
        self.radius = int(config["CampsiteDetectionRadius"]["values"][0]) * 100
        db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS realm_campfires(
                id INTEGER PRIMARY KEY AUTOINCREMENT, player INTEGER NOT NULL, scene INTEGER NOT NULL,
                channel INTEGER NOT NULL, expires INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,
                z INTEGER NOT NULL, body BLOB NOT NULL);
            CREATE INDEX IF NOT EXISTS campfires_room ON realm_campfires(scene, channel, x);
            CREATE INDEX IF NOT EXISTS campfires_expiry ON realm_campfires(expires);
        """)
        self.next_expiry = self.first_expiry()

    @staticmethod
    def first_expiry():
        return db.conn.execute("SELECT MIN(expires) FROM realm_campfires").fetchone()[0]

    def schedule(self, expires):
        if self.next_expiry is None or expires < self.next_expiry:
            self.next_expiry = expires

    def due(self):
        deadline = self.next_expiry
        return deadline is not None and now_ms() >= deadline

    def expire(self):
        now = now_ms()
        if self.next_expiry is None or now < self.next_expiry:
            return
        removed = defaultdict(list)
        with db.conn:
            for fid, scene, channel in db.conn.execute(
                    "SELECT id,scene,channel FROM realm_campfires WHERE expires<=? ORDER BY expires,id", (now,)):
                removed[scene, channel].append(fid)
            db.conn.execute("DELETE FROM realm_campfires WHERE expires<=?", (now,))
            upcoming = self.first_expiry()
        self.next_expiry = upcoming
        for room, ids in removed.items():
            self.broadcast(room, MsgId.RemoveCampFireNotice,
                           pb.RemoveCampFireNotice(status=pb.StatusCode_OK, camp_fire_id=ids))
        return bool(removed)

    def snapshot(self, session, data):
        if not session.dungeon[0]:
            rows = db.conn.execute("SELECT id,body FROM realm_campfires WHERE scene=? AND channel=? AND expires>? ORDER BY id",
                                   (session.scene_id, session.channel_id, now_ms()))
            for fid, raw in rows:
                fire = data.camp_fires.add()
                fire.ParseFromString(raw)
                fire.id = fid

    def place(self, session, req, rsp, notices):
        spec = self.materials.get(req.action_id)
        if spec is None:
            return pb.StatusCode_CFG_NOT_FOUND
        if session.dungeon[0] or not req.HasField("pos"):
            return pb.StatusCode_BAD_REQ
        now = now_ms()
        room = (session.scene_id, session.channel_id)
        p = req.pos
        # Narrow by the indexed room/x range before testing the 3-D distance.
        nearby = db.conn.execute("""SELECT x,y,z FROM realm_campfires
            WHERE scene=? AND channel=? AND x BETWEEN ? AND ? AND expires>?""",
                                 (*room, p.x - self.radius, p.x + self.radius, now))
        if any((p.x-x)**2 + (p.y-y)**2 + (p.z-z)**2 < self.radius**2 for x, y, z in nearby):
            return pb.StatusCode_CAMP_FIRE_TOO_CLOSE
        material, count, seconds = spec
        changes, status = payments(session.player_id, {material: count})
        if changes is None:
            return status
        expires = now + seconds * 1000
        fire = rsp.camp_fire
        fire.player_id = session.player_id
        fire.action_id = req.action_id
        fire.pos.CopyFrom(p)
        fire.rot.CopyFrom(req.rot)
        fire.place_time = now
        fire.id = db.conn.execute("""INSERT INTO realm_campfires(player,scene,channel,expires,x,y,z,body)
            VALUES(?,?,?,?,?,?,?,?)""", (session.player_id, *room, expires, p.x, p.y, p.z,
                                         fire.SerializeToString())).lastrowid
        save_pack(session, changes)
        notice = pb.PlaceCampFireNotice(status=pb.StatusCode_OK, camp_fires=[fire])
        session.commit_callbacks.append((self.schedule, (expires,)))
        notices.append((room, MsgId.PlaceCampFireNotice, notice))
        return pb.StatusCode_OK

    def remove(self, session, req, rsp, notices):
        if session.dungeon[0] or not req.camp_fire_id:
            return pb.StatusCode_BAD_REQ
        room = (session.scene_id, session.channel_id)
        if not db.conn.execute("""DELETE FROM realm_campfires
                WHERE id=? AND player=? AND scene=? AND channel=? AND expires>?""",
                (req.camp_fire_id, session.player_id, *room, now_ms())).rowcount:
            return pb.StatusCode_BAD_REQ
        notice = pb.RemoveCampFireNotice(status=pb.StatusCode_OK, camp_fire_id=[req.camp_fire_id])
        notices.append((room, MsgId.RemoveCampFireNotice, notice))
        return pb.StatusCode_OK


def campfire_handler(name, operation):
    @packet_handler(getattr(MsgId, name + "Req"))
    class Handler(PacketHandler):
        def handle(self, session, data, packet_id):
            notices = []
            def apply(session, request, reply):
                return operation(session.campfires, session, request, reply, notices)
            cached_mutation(session, name, data, packet_id, apply)
            # The caller receives its reply before the room sees the change. Replays have no notices.
            for room, msg_id, notice in notices:
                session.campfires.broadcast(room, msg_id, notice)
    return Handler
