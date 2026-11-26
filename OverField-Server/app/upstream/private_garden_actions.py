"""Durable plaza furniture edits and supply-box claims for a private realm."""
import random
import time

from network.msg_id import MsgId
from proto import net_pb2 as pb
from server.scene_data import room_members
from utils import db
from private_dungeons import grant
from private_channels import count_here, room_limit, tally, bag_adjust
from private_initial_queries import supply,table


def publish(session, notice, exclude_actor=False):
    msg_id = getattr(MsgId, notice.DESCRIPTOR.name)
    for peer in room_members(session):
        if not exclude_actor or peer is not session:
            peer.send(msg_id,notice,0)


def fresh_id(pid,scene,channel):
    from private_garden_ids import chair_ids
    minimum=max(chair_ids(),default=0)+1
    fid=db.get_instance_id(pid,minimum=minimum)
    while db.conn.execute("SELECT 1 FROM furnitures WHERE scene_id=? AND channel_id=? AND furniture_id=?", (scene,channel,fid)).fetchone() or db.conn.execute("SELECT 1 FROM realm_hand_furniture WHERE fid=?",(fid,)).fetchone():
        fid=db.get_instance_id(pid)
    return fid


def debit(s,item_id):
    blob=db.get_item_detail(s.player_id,item_id=item_id)
    it=pb.ItemDetail.FromString(blob) if blob else None
    if not it or it.main_item.item_tag!=pb.EBagItemTag_Furniture or it.main_item.WhichOneof("item")!="base_item" or it.main_item.base_item.num<1:
        return False
    item=bag_adjust(s.player_id,item_id,-1)
    s.send(MsgId.PackNotice,pb.PackNotice(status=pb.StatusCode_OK,items=[item]),0)
    return True


def hand(session,req,rsp):
    if session.scene_id!=9999 or session.dungeon[0]:return pb.StatusCode_BAD_REQ
    pid=session.player_id
    row=db.conn.execute("SELECT fid,item FROM realm_hand_furniture WHERE player=?",(pid,)).fetchone()
    if row:
        if row[1]!=req.item_id:return pb.StatusCode_CANT_HANDING_MORE
        rsp.furniture_id=row[0]
        return pb.StatusCode_OK
    if not debit(session,req.item_id):return pb.StatusCode_ITEM_NOT_ENOUGH
    rsp.furniture_id=fresh_id(pid,session.scene_id,session.channel_id)
    db.conn.execute("INSERT INTO realm_hand_furniture VALUES(?,?,?)",(pid,rsp.furniture_id,req.item_id))
    return pb.StatusCode_OK


def put_away(session,req,rsp):
    pid=session.player_id
    row=db.conn.execute("SELECT fid,item FROM realm_hand_furniture WHERE player=? AND fid=?",(pid,req.furniture_id)).fetchone()
    if not row:
        if req.furniture_id and req.furniture_id == getattr(session, "returned_hand_id", None):
            rsp.furniture_id = req.furniture_id
            return pb.StatusCode_OK
        return pb.StatusCode_FURNITURE_NOT_EXIST
    db.conn.execute("DELETE FROM realm_hand_furniture WHERE player=?",(pid,))
    grant(session,{row[1]:1},pb.PackNotice().items)
    rsp.furniture_id=row[0]
    return pb.StatusCode_OK


def place(session, req, rsp):
    if session.scene_id!=9999 or session.dungeon[0] or req.furniture_id<0 or not req.furniture_item_id:
        return pb.StatusCode_BAD_REQ
    pid=session.player_id
    fid=req.furniture_id
    from private_garden_ids import chair_ids
    if fid in chair_ids():
        return pb.StatusCode_FURNITURE_OCCUPY
    held=db.conn.execute("SELECT fid,item FROM realm_hand_furniture WHERE player=?",(pid,)).fetchone()
    if not fid:fid=held[0] if held and held[1]==req.furniture_item_id else fresh_id(pid,session.scene_id,session.channel_id)
    existing=db.conn.execute("SELECT player_id,furniture_detail_blob FROM furnitures WHERE scene_id=? AND channel_id=? AND furniture_id=?",
                             (session.scene_id,session.channel_id,fid)).fetchone()
    if existing:
        if existing[0]!=pid or pb.FurnitureDetailsInfo.FromString(existing[1]).furniture_item_id!=req.furniture_item_id:
            return pb.StatusCode_FURNITURE_OCCUPY
    else:
        limit=room_limit(session)
        if limit is not None and count_here(session)>=limit:return pb.StatusCode_CANT_REPEATED_PLACED_IN_OTHER_CHANNEL
        from private_home_capacity import can_place
        if not can_place(session, req.furniture_item_id):
            return pb.StatusCode_FurnitureNumLimit
        if held and held[0]==fid:
            if held[1]!=req.furniture_item_id:return pb.StatusCode_BAD_REQ
            db.conn.execute("DELETE FROM realm_hand_furniture WHERE player=?",(pid,))
        elif not debit(session,req.furniture_item_id):return pb.StatusCode_ITEM_NOT_ENOUGH
    details=rsp.furniture_details_info
    details.furniture_id,details.furniture_item_id,details.layer_num=fid,req.furniture_item_id,req.layer_num
    details.pos.CopyFrom(req.pos);details.rotation.CopyFrom(req.rot)
    db.set_furniture(session.scene_id,session.channel_id,pid,fid,details.SerializeToString())
    if not existing:
        tally(pid)
        from private_newcomer import record
        record(session, 129)
    update=pb.SceneGardenFurnitureUpdateNotice(status=pb.StatusCode_OK)
    update.furniture_info.CopyFrom(details)
    # The retail placement callback operates on the local preview before applying scene updates.
    session.commit_callbacks.append((publish, (session, update)))
    return pb.StatusCode_OK


def remove(session, req, rsp):
    row=db.get_furniture(session.scene_id,session.channel_id,session.player_id,req.furniture_id) if req.furniture_id else None
    if not row or session.dungeon[0]:return pb.StatusCode_FURNITURE_NOT_EXIST
    details=pb.FurnitureDetailsInfo.FromString(row[-1])
    if not req.is_put_in_package:
        if db.conn.execute("SELECT 1 FROM realm_hand_furniture WHERE player=?",(session.player_id,)).fetchone():
            return pb.StatusCode_CANT_HANDING_MORE
        db.conn.execute("INSERT INTO realm_hand_furniture VALUES(?,?,?)",(session.player_id,req.furniture_id,details.furniture_item_id))
    from private_garden_residents import clear_furniture
    clear_furniture(session,req.furniture_id)
    if details.furniture_item_id==5044010:
        from private_gomoku import remove_table
        remove_table(session.channel_id,req.furniture_id)
    db.del_furniture(session.scene_id,session.channel_id,session.player_id,req.furniture_id)
    tally(session.player_id)
    if req.is_put_in_package:grant(session,{details.furniture_item_id:1},pb.PackNotice().items)
    rsp.furniture_id=req.furniture_id
    notice = pb.SceneGardenFurnitureRemoveNotice(status=pb.StatusCode_OK, furniture_id=req.furniture_id,
                                                 item_id=details.furniture_item_id)
    # Picking the object up reuses its local handle. The remove notice destroys that handle.
    session.commit_callbacks.append((publish, (session, notice, not req.is_put_in_package)))
    return pb.StatusCode_OK


def claim_supply(session, req, rsp):
    prev = pb.SupplyBoxInfoRsp()
    supply(session,req,prev)
    now = int(time.time())
    if prev.next_reward_time > now:
        rsp.next_reward_time = prev.next_reward_time
        return pb.StatusCode_FAIL
    interval = next(row for row in table("GlobalConfig","config") if row["key"]=="SupplyBox_Random_Time")
    low,high = map(int,interval["values"])
    rsp.next_reward_time = now + 60 * random.randint(low,high)
    pid = session.player_id
    db.conn.execute("INSERT OR REPLACE INTO player_kv(player_id,ns,k,v) VALUES(?,'player','supply_box_next',?)",(pid,rsp.next_reward_time))
    db.set_SupplyBox_next_reward_time(pid,rsp.next_reward_time)
    # The CN table specifies the timer; the private coin award follows the public of-ps realm.
    grant(session,{101:1000},rsp.items)
    return pb.StatusCode_OK

