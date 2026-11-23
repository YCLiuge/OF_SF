"""Persist native scene boxes, generated drops and owner-scoped pickups in one transaction."""
import random
import time

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item
from utils.res_loader import res
import private_dungeons as dungeon


def table(key):
    return res["TreasureBox"][key]["datas"]


def zone(session):
    return -session.dungeon[0] if session.dungeon[0] else session.scene_id


def gameplay_reward(session, req, rsp):
    stages=getattr(session,"stages",None)
    if stages:
        stages.gameplay_reward(req.reward_id,req.num)
    if req.reward_id==1003:
        from private_story_starter import start
        return start(session,req,rsp)
    box_id = req.reward_id + 2000
    if not any(row["i_d"] == box_id for row in table("game_treasure_box")):
        return pb.StatusCode_CFG_NOT_FOUND
    pid, realm_zone = session.player_id, zone(session)
    existing = db.conn.execute("SELECT idx FROM private_boxes WHERE player_id=? AND zone=? AND dynamic=1 AND template=? AND claimed=0 ORDER BY idx LIMIT 1",
                               (pid, realm_zone, box_id)).fetchone()
    if existing:
        idx = existing[0]
    else:
        idx = db.conn.execute("SELECT COALESCE(MAX(idx),0)+1 FROM private_boxes WHERE player_id=? AND dynamic=1", (pid,)).fetchone()[0]
        db.conn.execute("INSERT INTO private_boxes(player_id,zone,dynamic,idx,template) VALUES(?,?,1,?,?)", (pid, realm_zone, idx, box_id))
    rsp.dynamic_treasure_box_info.box_id = box_id
    rsp.dynamic_treasure_box_info.index = idx
    return pb.StatusCode_OK


def template_at(session, index):
    if session.dungeon[0]:
        cfg = dungeon.box_config(session.dungeon[0], index)
        return cfg["i_d"] if cfg else 0
    scene = next((s for s in res["ScenesConfigAsset"]["Scenes"] if s["ID"] == session.scene_id), {})
    return next((p["ID"] for i, p in enumerate(scene.get("CollectionTreasureInfos", ())) if p.get("Index", i) == index), 0)


def key_for(session, req, index):
    if req.box_type not in (0, 1) or req.loc_type != (pb.TreasureBoxLocType_DUNGEON if session.dungeon[0] else pb.TreasureBoxLocType_SCENE):
        return None
    return (session.player_id, zone(session), int(req.box_type), index)


def loot(session, box, dynamic):
    pid = session.player_id
    # Fixed national tutorial boxes and known collection rewards in of-ps. Other ordinary boxes use private drop rates.
    fixed = {30001: (101,10), 30002: (102,10), 101005: (1102,1), 101007: (1101,1), 101008: (5041907,1), 101103: (4200203,1)}
    if session.scene_id == 100 and dynamic:
        fixed.update({3001: (101,100), 3002: (101,1000)})
    if box in fixed:
        template, count = fixed[box]
        return [make_item(template,count,pid)]
    items = [make_item(101,10,pid)]
    world = max(1, db.get_players_info(pid, "world_level")[0])
    equipment = [r["i_d"] for r in res["Item"]["item"]["datas"] if r.get("new_bag_item_tag") in (2,3)
                 and 0 < r.get("quality",0) <= world]
    for template in random.sample(equipment, min(len(equipment), random.randint(1,2))):
        instance = make_item(template,1,pid)
        if instance and instance.main_item.WhichOneof("item") in ("armor","weapon"):
            items.append(instance)
    return items


def open_box(session, req, rsp):
    idx = req.treasure_box_index
    key = key_for(session, req, idx)
    if key is None:
        return pb.StatusCode_BAD_REQ
    pid, realm_zone, dynamic, _ = key
    old = db.conn.execute("SELECT template,data,refresh FROM private_boxes WHERE player_id=? AND zone=? AND dynamic=? AND idx=?", key).fetchone()
    now = int(time.time())
    if old and old[1] and (not old[2] or old[2] > now):
        data = pb.TreasureBoxData.FromString(old[1])
    else:
        if dynamic and not old:
            return pb.StatusCode_TREASURE_BOX_NOT_EXIST
        template = old[0] if old else template_at(session, idx)
        if not template:
            return pb.StatusCode_TREASURE_BOX_NOT_EXIST
        if session.dungeon[0] and not dynamic:
            status = dungeon.open_box(session,req,rsp)
            if status != pb.StatusCode_OK:
                return status
        cfg = next((r for r in table("treasure_box") if r["i_d"] == template), {})
        data = pb.TreasureBoxData(index=idx,box_id=template,state=pb.TreasureBoxState_Open,type=cfg.get("new_treasure_box_type",0))
        collect = next((r for r in table("collection_treasure_box") if r["i_d"] == template), {})
        duration = (collect.get("refresh_time") or [0])[0]
        if not dynamic and duration > 0:
            data.next_refresh_time = now + duration
        for item in loot(session,template,dynamic):
            if item and item.main_item.item_id:
                item.pack_type = pb.ItemDetail.PackType_TempStorageArea
                data.rewards.add().CopyFrom(item)
        db.conn.execute("INSERT OR REPLACE INTO private_boxes(player_id,zone,dynamic,idx,template,data,refresh,claimed) VALUES(?,?,?,?,?,?,?,0)",
                        (*key,template,data.SerializeToString(),data.next_refresh_time))
    rsp.next_refresh_time = data.next_refresh_time * 1000
    rsp.items.extend(data.rewards)
    return pb.StatusCode_OK


def pickup(session, req, rsp):
    key = key_for(session, req, req.box_index)
    if key is None:
        return pb.StatusCode_BAD_REQ
    row = db.conn.execute("SELECT data FROM private_boxes WHERE player_id=? AND zone=? AND dynamic=? AND idx=?", key).fetchone()
    if not row or not row[0]:
        return pb.StatusCode_TREASURE_BOX_NOT_EXIST
    data = pb.TreasureBoxData.FromString(row[0])
    if req.pick_index == -1:
        picks = list(data.rewards)
        del data.rewards[:]
    elif 1 <= req.pick_index <= len(data.rewards):
        picks = [pb.ItemDetail.FromString(data.rewards[req.pick_index-1].SerializeToString())]
        del data.rewards[req.pick_index-1]
    else:
        return pb.StatusCode_BAD_REQ
    notice = pb.PackNotice(status=pb.StatusCode_OK)
    pid = session.player_id
    for item in picks:
        item.pack_type = pb.ItemDetail.PackType_Inventory
        rsp.items.add().CopyFrom(item)
        total = notice.items.add()
        total.CopyFrom(item)
        kind = item.main_item.WhichOneof("item")
        if kind in ("armor","weapon"):
            db.set_item_detail(pid,total.SerializeToString(),instance_id=getattr(item.main_item,kind).instance_id)
        elif kind == "base_item":
            prev = db.get_item_detail(pid,item.main_item.item_id)
            if prev:
                total.main_item.base_item.num += pb.ItemDetail.FromString(prev).main_item.base_item.num
            db.set_item_detail(pid,total.SerializeToString(),item_id=item.main_item.item_id)
        elif kind == "outfit":
            db.set_item_detail(pid,total.SerializeToString(),item_id=item.main_item.item_id)
        else:
            raise ValueError("Unrecognized private chest item")
    db.conn.execute("UPDATE private_boxes SET data=?,claimed=? WHERE player_id=? AND zone=? AND dynamic=? AND idx=?",
                    (data.SerializeToString(),not data.rewards,*key))
    if notice.items:
        session.send(MsgId.PackNotice,notice,0)
    return pb.StatusCode_OK


def fill_scene(session, data):
    rows = db.conn.execute("SELECT data FROM private_boxes WHERE player_id=? AND zone=? AND dynamic=0 AND data IS NOT NULL AND (refresh=0 OR refresh>?)",
                           (session.player_id,zone(session),int(time.time())))
    for (blob,) in rows:
        entry = data.treasure_boxes.add()
        entry.ParseFromString(blob)
        entry.next_refresh_time *= 1000

