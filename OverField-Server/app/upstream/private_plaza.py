"""Player-owned instruments and the realm's shared game-time weather."""
import random
import time

from proto import net_pb2 as pb
from server.scene_data import up_scene_action, get_session
from utils import db
from utils.res_loader import res


def game_seconds():
    return int(21600 + time.time() * 50)


def weather(hour):
    day, local_hour = divmod(hour, 24)
    start = random.Random("weather:" + str(day)).randrange(23)
    return pb.WeatherType_RAINY if start <= local_hour < start + 2 else pb.WeatherType_SUNNY


def forecast(session, req, rsp):
    rsp.generic_msg_id = req.generic_msg_id
    if req.generic_msg_id != 4:
        return pb.StatusCode_BAD_REQ
    count = next(int(row["values"][0]) for row in res["GlobalConfig"]["config"]["datas"]
                 if row["key"] == "WeatherForecastHour")
    first = game_seconds() // 3600
    for hour in range(first, first + count):
        rsp.params.add(int_value=hour % 24, string_value=str(weather(hour)))
    return pb.StatusCode_OK


def instrument(session, source, instance):
    if source == pb.MusicalItemSource_Furniture:
        row = db.conn.execute("SELECT furniture_detail_blob FROM furnitures WHERE scene_id=? AND channel_id=? AND furniture_id=? LIMIT 1",
                              (session.scene_id, session.channel_id, instance)).fetchone()
        return pb.FurnitureDetailsInfo.FromString(row[0]).furniture_item_id if row else 0
    raw = db.get_item_detail(session.player_id, instance_id=instance)
    if raw:
        return pb.ItemDetail.FromString(raw).main_item.item_id
    # Furniture in the national Item table is stackable. Inventory selection can use its template key.
    raw = db.get_item_detail(session.player_id, item_id=instance)
    if raw:
        item = pb.ItemDetail.FromString(raw).main_item
        if item.HasField("base_item") and item.base_item.num > 0:
            return item.item_id
    return 0


def broadcast(session):
    notice = pb.ServerSceneSyncDataNotice(status=pb.StatusCode_OK)
    action = notice.data.add(player_id=session.player_id).server_data.add(action_type=pb.SceneActionType_UPDATE_MUSICAL_ITEM)
    action.player.CopyFrom(session.scene_player)
    up_scene_action(session.scene_id, session.channel_id, notice)


def change_instrument(session, req, rsp):
    instance = req.musical_item_instance_id
    if instance < 0 or req.source not in (pb.MusicalItemSource_Self, pb.MusicalItemSource_Furniture):
        return pb.StatusCode_BAD_REQ
    template = instrument(session, req.source, instance) if instance else 0
    if instance and template not in res["PlazaMusic"]["instruments"]:
        return pb.StatusCode_FURNITURE_NOT_EXIST
    player = session.scene_player
    changed = (player.musical_item_instance_id, player.musical_item_source, player.musical_item_id) != (instance, req.source, template)
    rsp.musical_item_id = player.musical_item_id = template
    rsp.source = player.musical_item_source = req.source
    rsp.musical_item_instance_id = player.musical_item_instance_id = instance
    if changed:
        player.ClearField("playing_music_note")
        broadcast(session)
    return pb.StatusCode_OK


def play_note(session, req, rsp):
    if req.music_note_id and req.music_note_id not in res["PlazaMusic"]["notes"]:
        return pb.StatusCode_CFG_NOT_FOUND
    player = session.scene_player
    start = int(time.time() * 1000)
    if req.music_note_id and not player.musical_item_id:
        return pb.StatusCode_BAD_REQ
    if req.join_player_id and req.join_player_id != session.player_id:
        peer = next((s for s in get_session() if s.logged_in and s.scene_id == session.scene_id and
                     s.channel_id == session.channel_id and s.player_id == req.join_player_id), None)
        if not peer or not peer.scene_player.HasField("playing_music_note") or peer.scene_player.playing_music_note.music_note_id != req.music_note_id:
            return pb.StatusCode_BAD_REQ
        start = peer.scene_player.playing_music_note.start_time
    rsp.player_id = session.player_id
    rsp.music_note_id = req.music_note_id
    rsp.start_time = start
    if req.music_note_id:
        player.playing_music_note.music_note_id = req.music_note_id
        player.playing_music_note.start_time = start
    else:
        player.ClearField("playing_music_note")
    broadcast(session)
    return pb.StatusCode_OK


def starter_instruments(player_id):
    # This realm makes the national furniture instruments available once on first visit.
    if not db.conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?, 'private_unlock','plaza_instruments')", (player_id,)).rowcount:
        return
    from utils.pb_create import make_item
    for template in res["PlazaMusic"]["instruments"]:
        if not db.get_item_detail(player_id, item_id=template):
            item = make_item(template, 1, player_id)
            if item and item.main_item.HasField("base_item"):
                db.set_item_detail(player_id, item.SerializeToString(), item_id=template)

def send_action(session,req,rsp):
    if req.action_id and req.action_id not in res["PlazaMusic"]["actions"]:
        return pb.StatusCode_CFG_NOT_FOUND
    from network.msg_id import MsgId
    notice=pb.SendActionNotice(status=pb.StatusCode_OK,action_id=req.action_id,from_player_id=session.player_id,from_player_name=session.player_name)
    for peer in get_session():
        if peer.scene_id==session.scene_id and peer.channel_id==session.channel_id and peer.dungeon[0]==session.dungeon[0]:
            peer.send(MsgId.SendActionNotice,notice,0)
    return pb.StatusCode_OK
