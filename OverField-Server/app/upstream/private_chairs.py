"""Home scene chair occupancy. Each native ChairInfo describes one *occupied* seat."""
from proto import net_pb2 as pb
from network.msg_id import MsgId
from server import scene_data
from utils.res_loader import res
from realm_uids import wire_uid

# FashionChair.SetPlayerId and the two national pelvis prefabs (avatarType=22).
FASHION_BASE = 100_000_000_000
PELVIS_SLOT = 22


def fashion_id(owner):
    return FASHION_BASE + wire_uid(owner.player_id)*1000 + PELVIS_SLOT


def is_fashion(chair):
    uid, slot = divmod(chair - FASHION_BASE,1000)
    return 0 < uid <= 0xffffffff and slot == PELVIS_SLOT


def wearable(owner):
    item = owner.scene_player.team.char1.outfit_preset.pend_pelvis
    return item if res.index("Fashion","fashion_info").get(item,{}).get("is_can_sit") else 0


def release_fashion(rider, exclude=None):
    state = rider.fashion_seat
    if not state:
        return
    owner, room, _ = state
    rider.fashion_seat = owner.fashion_rider = rider.home_seat = None
    notice = pb.SceneSitChairRsp(status=pb.StatusCode_OK,player_id=rider.player_id,chair_id=fashion_id(owner))
    for peer in rider.social_rooms.get(room,()):
        if peer is not exclude and peer.running and not peer.dungeon[0] and not getattr(peer,"mp_room_team",0):
            peer.send(MsgId.SceneSitChairRsp,notice,0)


def depart_fashion(session):
    if session.fashion_seat:
        release_fashion(session)
    if session.fashion_rider:
        release_fashion(session.fashion_rider)


def refresh_fashion(owner):
    rider = owner.fashion_rider
    if rider and wearable(owner)!=rider.fashion_seat[2]:
        release_fashion(rider)


def occupy_fashion(rider,owner,item):
    room = (rider.scene_id,rider.channel_id)
    rider.fashion_seat = owner,room,item
    rider.home_seat = fashion_id(owner),0
    owner.fashion_rider = rider
    notice = pb.SceneSitChairRsp(status=pb.StatusCode_OK,player_id=rider.player_id,chair_id=fashion_id(owner),is_sit=True)
    for peer in occupants(rider):
        if peer is not rider and not getattr(peer,"mp_room_team",0):
            peer.send(MsgId.SceneSitChairRsp,notice,0)


def sit_fashion(rider,req):
    state = rider.fashion_seat
    if not req.is_sit:
        if not state or req.chair_id!=fashion_id(state[0]):
            return pb.StatusCode_PlayerChairRemoved
        rider.commit_callbacks.append((release_fashion,(rider,rider)))
        return pb.StatusCode_OK
    if rider.dungeon[0] or getattr(rider,"mp_room_team",0) or req.seat_id!=0:
        return pb.StatusCode_BAD_REQ
    if rider.home_seat and rider.home_seat!=(req.chair_id,0) or rider.fashion_rider:
        return pb.StatusCode_PlayerSitOtherChair
    owner = next((peer for peer in occupants(rider) if fashion_id(peer)==req.chair_id and not getattr(peer,"mp_room_team",0)),None)
    if owner is None or owner is rider or not (item:=wearable(owner)):
        return pb.StatusCode_PlayerChairRemoved
    if owner.fashion_rider is rider:
        return pb.StatusCode_OK
    if owner.fashion_rider or owner.fashion_seat:
        return pb.StatusCode_ChairSeatTaken
    rider.commit_callbacks.append((occupy_fashion,(rider,owner,item)))
    return pb.StatusCode_OK


def occupants(session):
    return scene_data.room_members(session)


def fill(session, scene):
    if not session.dungeon[0] and not getattr(session,"mp_room_team",0):
        for other in occupants(session):
            seat = other.home_seat
            if seat is not None and (other.fashion_seat or scene.scene_id == res["HomeChairSeats"]["scene"]):
                scene.chair_info_list.add(chair_id=seat[0], seat_id=seat[1], player_id=other.player_id)


def sit(session, req, rsp):
    if req.is_sit and getattr(session, "vehicle_seat", None):
        return pb.StatusCode_PlayerSitOtherChair
    if is_fashion(req.chair_id):
        return sit_fashion(session,req)
    if session.fashion_seat:
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
