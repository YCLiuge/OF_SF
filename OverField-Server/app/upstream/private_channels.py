"""Numbered public plazas and player-owned homes on original PC game channels."""
from collections import Counter
from network.msg_id import MsgId
from proto import net_pb2 as pb
from server.scene_data import get_session
from utils import db
from utils.pb_create import make_item
from realm_uids import local_uid, public_uid
from utils.res_loader import res

HOME = 9999


def is_public(rules,channel):
    return rules.public_min <= channel <= rules.public_max


def owner_for(channel):
    from realm_uids import FIRST
    if channel < FIRST:
        return None
    try:
        pid=local_uid(channel)
    except (KeyError, ValueError):
        return None
    return pid if pid and db.conn.execute("SELECT 1 FROM players WHERE player_id=?",(pid,)).fetchone() else None


def tally(pid):
    db.conn.execute("UPDATE garden_info SET furniture_num=(SELECT count(*) FROM furnitures WHERE scene_id=? AND channel_id=? AND player_id=?) WHERE player_id=?",(HOME,public_uid(pid),pid,pid))


def setup(rules):
    with db.conn:
        from private_garden_residents import setup as setup_residents
        setup_residents()
        columns={r[1] for r in db.conn.execute("PRAGMA table_info(private_locations)")}
        if "channel" not in columns:db.conn.execute("ALTER TABLE private_locations ADD COLUMN channel INTEGER NOT NULL DEFAULT 1")
        db.conn.execute("CREATE TABLE IF NOT EXISTS realm_garden_migration (version INTEGER PRIMARY KEY)")
        db.conn.execute("CREATE TABLE IF NOT EXISTS realm_hand_furniture (player INTEGER PRIMARY KEY, fid INTEGER NOT NULL, item INTEGER NOT NULL)")
        db.conn.execute("CREATE TABLE IF NOT EXISTS realm_garden_schemes "
                        "(player INTEGER NOT NULL, slot INTEGER NOT NULL CHECK(slot BETWEEN 0 AND 2), "
                        "name TEXT NOT NULL DEFAULT '', layout BLOB, PRIMARY KEY(player,slot))")
        db.conn.execute("CREATE TABLE IF NOT EXISTS realm_home_likes "
                        "(owner INTEGER, visitor INTEGER, count INTEGER NOT NULL, PRIMARY KEY(owner,visitor))")
        db.conn.execute("CREATE TABLE IF NOT EXISTS realm_like_daily "
                        "(player INTEGER PRIMARY KEY, day TEXT NOT NULL, count INTEGER NOT NULL)")
        if not db.conn.execute("SELECT 1 FROM realm_garden_migration WHERE version=1").fetchone():
            # The previous implementation had only public channel 1. Preserve every existing object and owner.
            rows=db.conn.execute("SELECT player_id,furniture_id,furniture_detail_blob FROM furnitures WHERE scene_id=? AND channel_id=1",(HOME,)).fetchall()
            for pid,fid,raw in rows:
                dest=public_uid(pid)
                new_id=fid
                while db.conn.execute("SELECT 1 FROM furnitures WHERE scene_id=? AND channel_id=? AND furniture_id=?",(HOME,dest,new_id)).fetchone():
                    new_id=db.get_instance_id(pid)
                if new_id!=fid:
                    obj=pb.FurnitureDetailsInfo.FromString(raw);obj.furniture_id=new_id;raw=obj.SerializeToString()
                db.set_furniture(HOME,dest,pid,new_id,raw)
                db.del_furniture(HOME,1,pid,fid)
                tally(pid)
            db.conn.execute("INSERT INTO realm_garden_migration VALUES(1)")
        # Unclean shutdown: nobody is online yet. A second startup sees no placements to refund.
        for pid,channel in db.conn.execute("SELECT DISTINCT player_id,channel_id FROM furnitures WHERE scene_id=? AND channel_id>0 AND channel_id < ?",(HOME,rules.uid_start)).fetchall():
            reclaim(pid,channel)
        from private_garden_ids import reserve_chair_ids
        reserve_chair_ids()
        for (pid,) in db.conn.execute("SELECT player_id FROM garden_info").fetchall():tally(pid)


def selected(s,r):
    target=r.channel_label
    if r.target_player_label:
        if target and target!=r.target_player_label:return 0,pb.StatusCode_CANT_JOIN_PLAYER_CURRENT_SCENE_CHANNEL
        target=r.target_player_label
    if not target:
        target=s.channel_id if is_public(s.realm_rules,s.channel_id) or r.scene_id==s.scene_id else s.realm_rules.public_min
    if is_public(s.realm_rules,target):
        if r.target_player_label:return 0,pb.StatusCode_SCENE_CHANNEL_NOT_EXIST
        return target,pb.StatusCode_OK
    owner=owner_for(target)
    if r.scene_id!=HOME or not owner:return 0,pb.StatusCode_SCENE_CHANNEL_NOT_EXIST
    if owner!=s.player_id and not db.get_garden_info(owner)[4]:return 0,pb.StatusCode_SCENE_CHANNEL_PRIVATE
    return target,pb.StatusCode_OK


def restore(s,saved,valid_scenes):
    if not saved or saved[0] not in valid_scenes:
        s.channel_id=s.realm_rules.public_min
        return
    scene,ch=saved
    owner=owner_for(ch)
    if is_public(s.realm_rules,ch) or (scene==HOME and owner and (owner==s.player_id or db.get_garden_info(owner)[4])):
        s.scene_id,s.channel_id=scene,ch
    else:
        s.scene_id,s.channel_id=HOME,s.realm_rules.public_min


def room_limit(s):
    rules=s.realm_rules
    if is_public(rules,s.channel_id):return rules.public_furniture_limit
    if s.channel_id==public_uid(s.player_id):return rules.home_furniture_limit or None
    return rules.visitor_furniture_limit


def count_here(s):
    if is_public(s.realm_rules,s.channel_id) and s.realm_rules.public_furniture_scope=="realm":
        return db.conn.execute("SELECT count(*) FROM furnitures WHERE scene_id=? AND channel_id BETWEEN ? AND ? AND player_id=?",(HOME,s.realm_rules.public_min,s.realm_rules.public_max,s.player_id)).fetchone()[0]
    return db.conn.execute("SELECT count(*) FROM furnitures WHERE scene_id=? AND channel_id=? AND player_id=?",(HOME,s.channel_id,s.player_id)).fetchone()[0]


def bag_adjust(pid,item_id,amount):
    raw=db.get_item_detail(pid,item_id=item_id)
    item=pb.ItemDetail.FromString(raw) if raw else make_item(item_id,0,pid)
    if not item or item.main_item.item_tag!=pb.EBagItemTag_Furniture or item.main_item.WhichOneof("item")!="base_item":
        raise ValueError("Unknown furniture inventory item")
    if item.main_item.base_item.num+amount<0:return None
    item.main_item.base_item.num+=amount
    db.set_item_detail(pid,item.SerializeToString(),item_id=item_id)
    return item


def reclaim(pid,channel,player=None,*,notify_peers=False):
    rows=db.conn.execute("SELECT furniture_id,furniture_detail_blob FROM furnitures WHERE scene_id=? AND channel_id=? AND player_id=?",(HOME,channel,pid)).fetchall()
    if not rows:return
    bag=pb.PackNotice(status=pb.StatusCode_OK)
    amounts=Counter()
    for fid,raw in rows:
        item_id=pb.FurnitureDetailsInfo.FromString(raw).furniture_item_id
        amounts[item_id]+=1
        if item_id==5044010:
            from private_gomoku import remove_table
            remove_table(channel,fid)
        db.del_furniture(HOME,channel,pid,fid)
        if player or notify_peers:
            msg=pb.SceneGardenFurnitureRemoveNotice(status=pb.StatusCode_OK,furniture_id=fid,item_id=item_id)
            for peer in get_session():
                if peer.scene_id==HOME and peer.channel_id==channel and not peer.dungeon[0]:
                    peer.send(MsgId.SceneGardenFurnitureRemoveNotice,msg,0)
    for item_id,n in amounts.items():bag.items.add().CopyFrom(bag_adjust(pid,item_id,n))
    if player and player.running:player.send(MsgId.PackNotice,bag,0)


def change(s,new_scene,new_channel):
    if (s.scene_id, s.channel_id) != (new_scene, new_channel):
        s.scene_player.ClearField("status")
        if s.scene_id == HOME and is_public(s.realm_rules, s.channel_id):
            reclaim(s.player_id, s.channel_id, s)
    if new_scene==HOME and s.channel_id!=new_channel:
        owner=owner_for(new_channel)
        if owner and owner!=s.player_id:db.conn.execute("UPDATE garden_info SET access_num=access_num+1 WHERE player_id=?",(owner,))
    s.home_seat=None
    if new_scene == HOME and s.scene_id == HOME and s.channel_id != new_channel:
        from private_newcomer import record
        record(s, 156)
    s.scene_id,s.channel_id=new_scene,new_channel


def fill_garden(s,world):
    if s.scene_id!=HOME:return
    owner=owner_for(s.channel_id)
    for _,_,pid,fid,raw in db.get_furniture(HOME,s.channel_id):
        obj=world.garden_furniture_info_map[fid]
        obj.ParseFromString(raw)
        world.furniture_player_map[fid]=pid
        if pid!=owner:
            other=world.other_player_furniture_info_map[fid];other.player_id=pid;other.furniture_info.CopyFrom(obj)
    if owner:
        world.likes_num,world.access_player_num,*_=db.get_garden_info(owner)
        if owner != s.player_id and db.get_garden_info(owner)[4]:
            from private_garden_likes import day_key, remaining
            world.left_like_num = remaining(s, day_key())
        row=db.conn.execute("SELECT player_name FROM players WHERE player_id=?",(owner,)).fetchone()
        if row:world.garden_name=row[0]
    players = [peer.player_id for peer in get_session() if peer.scene_id == HOME and peer.channel_id == s.channel_id]
    if not s.logged_in:
        players.append(s.player_id)
    if players:
        placeholders = ",".join("?" for _ in players)
        held = {pid:(fid,item) for pid,fid,item in db.conn.execute(
            f"SELECT player,fid,item FROM realm_hand_furniture WHERE player IN ({placeholders})",players)}
        for player in players:
            if player in held:
                fid,item=held[player]
                shown=world.player_handing_furniture_info_map[fid]
                shown.player_id=player
                shown.furniture_info.furniture_id=fid
                shown.furniture_info.furniture_item_id=item
    catalog = res.index("HomeItem", "home_item", "item_i_d")
    points = sum(catalog.get(entry.furniture_item_id, {}).get("home_item_point_num", 0)
                 for entry in world.garden_furniture_info_map.values())
    world.furniture_current_point_num = min(points, 0xffffffff)
