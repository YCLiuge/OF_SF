"""Home garden residents occupy checked furniture seats and retain the owner's native outfit."""
from network.msg_id import MsgId
from private_arcade_wallet import wallet
from private_channels import HOME, owner_for
from private_dungeons import _table
from proto import net_pb2 as pb
from realm_uids import public_uid
from utils import db
from utils.pb_create import active_outfit, make_SceneCharacterOutfitPreset
from utils.res_loader import res


def setup():
    db.conn.execute("""CREATE TABLE IF NOT EXISTS garden_residents(
        owner INTEGER NOT NULL,character INTEGER NOT NULL,furniture INTEGER NOT NULL,seat INTEGER NOT NULL,
        PRIMARY KEY(owner,character),UNIQUE(owner,furniture,seat)) WITHOUT ROWID""")


def find(owner,character):
    return db.conn.execute("SELECT furniture,seat FROM garden_residents WHERE owner=? AND character=?",
                           (owner,character)).fetchone()


def furniture(scene,channel,instance):
    row=db.conn.execute("SELECT player_id,furniture_detail_blob FROM furnitures WHERE scene_id=? AND channel_id=? AND furniture_id=?",
                        (scene,channel,instance)).fetchone()
    if row:
        return row[0],pb.FurnitureDetailsInfo.FromString(row[1]).furniture_item_id
    return None


def seat_count(template):
    return res["GardenCharacterSeats"]["seats"].get(str(template),0)


def occupant(owner,fid,seat):
    return db.conn.execute("SELECT character FROM garden_residents WHERE owner=? AND furniture=? AND seat=?",
                           (owner,fid,seat)).fetchone()


def character_message(owner,cid,fid,seat):
    result=pb.ScenePlacedCharacter(character_id=cid,furniture_id=fid,seat_id=seat)
    raw=db.get_characters(owner,cid)
    if raw:
        char=pb.Character.FromString(raw[0])
        make_SceneCharacterOutfitPreset(owner,active_outfit(char),result.outfit_preset)
    return result


def snapshot(s,data):
    if s.scene_id!=HOME or s.dungeon[0]:return
    owner=owner_for(s.channel_id)
    if owner is None:return
    for cid,fid,seat in db.conn.execute("SELECT character,furniture,seat FROM garden_residents WHERE owner=? ORDER BY character",(owner,)):
        data.placed_characters.add().CopyFrom(character_message(owner,cid,fid,seat))


def own_residents(player,result):
    result.extend(cid for cid, in db.conn.execute("SELECT character FROM garden_residents WHERE owner=? ORDER BY character",(player,)))


def publish(s,notice):
    recipients=set(s.social_rooms.get((HOME,public_uid(s.player_id)),()))
    # Native recall from the team editor can originate in a different scene.
    recipients.add(s)
    for peer in recipients:
        if peer.running:peer.send(MsgId.GardenPlaceCharacterNotice,notice,0)


def recall(s,cid):
    db.conn.execute("DELETE FROM garden_residents WHERE owner=? AND character=?",(s.player_id,cid))
    publish(s,pb.GardenPlaceCharacterNotice(status=pb.StatusCode_OK,remove_character_id=cid))


def clear_furniture(s,fid):
    if s.scene_id!=HOME or s.channel_id!=public_uid(s.player_id):return
    residents=db.conn.execute("SELECT character FROM garden_residents WHERE owner=? AND furniture=?",
                              (s.player_id,fid)).fetchall()
    for cid, in residents:recall(s,cid)
    for peer in s.social_rooms.get((HOME,s.channel_id),()):
        if peer.home_seat and peer.home_seat[0]==fid:
            notice=pb.SceneSitChairRsp(status=pb.StatusCode_OK,player_id=peer.player_id,
                                      chair_id=fid,seat_id=peer.home_seat[1],is_sit=False)
            s.commit_callbacks.append((setattr,(peer,"home_seat",None)))
            for other in s.social_rooms.get((HOME,s.channel_id),()):
                if other.running:other.send(MsgId.SceneSitChairRsp,notice,0)


def can_display(pid,cid):
    row=_table("Character","character").get(cid,{})
    tiers=_table("Character","character_achieve_reward").get(row.get("achieve_reward_i_d"),{}).get("achieve_reward_info",())
    position=int(_table("GlobalConfig","config","key")["Character_HomeDisplay_Condition"]["values"][0])-1
    if not 0<=position<len(tiers):return False
    threshold=tiers[position]
    return wallet(pid,threshold["unlock_item_i_d"])>=threshold["unlock_item_count"]


def place(s,req,rsp):
    pid,cid=s.player_id,req.character_id
    if not cid:return pb.StatusCode_BAD_REQ
    current=find(pid,cid)
    rsp.character_id,rsp.furniture_id,rsp.seat_id,rsp.is_remove=cid,req.furniture_id,req.seat_id,req.is_remove
    if req.is_remove:
        if not current or (req.furniture_id and (req.furniture_id,req.seat_id)!=current):
            return pb.StatusCode_FURNITURE_NOT_EXIST
        rsp.furniture_id,rsp.seat_id=current
        recall(s,cid)
        return pb.StatusCode_OK
    if s.scene_id!=HOME or s.dungeon[0] or s.channel_id!=public_uid(pid):
        return pb.StatusCode_GARDEN_PLACE_CHARACTER_CONDITION_NOT_MET
    target=furniture(HOME,s.channel_id,req.furniture_id)
    if not target or target[0]!=pid:return pb.StatusCode_FURNITURE_NOT_EXIST
    if not 0<=req.seat_id<seat_count(target[1]):return pb.StatusCode_BAD_REQ
    if not db.get_characters(pid,cid):return pb.StatusCode_CharNotExist
    if cid==db.get_players_info(pid,"team")[0][0]:return pb.StatusCode_CHARACTER_IN_TEAM
    if not can_display(pid,cid):return pb.StatusCode_GARDEN_PLACE_CHARACTER_CONDITION_NOT_MET
    if current:return pb.StatusCode_GARDEN_PLACE_CHARACTER_ALREADY
    location=(req.furniture_id,req.seat_id)
    if occupant(pid,*location) or any(peer.home_seat==location for peer in s.social_rooms.get((HOME,s.channel_id),())):
        return pb.StatusCode_FURNITURE_OCCUPY
    db.conn.execute("INSERT INTO garden_residents VALUES(?,?,?,?)",(pid,cid,*location))
    notice=pb.GardenPlaceCharacterNotice(status=pb.StatusCode_OK,character=character_message(pid,cid,*location))
    publish(s,notice)
    return pb.StatusCode_OK
