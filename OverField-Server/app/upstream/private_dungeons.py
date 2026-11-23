"""Durable private-realm dungeon visits, configured objectives and one-time rewards."""
from collections import Counter
from network.msg_id import MsgId
import math
import random
import time

from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_SceneTeam, make_item
from utils.res_loader import res


_indices = {}

def _table(resource, group, key="i_d"):
    owner = res[resource][group]
    marker = (resource, group, key)
    entry = _indices.get(marker)
    if entry is None or entry[0] is not owner:
        rows = owner["datas"] if isinstance(owner, dict) else owner
        entry = (owner, {row[key]: row for row in rows if row.get(key)})
        _indices[marker] = entry
    return entry[1]


def config(did):
    return _table("Dungeon", "dungeon").get(did)


def scene(did):
    return _table("ScenesConfigAsset", "Dungeons", "ID").get(did, {})


def load(pid, did):
    raw = db.get_dungeon(pid, did) if did else None
    return pb.DungeonData.FromString(raw) if raw else pb.DungeonData(dungeon_id=did)


def save(pid, progress):
    db.set_dungeon(pid, progress.dungeon_id, progress.SerializeToString())


def ensure_facts_table():
    db.conn.execute("""CREATE TABLE IF NOT EXISTS private_dungeon_facts (
        player INTEGER NOT NULL, dungeon INTEGER NOT NULL, kind TEXT NOT NULL,
        point INTEGER NOT NULL, template INTEGER NOT NULL,
        PRIMARY KEY (player,dungeon,kind,point,template)) WITHOUT ROWID""")


def facts(pid, did):
    ensure_facts_table()
    return db.conn.execute(
        "SELECT kind,point,template FROM private_dungeon_facts WHERE player=? AND dungeon=?",
        (pid, did)).fetchall()


def record(pid, did, kind, point, template=0):
    ensure_facts_table()
    return db.conn.execute("INSERT OR IGNORE INTO private_dungeon_facts VALUES (?,?,?,?,?)",
                           (pid, did, kind, point, template)).rowcount == 1


def box_config(did, index):
    point = next((r for r in scene(did).get("CollectionTreasureInfos", ()) if r.get("Index", 0) == index), None)
    return _table("TreasureBox", "treasure_box").get(point["ID"]) if point else None


def tasks(pid, data):
    did = data.dungeon_id
    group = _table("Dungeon", "dungeon_quest").get(config(did).get("dengeon_quest_i_d"), {})
    counted = Counter()
    counted[(34,)] = len(set(data.coins))
    for kind, point, template in facts(pid, did):
        if kind == "monster":
            counted[42, template] += 1
        elif kind == "boss":
            counted[41, 3] += 1
        elif kind == "box":
            cfg = box_config(did, point)
            if cfg:
                counted[167, cfg.get("new_treasure_box_reward_type")] += 1
    result = []
    for entry in group.get("dungeon_quest_group_info", ()):
        aid = entry.get("achieve_condition_i_d")
        if not aid:
            continue
        target = _table("Achieve", "achieve")[aid]
        cond = target["new_achieve_condition"]
        params = target.get("param", ())
        goal = target["count_param"]
        count = counted[(cond, *params[1:])] if params and params[0] == did else 0
        result.append((aid, min(count, goal), goal))
    return result


def track(pid, data):
    objectives = tasks(pid, data)
    data.all_task_finished = data.all_task_finished or bool(objectives) and all(count == goal for _, count, goal in objectives)
    save(pid, data)


def enter(session, req, rsp):
    pid, did = session.player_id, req.dungeon_id
    cfg = config(did)
    if cfg is None:
        return pb.StatusCode_CFG_NOT_FOUND
    if session.dungeon[0] and session.dungeon[0] != did:
        return pb.StatusCode_BAD_REQ
    required_level = cfg.get("need_player_level", 0)
    player_level = db.conn.execute("SELECT level FROM players WHERE player_id=?", (pid,)).fetchone()[0]
    if player_level < required_level:
        return pb.StatusCode_DungeonUnlock
    previous = cfg.get("dungeon_i_d")
    if previous and not load(pid, previous).finish_times:
        return pb.StatusCode_DungeonUnlock
    team = [req.char1, req.char2, req.char3]
    members = [cid for cid in team if cid]
    if not req.char1 or len(members) != len(set(members)) or any(not db.get_characters(pid, cid) for cid in members):
        return pb.StatusCode_BAD_REQ
    data = load(pid, did)
    if not session.dungeon[0]:
        data.enter_times += 1
        data.last_enter_time = int(time.time())
        data.char1, data.char2, data.char3 = team
        data.pos.CopyFrom(req.pos)
        data.rot.CopyFrom(req.rot)
        save(pid, data)
        session.dungeon = [did, 0, 0, -1]
    make_SceneTeam(pid, [data.char1, data.char2, data.char3], rsp.team)
    born = scene(did).get("Born", ())
    if born:
        for idx, actor in enumerate((rsp.team.char1, rsp.team.char2, rsp.team.char3)):
            if not actor.char_id:
                continue
            pose = born[idx] if idx < len(born) else born[0]
            for field, source in (("pos", "Position"), ("rot", "Rotation")):
                target = getattr(actor, field)
                for axis in ("x","y","z"):
                    setattr(target, axis, round(pose.get(source, {}).get(axis, 0) * 100))
    db.conn.execute("""CREATE TABLE IF NOT EXISTS private_dungeon_poses(
        player INTEGER NOT NULL, dungeon INTEGER NOT NULL, visit INTEGER NOT NULL,
        pos BLOB NOT NULL, rot BLOB NOT NULL, PRIMARY KEY(player,dungeon)) WITHOUT ROWID""")
    lead = rsp.team.char1
    previous_pose = db.conn.execute(
        "SELECT pos,rot FROM private_dungeon_poses WHERE player=? AND dungeon=? AND visit=?",
        (pid, did, data.enter_times)).fetchone()
    if previous_pose:
        lead.pos.ParseFromString(previous_pose[0])
        lead.rot.ParseFromString(previous_pose[1])
    else:
        db.conn.execute("INSERT OR REPLACE INTO private_dungeon_poses VALUES(?,?,?,?,?)",
                        (pid,did,data.enter_times,lead.pos.SerializeToString(),lead.rot.SerializeToString()))
    opened = {idx for kind, idx, _ in facts(pid,did) if kind=="box"}
    for point in scene(did).get("CollectionTreasureInfos", ()):
        idx = point.get("Index", 0)
        box = box_config(did, idx)
        if box:
            rsp.treasure_boxes.add(index=idx,box_id=point["ID"],type=box.get("new_treasure_box_type",0),
                                   state=pb.TreasureBoxState_Open if idx in opened else pb.TreasureBoxState_Close)
    rsp.dungeon_data.CopyFrom(data)
    return pb.StatusCode_OK


def finish(session, rsp):
    pid, did = session.player_id, session.dungeon[0]
    if not did:
        return pb.StatusCode_PlayerNotInDungeon
    data = load(pid,did)
    if record(pid,did,"finish",data.enter_times):
        data.finish_times += 1
        data.last_finish_time = int(time.time())
        track(pid,data)
    rsp.scene_id=session.scene_id
    rsp.dungeon_data.CopyFrom(data)
    return pb.StatusCode_OK


def objectives(session, req, rsp):
    if not config(req.dungeon_id):
        return pb.StatusCode_CFG_NOT_FOUND
    data=load(session.player_id,req.dungeon_id)
    rsp.dungeon_data.CopyFrom(data)
    for aid,count,_ in tasks(session.player_id,data):
        if count:
            rsp.task_data_lst.add(achieve_id=aid,count=count)
    return pb.StatusCode_OK


def monster(session, req, rsp):
    did,pid=session.dungeon[0],session.player_id
    if not did:
        return pb.StatusCode_PlayerNotInDungeon
    points=scene(did).get("MonsterInfos", ())
    idx,unit=req.monster_index,req.monster_id
    configured = {r.get("UnitID") for r in points if r.get("Index",0)==idx}
    if unit in configured:
        record(pid,did,"monster",idx,unit)
    tags=_table("DungeonMonsterTags","monster_character_data")
    # Some bosses are spawned by dungeon scripts. Monster.json verifies the reported unit's tag.
    if tags.get(unit,{}).get("new_tag")==3 and (not configured or unit in configured):
        record(pid,did,"boss",idx,unit)
    data=load(pid,did)
    if (not configured or unit in configured) and idx not in data.monsters:
        data.monsters.append(idx)
    track(pid,data)
    rsp.monster_index=idx
    return pb.StatusCode_OK


def coin(session, req, rsp):
    did,pid=session.dungeon[0],session.player_id
    if not did:
        return pb.StatusCode_PlayerNotInDungeon
    idx=req.coin_index
    if not any(r.get("Index",0)==idx for r in scene(did).get("CoinInfos", ())):
        return pb.StatusCode_BAD_REQ
    data=load(pid,did)
    rsp.coin_index=idx
    if idx not in data.coins:
        chart = _table("PrivateDungeonMap", "dungeons", "id").get(did, {})
        star = next((s for s in chart.get("coins", ()) if s["index"] == idx), None)
        if star:
            raw = db.conn.execute(
                "SELECT pos FROM private_dungeon_poses WHERE player=? AND dungeon=? AND visit=?",
                (pid, did, data.enter_times)).fetchone()
            if not raw:
                return pb.StatusCode_BAD_REQ
            pose = pb.Vector3.FromString(raw[0])
            if math.hypot(pose.x / 100-star["x"], pose.z / 100-star["z"]) > star["radius"]:
                return pb.StatusCode_BAD_REQ
        grant(session,{106:1},rsp.rewards)
        data.coins.append(idx)
        track(pid,data)
    return pb.StatusCode_OK


def open_box(session, req, rsp):
    pid,did=session.player_id,session.dungeon[0]
    if not did:
        return pb.StatusCode_PlayerNotInDungeon
    if req.loc_type != pb.TreasureBoxLocType_DUNGEON or req.box_type != pb.TREASURE_BOX_TYPE_STATIC:
        return pb.StatusCode_BAD_REQ
    box=box_config(did,req.treasure_box_index)
    if not box:
        return pb.StatusCode_CFG_NOT_FOUND
    if record(pid,did,"box",req.treasure_box_index):
        data=load(pid,did)
        if box.get("new_treasure_box_reward_type")==4:
            data.is_open_secret_box=True
        track(pid,data)
    return pb.StatusCode_OK


def reward_items(pid, reward_id):
    level, world=db.conn.execute("SELECT level,world_level FROM players WHERE player_id=?",(pid,)).fetchone()
    world_cfg=_table("Reward","world_level_pool").get(reward_id,{})
    groups=world_cfg.get("world_level_pool_group", ())
    pools=[g["pool_i_d"] for g in groups if g.get("world_level",0)==world]
    if not pools:
        pools=[g["pool_i_d"] for g in groups if not g.get("world_level")]
    items=Counter()
    for pool_id in pools or [reward_id]:
        pool=_table("Reward","reward_pool").get(pool_id,{})
        ids=[g["reward_pool_group_i_d"] for g in pool.get("reward_pool_group",())]
        for row_id in ids or [pool_id]:
            row=_table("Reward","reward_item_pool").get(row_id,{})
            for group in row.get("reward_item_pool_group",()):
                if group.get("min_level_limit",0)<=level<= (group.get("max_level_limit") or 0xffffffff):
                    lo=group.get("item_min_count",1)
                    count=random.randint(lo,group.get("item_max_count",lo))
                    if count>0:
                        items[group["item_i_d"]]+=count
    return items


def grant(session, amounts, rewards):
    pid=session.player_id
    notice=pb.PackNotice(status=pb.StatusCode_OK)
    for item_id,count in amounts.items():
        raw=db.get_item_detail(pid,item_id)
        total=pb.ItemDetail.FromString(raw) if raw else make_item(item_id,0,pid)
        if not total or total.main_item.WhichOneof("item")!="base_item":
            raise ValueError("Unsupported non-stackable dungeon reward")
        total.main_item.base_item.num+=count
        db.set_item_detail(pid,total.SerializeToString(),item_id)
        notice.items.add().CopyFrom(total)
        delta=rewards.add()
        delta.CopyFrom(total)
        delta.main_item.base_item.num=count
    if 1 in amounts:
        level,exp=db.conn.execute("SELECT level,exp FROM players WHERE player_id=?",(pid,)).fetchone()
        exp+=amounts[1]
        levels=_table("Level","player_level")
        while level+1 in levels and levels[level].get("need_exp",0)>0 and exp>=levels[level]["need_exp"]:
            exp-=levels[level]["need_exp"]
            level+=1
        db.conn.execute("UPDATE players SET level=?,exp=? WHERE player_id=?",(level,exp,pid))
        session.send(MsgId.PlayerLevelExpNotice,pb.PlayerLevelExpNotice(status=pb.StatusCode_OK,level=level,exp=exp),0)
    session.send(MsgId.PackNotice,notice,0)


def claim(session, req, rsp):
    pid,did=session.player_id,req.dungeon_id
    cfg=config(did)
    if not cfg:
        return pb.StatusCode_CFG_NOT_FOUND
    data=load(pid,did)
    if data.task_finish_reward==pb.Reward:
        return pb.StatusCode_REPEAT_GET_REWARD
    if not data.all_task_finished or not tasks(pid,data):
        return pb.StatusCode_DungeonCantReward
    items=reward_items(pid,cfg.get("reward_i_d"))
    special_tags = {pb.EBagItemTag_Weapon, pb.EBagItemTag_Armor, pb.EBagItemTag_Poster,
                    pb.EBagItemTag_Fashion, pb.EBagItemTag_Inscription, pb.EBagItemTag_Card,
                    pb.EBagItemTag_TreasureMap, pb.EBagItemTag_Pet}
    item_cfg = _table("Item", "item")
    # Only issue rewards supported by the local inventory. Keep any pending claim intact.
    if not items or any(k not in item_cfg or item_cfg[k]["new_bag_item_tag"] in special_tags for k in items):
        return pb.StatusCode_CFG_NOT_FOUND
    grant(session,items,rsp.rewards)
    data.task_finish_reward=pb.Reward
    save(pid,data)
    return pb.StatusCode_OK



def move_inside(session, record):
    pid, did = session.player_id, session.dungeon[0]
    data = load(pid, did)
    lead = next((item for item in record.char_recorder_data_lst if item.char_id == data.char1), None)
    if lead is None:
        return
    if lead.HasField("pos"):
        db.conn.execute("UPDATE private_dungeon_poses SET pos=? WHERE player=? AND dungeon=? AND visit=?",
                        (lead.pos.SerializeToString(), pid, did, data.enter_times))
    if lead.HasField("rot"):
        db.conn.execute("UPDATE private_dungeon_poses SET rot=? WHERE player=? AND dungeon=? AND visit=?",
                        (lead.rot.SerializeToString(), pid, did, data.enter_times))
    notice = pb.PlayerSceneSyncDataNotice(status=pb.StatusCode_OK)
    notice.data.add(player_id=pid).data.add().CopyFrom(record)
    session.send(MsgId.PlayerSceneSyncDataNotice, notice, 0)
