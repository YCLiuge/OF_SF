"""Persist the PC 1.1.12 prologue's own two Faye-dialog telemetry events."""
from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_first_quest import saved
from proto import net_pb2 as pb
from utils import db

# Direct mappings in the original StatisticSystem.InitP2TutorialKeyToName.
FAYE_EVENTS = {11: "菲对话开始", 12: "菲对话结束"}


def record(session, req):
    phase = req.tutorial_id
    if phase not in FAYE_EVENTS or (req.tutorial_name and req.tutorial_name != FAYE_EVENTS[phase]):
        return pb.StatusCode_BAD_REQ
    if session.scene_id != 9999 or session.dungeon[0]:
        return pb.StatusCode_BAD_REQ
    pid = session.player_id
    seen = {row[0] for row in db.conn.execute(
        "SELECT k FROM player_kv WHERE player_id=? AND ns='tutorial' AND k IN ('11','12') AND CAST(v AS INTEGER)>0",
        (pid,))}
    if str(phase) in seen:
        return pb.StatusCode_OK
    quest = saved(pid,100011)
    if quest is None or quest.status != pb.QuestStatus_InProgress or (phase == 12 and "11" not in seen):
        return pb.StatusCode_BAD_REQ
    db.conn.execute("INSERT OR REPLACE INTO player_kv(player_id,ns,k,v) VALUES(?,'tutorial',?,1)",(pid,str(phase)))
    # Keep the public server's aggregate format while preserving all older completed tutorial IDs.
    row = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='tutorial' AND k='ids'",(pid,)).fetchone()
    stored = row[0] if row else b""
    text = stored.decode("ascii") if isinstance(stored,bytes) else str(stored)
    ids = {k for k in text.split(",") if k.isdigit()}
    ids.update(seen)
    ids.add(str(phase))
    result = ",".join(sorted(ids,key=int)).encode("ascii")
    db.conn.execute("INSERT OR REPLACE INTO player_kv(player_id,ns,k,v) VALUES(?,'tutorial','ids',?)",(pid,result))
    return pb.StatusCode_OK


@packet_handler(MsgId.TutorialReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.TutorialReq.FromString(data)
        session.send(MsgId.TutorialRsp,pb.TutorialRsp(status=record(session,req)),packet_id)
