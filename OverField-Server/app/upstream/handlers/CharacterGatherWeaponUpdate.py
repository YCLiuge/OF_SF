from network.msg_id import MsgId
from private_dungeons import _table
from private_equipment import load
from private_request import handler
from proto import net_pb2 as pb
from server.scene_data import room_members
from utils import db


def update(session, req, rsp):
    raw = db.get_characters(session.player_id,req.character_id) if req.character_id else None
    if not raw:
        return pb.StatusCode_CharNotExist
    actor = pb.Character.FromString(raw[0])
    if req.weapon_id:
        item = load(session.player_id,req.weapon_id,"weapon")
        if not item or _table("Weapon","weapon").get(item.main_item.weapon.weapon_id,{}).get("new_weapon_system_type") != 6:
            return pb.StatusCode_GATHER_WEAPON_NOT_WEAR
    if actor.gather_weapon == req.weapon_id:
        return pb.StatusCode_OK
    actor.gather_weapon = req.weapon_id
    db.set_character(session.player_id,req.character_id,actor.SerializeToString())
    team = session.scene_player.team
    members = (team.char1,team.char2,team.char3)
    for member in members:
        if member.char_id == req.character_id:
            session.commit_callbacks.append((setattr,(member,"gather_weapon",req.weapon_id)))
    if session.dungeon[0]:
        lead = getattr(session,"dungeon_lead",None)
        if lead is not None and lead.char_id == req.character_id:
            session.commit_callbacks.append((setattr,(lead,"gather_weapon",req.weapon_id)))
    elif any(member.char_id == req.character_id for member in members):
        notice = pb.ServerSceneSyncDataNotice(status=pb.StatusCode_OK)
        event = notice.data.add(player_id=session.player_id).server_data.add(action_type=pb.SceneActionType_UPDATE_EQUIP)
        event.player.CopyFrom(session.scene_player)
        team = event.player.team
        next(m for m in (team.char1,team.char2,team.char3) if m.char_id == req.character_id).gather_weapon=req.weapon_id
        for peer in room_members(session):
            if peer is not session and getattr(peer,"mp_room_team",0) == getattr(session,"mp_room_team",0):
                peer.send(MsgId.ServerSceneSyncDataNotice,notice,0)
    return pb.StatusCode_OK


Handler = handler("CharacterGatherWeaponUpdate", update, replay=True)
