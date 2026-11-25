"""Native profile settings and owned character outfits for a private realm."""
from network.msg_id import MsgId
from private_request import handler
from private_chat_text import chat_text
from utils.res_loader import res
from proto import net_pb2 as pb
from server.scene_data import get_session
from utils import db


def phone_case(player_id):
    row=db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='player' AND k='phone_case'",(player_id,)).fetchone()
    return int(row[0]) if row else 0


def owned(player,item,tag):
    if not item or res.index("Item","item").get(item,{}).get("new_bag_item_tag")!=tag:
        return False
    raw=db.get_item_detail(player,item)
    if not raw:
        return False
    info=pb.ItemDetail.FromString(raw).main_item
    return info.item_id==item and info.item_tag==tag and info.HasField("base_item") and info.base_item.num>0


def change_sign(session,req,rsp):
    value=chat_text(req.sign)
    limit=int(res.index("GlobalConfig","config","key")["Player_Sign_Text_Cout"]["values"][0])
    if value is None or len(value.encode("utf-16-le"))//2>limit or not session.chat_guard.allows(value):
        return pb.StatusCode_BAD_REQ
    rsp.sign=value
    db.set_players_info(session.player_id,"sign",value)
    return pb.StatusCode_OK


def unlock_head_list(session,req,rsp):
    rsp.heads.extend(session.entitlements.head_stickers)
    return pb.StatusCode_OK


def change_head(session,req,rsp):
    if req.head not in session.entitlements.head_stickers:
        return pb.StatusCode_BAD_REQ
    db.conn.execute("UPDATE players SET head=? WHERE player_id=? AND head IS NOT ?",
                    (req.head,session.player_id,req.head))
    from private_request import set_after_commit
    set_after_commit(session,"avatar_id",req.head)
    rsp.head=req.head
    return pb.StatusCode_OK


def change_background(session,req,rsp):
    key=req.phone_background
    if key!=8000 and not owned(session.player_id,key,pb.EBagItemTag_WallPaper):
        return pb.StatusCode_PHONE_BACKGROUND_NOT_UNLOCK
    db.set_players_info(session.player_id,"phone_background",key)
    rsp.phone_background=key
    return pb.StatusCode_OK


def change_case(session,req,rsp):
    key=req.phone_case
    if key and (not res.index("Phone","phone").get(key,{}).get("asset_path")
                or key!=7000 and not owned(session.player_id,key,pb.EBagItemTag_PhoneCase)):
        return pb.StatusCode_BAD_REQ
    db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'player','phone_case',?)",(session.player_id,key))
    rsp.phone_case=key
    publish(session,pb.SceneActionType_UPDATE_PHONE_CASE)
    return pb.StatusCode_OK


def player_appearance(session,req,rsp):
    value=req.appearance
    if not req.HasField("appearance") or (value.avatar_frame and not owned(session.player_id,value.avatar_frame,pb.EBagItemTag_AvatarFrame)
            or value.pendant and not owned(session.player_id,value.pendant,pb.EBagItemTag_Pendant)):
        return pb.StatusCode_BAD_REQ
    db.conn.execute("UPDATE players SET avatar_frame=?,pendant=? WHERE player_id=?",(value.avatar_frame,value.pendant,session.player_id))
    rsp.appearance.CopyFrom(value)
    publish(session,pb.SceneActionType_UPDATE_APPEARANCE)
    return pb.StatusCode_OK


def publish(session,change):
    from utils.pb_create import make_ScenePlayer
    make_ScenePlayer(session)
    broadcast(session, change)


def broadcast(session, change, outfit_update=None):
    notice=pb.ServerSceneSyncDataNotice(status=pb.StatusCode_OK)
    record=notice.data.add(player_id=session.player_id).server_data.add(action_type=change)
    record.player.CopyFrom(session.scene_player)
    if outfit_update is not None:
        slot,outfit=outfit_update
        getattr(record.player.team,slot).outfit_preset.CopyFrom(outfit)
    for other in get_session():
        if (other.scene_id,other.channel_id,other.dungeon[0])==(session.scene_id,session.channel_id,session.dungeon[0]):
            other.send(MsgId.ServerSceneSyncDataNotice,notice,0)


WEAR_SLOTS=("hat","hair","clothes","ornament","pend_top","pend_chest","pend_pelvis","pend_up_face","pend_down_face",
            "pend_left_hand","pend_right_hand","pend_left_foot","pend_right_foot")


def valid_preset(player,char_id,preset):
    sex=res.index("Character","character").get(char_id,{}).get("new_sex_type")
    fashion=res.index("Fashion","fashion_info")
    for part,slot in enumerate(WEAR_SLOTS):
        key=getattr(preset,slot)
        index=getattr(preset,slot+"_dye_scheme_index")
        if not key:
            if index:return False
            continue
        row=fashion.get(key,{})
        category=row.get("new_fashion_type",0)
        if (not row.get("asset_full_name") or row.get("only_character_i_d") not in (None,0,char_id)
                or row.get("new_sex_type") not in (None,0,sex)
                or (category<3 if slot=="ornament" else category!=part)):
            return False
        raw=db.get_item_detail(player,key)
        if not raw:return False
        data=pb.ItemDetail.FromString(raw).main_item
        if (not data.HasField("outfit") or data.outfit.outfit_id!=key or index>=len(data.outfit.dye_schemes)
                or not data.outfit.dye_schemes[index].is_un_lock):
            return False
    return True


def modify_outfit(session,req,rsp):
    player=session.player_id
    if not req.char_id:return pb.StatusCode_BAD_REQ
    rows=db.get_characters(player,req.char_id)
    if not rows:return pb.StatusCode_BAD_REQ
    character=pb.Character.FromString(rows[0])
    def worn():
        i = character.in_use_outfit_preset_index
        if i >= len(character.outfit_presets):
            return ()
        preset = character.outfit_presets[i]
        return (preset.hat, preset.hair, preset.clothes, preset.ornament)
    before = worn()
    if isinstance(req,pb.OutfitPresetUpdateReq):
        value=req.preset
        if not req.HasField("preset") or value.preset_index>=len(character.outfit_presets) or not valid_preset(player,req.char_id,value):
            return pb.StatusCode_BAD_REQ
        character.outfit_presets[value.preset_index].CopyFrom(value)
        rsp.preset.CopyFrom(value)
    elif isinstance(req,pb.OutfitPresetSwitchReq):
        if req.use_preset_index>=len(character.outfit_presets):return pb.StatusCode_BAD_REQ
        target=character.outfit_presets[req.use_preset_index]
        if not valid_preset(player,req.char_id,target):return pb.StatusCode_BAD_REQ
        rsp.use_preset_index=character.in_use_outfit_preset_index=req.use_preset_index
    else:
        if not req.HasField("hide_info") or character.in_use_outfit_preset_index>=len(character.outfit_presets):
            return pb.StatusCode_BAD_REQ
        character.outfit_presets[character.in_use_outfit_preset_index].outfit_hide_info.CopyFrom(req.hide_info)
        rsp.hide_info.CopyFrom(req.hide_info)
    rsp.char_id=req.char_id
    db.set_character(player,req.char_id,character.SerializeToString())
    if worn() != before:
        from private_newcomer import record
        record(session, 127)
    # The team snapshot also carries live poses, equipment and active buffs.
    for slot in ("char1","char2","char3"):
        member=getattr(session.scene_player.team,slot)
        if member.char_id==req.char_id:
            from utils.pb_create import active_outfit,make_SceneCharacterOutfitPreset
            outfit=make_SceneCharacterOutfitPreset(player,active_outfit(character))
            session.commit_callbacks.append((member.outfit_preset.CopyFrom,(outfit,)))
            broadcast(session,pb.SceneActionType_UPDATE_FASHION,(slot,outfit))
            break
    return pb.StatusCode_OK
