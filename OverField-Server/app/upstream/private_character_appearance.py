"""Validate the native ten-slot character appearance snapshot against this player's inventory."""
from network.msg_id import MsgId
from private_appearance import owned
from utils.res_loader import res
from private_equipment import load
from proto import net_pb2 as pb
from utils import db


# The previous character constructor recorded seven personal starter items as templates.
# Retain those issued to this role when restoring a slot after an explicit unequip.
STARTER = {"umbrella_id":4050, "insect_net_instance_id":1004001, "logging_axe_instance_id":1002001,
           "water_bottle_instance_id":1005001, "mining_hammer_instance_id":1003001,
           "collection_gloves_instance_id":1001001, "fishing_rod_instance_id":1006001}
TOOLS = {"insect_net_instance_id":7, "logging_axe_instance_id":5, "water_bottle_instance_id":8,
         "mining_hammer_instance_id":6, "collection_gloves_instance_id":4, "fishing_rod_instance_id":18,
         "vehicle_instance_id":21, "shovel_instance_id":22}


def valid_tool(player,instance,kind):
    detail=load(player,instance,"weapon")
    if not detail:
        return False
    template=detail.main_item.weapon.weapon_id
    config=res.index("Weapon","weapon").get(template,{})
    return (detail.main_item.item_id==template and config.get("new_weapon_type")==kind
            and config.get("new_weapon_system_type")==6)


def update(session,req,rsp):
    player=session.player_id
    raw=db.get_characters(player,req.char_id) if req.char_id else None
    if not raw:
        return pb.StatusCode_CharNotExist
    if not req.HasField("appearance"):
        return pb.StatusCode_BAD_REQ
    char=pb.Character.FromString(raw[0])
    current=char.character_appearance
    value=req.appearance
    issued={template for slot,template in STARTER.items() if getattr(current,slot)==template}
    previous={int(key) for key, in db.conn.execute("""SELECT key FROM claimed_rewards
        WHERE player_id=? AND system='appearance_starter'""",(player,))}
    legacy=issued|previous
    if value.badge not in (0,5000) and not owned(player,value.badge,pb.EBagItemTag_Badge):
        return pb.StatusCode_BAD_REQ
    if value.umbrella_id:
        umbrella=res.index("Umbrella","umbrella").get(value.umbrella_id)
        if (not umbrella or umbrella.get("item_i_d")!=value.umbrella_id or
                value.umbrella_id not in legacy and not owned(player,value.umbrella_id,pb.EBagItemTag_Umbrella)):
            return pb.StatusCode_BAD_REQ
    for slot,kind in TOOLS.items():
        key=getattr(value,slot)
        if not key or key==STARTER.get(slot) and key in legacy:
            continue
        if not valid_tool(player,key,kind):
            return pb.StatusCode_BAD_REQ
    rsp.char_id=req.char_id
    rsp.appearance.CopyFrom(value)
    if value==current:
        return pb.StatusCode_OK
    db.conn.executemany("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,'appearance_starter',?)",
                        [(player,str(template)) for template in issued-previous])
    changed_umbrella=bool(value.umbrella_id and current.umbrella_id!=value.umbrella_id)
    current.CopyFrom(value)
    db.set_character(player,req.char_id,char.SerializeToString())
    team=session.scene_player.team
    member=next((a for a in (team.char1,team.char2,team.char3) if a.char_id==req.char_id),None)
    from private_newcomer import record
    if changed_umbrella:
        record(session,144,(2,))
    if team.char1.char_id==req.char_id:
        db.set_players_info(player,"team_leader_badge",value.badge)
        record(session,143)
    own_notice=pb.CharacterAppearanceUpdateNotice(status=pb.StatusCode_OK)
    own_notice.appearance.CopyFrom(value)
    session.send(MsgId.CharacterAppearanceUpdateNotice,own_notice,0)
    if member:
        # Message 1674 has no actor or player id. Other clients receive an identified scene update.
        session.commit_callbacks.append((member.character_appearance.CopyFrom,(value,)))
        if req.char_id == team.char1.char_id and session.vehicle_riders:
            from private_vehicle_seats import refresh_equipment
            session.commit_callbacks.append((refresh_equipment, (session,)))
        if not session.dungeon[0]:
            out=pb.ServerSceneSyncDataNotice(status=pb.StatusCode_OK)
            scene=out.data.add(player_id=player).server_data.add(action_type=pb.SceneActionType_UPDATE_APPEARANCE)
            scene.player.CopyFrom(session.scene_player)
            peers=scene.player.team
            actor=next(a for a in (peers.char1,peers.char2,peers.char3) if a.char_id==req.char_id)
            actor.character_appearance.CopyFrom(value)
            room=getattr(session,"mp_room_team",0)
            for peer in session.social_rooms.get((session.scene_id,session.channel_id),()):
                if peer is not session and not peer.dungeon[0] and getattr(peer,"mp_room_team",0)==room:
                    peer.send(MsgId.ServerSceneSyncDataNotice,out,0)
    return pb.StatusCode_OK
