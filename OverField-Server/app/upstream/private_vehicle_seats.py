"""Passenger places in the active party leader's vehicle, scoped to a live scene/channel."""
from network.msg_id import MsgId
from private_equipment import load
from proto import net_pb2 as pb


# National PC Cars prefabs, 18 inspected: each of these has two ChairControllers.
# VehicleEntity.InitChair assigns successive controller indices from zero.
SEATS = dict.fromkeys((1009001, 1009002, 1009003, 1009006, 1009007, 1009015), 2)


def vehicle(owner):
    slot = owner.scene_player.team.char1.character_appearance.vehicle_instance_id
    detail = load(owner.player_id, slot, "weapon") if slot else None
    if detail:
        weapon = detail.main_item.weapon
        if weapon.weapon_id == detail.main_item.item_id and weapon.weapon_id in SEATS:
            return slot, weapon.weapon_id
    return None


def world(session):
    return (session.scene_id, session.channel_id)


def available(session):
    return session.running and session.logged_in and not session.dungeon[0] and not getattr(session, "mp_room_team", 0)


def broadcast(rider, owner, seat, room, sitting, exclude=None):
    notice = pb.SceneSitVehicleRsp(status=pb.StatusCode_OK, player_id=rider.player_id, chair_id=owner.player_id,
                                   seat_id=seat, is_sit=sitting)
    for peer in rider.social_rooms.get(room, ()):
        if peer is not exclude and available(peer) and world(peer) == room:
            peer.send(MsgId.SceneSitVehicleRsp, notice, 0)


def release(rider, exclude=None):
    current = rider.vehicle_seat
    if current is None:
        return
    owner, seat, room, equipped = current
    rider.vehicle_seat = None
    owner.vehicle_riders.pop(seat, None)
    broadcast(rider, owner, seat, room, False, exclude)


def depart(session):
    release(session)
    for rider in tuple(session.vehicle_riders.values()):
        release(rider)


def refresh_equipment(owner):
    if owner.vehicle_riders:
        equipped = vehicle(owner)
        for rider in tuple(owner.vehicle_riders.values()):
            if rider.vehicle_seat[3] != equipped:
                release(rider)


def occupy(rider, owner, seat, room, equipped):
    rider.vehicle_seat = (owner, seat, room, equipped)
    owner.vehicle_riders[seat] = rider
    broadcast(rider, owner, seat, room, True, rider)


def update(session, req, rsp):
    if not available(session):
        return pb.StatusCode_BAD_REQ
    room = world(session)
    rsp.player_id, rsp.chair_id, rsp.seat_id, rsp.is_sit = session.player_id, req.chair_id, req.seat_id, req.is_sit
    current = session.vehicle_seat
    if not req.is_sit:
        if current is None or current[0].player_id != req.chair_id or current[2] != room:
            return pb.StatusCode_PlayerChairRemoved
        # The original leave button sends seat zero even when the rider occupies seat one.
        rsp.seat_id = current[1]
        session.commit_callbacks.append((release, (session, session)))
        return pb.StatusCode_OK

    if session.home_seat or current and (current[0].player_id != req.chair_id or current[1] != req.seat_id):
        return pb.StatusCode_PlayerSitOtherChair
    owner = next((peer for peer in session.social_rooms.get(room, ()) if peer.player_id == req.chair_id
                  and world(peer) == room and available(peer)), None)
    if owner is None:
        return pb.StatusCode_PlayerChairRemoved
    equipped = vehicle(owner)
    if not equipped:
        return pb.StatusCode_PlayerChairRemoved
    if not 0 <= req.seat_id < SEATS[equipped[1]]:
        return pb.StatusCode_BAD_REQ
    other = owner.vehicle_riders.get(req.seat_id)
    if other is session:
        return pb.StatusCode_OK
    if other is not None:
        return pb.StatusCode_ChairSeatTaken
    session.commit_callbacks.append((occupy, (session, owner, req.seat_id, room, equipped)))
    return pb.StatusCode_OK


def fill(session, scene):
    if not available(session):
        return
    room = world(session)
    mounted = {}
    for owner in session.social_rooms.get(room, ()):
        if not owner.vehicle_riders or not available(owner) or world(owner) != room:
            continue
        occupied = {seat:rider.player_id for seat,rider in owner.vehicle_riders.items()
                    if available(rider) and world(rider) == room and rider.vehicle_seat[2] == room}
        if occupied:
            scene.vehicle_info_list.add(chair_id=owner.player_id).seat_info.update(occupied)
            rider = next(iter(owner.vehicle_riders.values()))
            mounted[owner.player_id] = rider.vehicle_seat[3][1]
    if mounted:
        for player in scene.players:
            if player.player_id in mounted:
                player.vehicle_item_id = mounted[player.player_id]
