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


_scene_boxes = {}


def template_at(session, index):
    if session.dungeon[0]:
        cfg = dungeon.box_config(session.dungeon[0], index)
        return cfg["i_d"] if cfg else 0
    scene = res.index("ScenesConfigAsset", "Scenes", "ID").get(session.scene_id)
    if scene is None:
        return 0
    points = scene.get("CollectionTreasureInfos", ())
    entry = _scene_boxes.get(session.scene_id)
    if entry is None or entry[0] is not points:
        by_index = {}
        for position, point in enumerate(points):
            by_index.setdefault(point.get("Index", position), point["ID"])
        entry = _scene_boxes[session.scene_id] = (points, by_index)
    return entry[1].get(index, 0)


def key_for(session, req, index):
    if req.box_type not in (0, 1) or req.loc_type != (pb.TreasureBoxLocType_DUNGEON if session.dungeon[0] else pb.TreasureBoxLocType_SCENE):
        return None
    if req.box_type==1:
        from private_encounter_gifts import visible
        if not visible(session,index):
            return None
    return (session.player_id, zone(session), int(req.box_type), index)


def refresh_seconds(template):
    chest = res.index("TreasureBox","treasure_box").get(template,{})
    rule = res.index("TreasureBox","collection_treasure_box").get(chest.get("relate_i_d"),{})
    return (rule.get("refresh_time") or [0])[0]


def level_reward(pool, world, level):
    # These retail pools contain one initial level curve followed by a row for
    # each of the five higher world tiers. The mapping to chests is a realm rule.
    rows = res.index("Reward","reward_item_pool")[pool]["reward_item_pool_group"]
    if world > 1:
        return rows[min(world,6)-7]
    return next((r for r in rows[:-5] if level <= r["max_level_limit"]),rows[-6])


def field_chest_loot(session, supply):
    from private_ground_loot import drop_pools, equipment_level
    pid = session.player_id
    level,world = db.get_players_info(pid,"level,world_level")
    world = max(1,world)
    max_level = min(max(1,level),res.index("Level","world_level")[min(world,6)]["monster_level"])
    coins = level_reward(1000001,world,level)["item_max_count"]
    if supply:
        # The official sample contains 76 coins; its exact variance/fatigue
        # is not in the client. Keep this provisional band local to supply boxes.
        coins = random.randint(max(1,coins*4//5),coins)
    rewards = [make_item(101,coins,pid)]
    gear = drop_pools(world)
    tags = [tag for tag in (2,3) if gear[tag]]
    for _ in range(random.randint(1,2) if tags else 0):
        tag = random.choice(tags)
        item = random.choice(gear[tag])
        rewards.append(make_item(item,1,pid,equipment_level=equipment_level(item,tag,max_level)))
    if supply:
        stone = level_reward(1000011,world,level)
        rewards.append(make_item(stone["item_i_d"],random.randint(stone["item_min_count"],stone["item_max_count"]),pid))
        if random.random() < 0.5:
            materials = res.index("Reward","reward_item_pool")[1000041]["reward_item_pool_group"]
            material = random.choice(materials)
            rewards.append(make_item(material["item_i_d"],random.randint(material["item_min_count"],material["item_max_count"]),pid))
    return rewards


def loot(session, box, dynamic):
    pid = session.player_id
    # Fixed national tutorial boxes and known collection rewards in of-ps. Other ordinary boxes use private drop rates.
    fixed = {30001: (101,10), 30002: (102,10), 101005: (1102,1), 101007: (1101,1), 101008: (5041907,1), 101103: (4200203,1)}
    if session.scene_id == 100 and dynamic:
        fixed.update({3001: (101,100), 3002: (101,1000)})
    if box in fixed:
        template, count = fixed[box]
        return [make_item(template,count,pid)]
    if not session.dungeon[0]:
        if dynamic and box==300000:
            return field_chest_loot(session,False)
        if not dynamic and refresh_seconds(box):
            return field_chest_loot(session,True)
    items = [make_item(101,10,pid)]
    world = max(1, db.get_players_info(pid, "world_level")[0])
    eligible = set(res["ReleasedEquipment"]["equipment_ids"])
    equipment = [r["i_d"] for r in res["Item"]["item"]["datas"] if r["i_d"] in eligible
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
    data = None
    if old and old[1] and (not old[2] or old[2] > now):
        data = pb.TreasureBoxData.FromString(old[1])
        # Old releases saved aliased four-hour chests with refresh=0. Keep an
        # unclaimed roll intact, and let an empty one spawn normally again.
        if not dynamic and not old[2] and (duration := refresh_seconds(old[0])):
            if data.rewards:
                data.next_refresh_time = now + duration
                db.conn.execute("UPDATE private_boxes SET data=?,refresh=? WHERE player_id=? AND zone=? AND dynamic=? AND idx=?",
                                (data.SerializeToString(),data.next_refresh_time,*key))
            else:
                data = None
    if data is None:
        if dynamic and not old:
            return pb.StatusCode_TREASURE_BOX_NOT_EXIST
        template = old[0] if old else template_at(session, idx)
        if not template:
            return pb.StatusCode_TREASURE_BOX_NOT_EXIST
        if session.dungeon[0] and not dynamic:
            status = dungeon.open_box(session,req,rsp)
            if status != pb.StatusCode_OK:
                return status
        cfg = res.index("TreasureBox","treasure_box").get(template,{})
        data = pb.TreasureBoxData(index=idx,box_id=template,state=pb.TreasureBoxState_Open,type=cfg.get("new_treasure_box_type",0))
        duration = refresh_seconds(template)
        if not dynamic and duration > 0:
            data.next_refresh_time = now + duration
        for item in loot(session,template,dynamic):
            if item and item.main_item.item_id:
                item.pack_type = pb.ItemDetail.PackType_TempStorageArea
                data.rewards.add().CopyFrom(item)
        db.conn.execute("INSERT OR REPLACE INTO private_boxes(player_id,zone,dynamic,idx,template,data,refresh,claimed) VALUES(?,?,?,?,?,?,?,0)",
                        (*key,template,data.SerializeToString(),data.next_refresh_time))
        from private_bless_tasks import record as prayer
        prayer(session,119)
        if not session.dungeon[0]:prayer(session,3,param=session.scene_id)
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
    from private_inventory import receive
    status = receive(session,picks,rsp.items)
    if status != pb.StatusCode_OK:
        return status
    db.conn.execute("UPDATE private_boxes SET data=?,claimed=? WHERE player_id=? AND zone=? AND dynamic=? AND idx=?",
                    (data.SerializeToString(),not data.rewards,*key))
    return pb.StatusCode_OK


def fill_scene(session, data):
    rows = db.conn.execute("SELECT template,refresh,claimed,data FROM private_boxes WHERE player_id=? AND zone=? AND dynamic=0 AND data IS NOT NULL AND (refresh=0 OR refresh>?)",
                           (session.player_id,zone(session),int(time.time())))
    for template,refresh,claimed,blob in rows:
        if claimed and not refresh and refresh_seconds(template):
            continue
        entry = data.treasure_boxes.add()
        entry.ParseFromString(blob)
        entry.next_refresh_time *= 1000
