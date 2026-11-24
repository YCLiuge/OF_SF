"""Home scene chair occupancy. Each native ChairInfo describes one *occupied* seat."""
from proto import net_pb2 as pb
from network.msg_id import MsgId
from server import scene_data
from utils.res_loader import res


def occupants(session):
    return scene_data.room_members(session)


def fill(session, scene):
    if not session.dungeon[0] and scene.scene_id == res["HomeChairSeats"]["scene"]:
        for other in occupants(session):
            seat = other.home_seat
            if seat is not None:
                scene.chair_info_list.add(chair_id=seat[0], seat_id=seat[1], player_id=other.player_id)


def sit(session, req, rsp):
    if req.is_sit and getattr(session, "vehicle_seat", None):
        return pb.StatusCode_PlayerSitOtherChair
    cfg = res["HomeChairSeats"]
    if session.scene_id != cfg["scene"] or session.dungeon[0]:
        return pb.StatusCode_OK  # Preserve the existing acknowledgement for other maps.
    count = cfg["seats"].get(str(req.chair_id))
    if count is None:
        from private_garden_residents import furniture, seat_count, occupant
        from private_channels import owner_for
        placed=furniture(session.scene_id,session.channel_id,req.chair_id)
        count=seat_count(placed[1]) if placed else 0
        owner=owner_for(session.channel_id)
        if req.is_sit and owner and occupant(owner,req.chair_id,req.seat_id):
            return pb.StatusCode_ChairSeatTaken
    if not 0 <= req.seat_id < count:
        return pb.StatusCode_BAD_REQ
    seat = (req.chair_id, req.seat_id)
    peers = occupants(session)
    if req.is_sit:
        if session.home_seat is not None and session.home_seat != seat:
            return pb.StatusCode_PlayerSitOtherChair
        if any(s is not session and s.home_seat == seat for s in peers):
            return pb.StatusCode_ChairSeatTaken
        session.home_seat = seat
    else:
        if session.home_seat is not None and session.home_seat != seat:
            return pb.StatusCode_BAD_REQ
        session.home_seat = None
    rsp.status = pb.StatusCode_OK
    for other in peers:
        if other is not session:
            other.send(MsgId.SceneSitChairRsp, rsp, 0)
    return rsp.status
