"""The retail opening scene's single area device and authoritative windmill handoff."""
from proto import net_pb2 as pb
from utils import db

SCENE = 100
PLAZA = 9999
DEVICE = 101


def set_device(session, area_id, state, result):
    if session.scene_id != SCENE or session.dungeon[0] or area_id != DEVICE:
        return pb.StatusCode_BAD_REQ
    from private_map_progress import rebuild, device_opened
    rebuild(session.player_id)
    raw = db.get_area(session.player_id,SCENE,DEVICE)
    if raw:
        result.area.ParseFromString(raw)
    else:
        result.area.area_id = DEVICE
        result.area.level = 1
    if not raw or result.area.area_state != state:
        result.area.area_state = state
        db.set_area(session.player_id,SCENE,DEVICE,result.area.SerializeToString())
    if state == pb.AreaData.Unlock:
        device_opened(session, area_id)
    return pb.StatusCode_OK


def departed(session):
    # An exit marker lets the client's final delayed telemetry arrive just after its scene change.
    db.conn.execute("UPDATE player_kv SET v='departed' WHERE player_id=? AND ns='profile' "
                    "AND k='bottle_intro' AND v='active'",(session.player_id,))


def is_enrolled(player):
    row = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='profile' AND k='bottle_intro'",
                          (player,)).fetchone()
    return row[0] if row else None
