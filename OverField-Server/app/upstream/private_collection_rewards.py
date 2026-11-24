"""Native discovery, scene collection levels and one-time collection rewards."""
from private_activity_rewards import deliver_once
from private_collection_achievements import discovered, restore
from private_dungeons import _table, reward_items
from private_map_progress import collected, collectible, rebuild
from proto import net_pb2 as pb
from utils import db

MANUAL = {1: "collection_item_file", 2: "collection_item_item",
          3: "collection_item_book", 6: "collection_item_data"}
LEVELS = {1: "file", 2: "collect_item", 3: "book", 4: "tape", 5: "portal",
          6: "data", 7: "photo", 8: "crystal", 9: "photo_item"}


def reward_pool(config):
    table = MANUAL.get(config.get("new_collection_type"))
    if table:
        row = _table("CollectionItem", table).get(config["relate_i_d"], {})
        return row.get("reward_i_d")


def region(item):
    code = item // 1000 % 100
    return {0: 100, 99: 9999, 51: 1001, 52: 1002, 53: 1003}.get(code, code)


def in_scene(pid, scene):
    known = _table("CollectionItem", "collection_item")
    extras = dict(db.conn.execute("SELECT CAST(k AS INTEGER), CAST(v AS INTEGER) FROM player_kv "
                                  "WHERE player_id=? AND ns='collection_scene'", (pid,)))
    for kind, raw in db.get_collection(pid):
        data = pb.PBCollectionRewardData.FromString(raw)
        item = data.item_id
        if item not in known or known[item].get("new_collection_type") != kind:
            continue
        locations = collectible(item)
        if scene in locations or (not locations and extras.get(item, region(item)) == scene):
            yield kind, data


def fill_level(group, scene, count):
    group.level, group.exp = 1, count
    name = LEVELS.get(group.type)
    if name:
        table = name + "_level"
        row = _table("Level", table).get(scene, {})
        for entry in row.get(table + "_info", ()):
            cost = entry.get("need_exp", 0)
            if not cost or group.exp < cost:
                break
            group.exp -= cost
            group.level = entry["level"] + 1


def reopen_legacy(pid):
    # Older OF_SF wrote Reward on discovery, without granting a manual collection reward.
    # A real claim receipt takes priority over the historical display flag.
    if db.conn.execute("SELECT 1 FROM player_kv WHERE player_id=? AND ns='collection_rewards' AND k='v1'", (pid,)).fetchone():
        return
    claimed = {key for (key,) in db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system='collection'", (pid,))}
    known = _table("CollectionItem", "collection_item")
    for kind, raw in db.get_collection(pid):
        if kind not in MANUAL:
            continue
        item = pb.PBCollectionRewardData.FromString(raw)
        config = known.get(item.item_id, {})
        if (item.status == pb.Reward and str(item.item_id) not in claimed
                and config.get("new_collection_type") == kind and reward_pool(config)):
            item.status = pb.NotReward
            db.set_collection(pid, item.item_id, kind, item.SerializeToString())
    db.conn.execute("INSERT INTO player_kv VALUES(?,'collection_rewards','v1',1)", (pid,))


def fill_scene(session, scene_data):
    pid, scene = session.player_id, session.scene_id
    reopen_legacy(pid)
    restore(session)
    for kind in range(12):
        scene_data.collections.add(type=kind)
    for kind, item in in_scene(pid, scene):
        scene_data.collections[kind].item_map[item.item_id].CopyFrom(item)
    for group in scene_data.collections:
        fill_level(group, scene, len(group.item_map))


def discover(session, req, rsp):
    pid, scene, item = session.player_id, session.scene_id, req.item_id
    config = _table("CollectionItem", "collection_item").get(item)
    if not config:
        return pb.StatusCode_BAD_REQ
    locations = collectible(item)
    if locations and (session.dungeon[0] or scene not in locations):
        return pb.StatusCode_BAD_REQ
    if not locations and region(item) in _table("Level", "file_level") and region(item) != scene:
        return pb.StatusCode_BAD_REQ
    if db.get_collection(pid, item):
        return pb.StatusCode_ALREADY_COLLECTED
    rebuild(pid)
    reopen_legacy(pid)
    restore(session)
    kind = config["new_collection_type"]
    pickup = _table("Item", "item").get(item)
    if pickup:
        status = deliver_once(session, "collection_pickup", item, {pickup["text_i_d"]: 1}, rsp.items)
        if status != pb.StatusCode_OK:
            return status
    group = rsp.collections.add(type=kind)
    entry = group.item_map[item]
    entry.item_id = item
    entry.status = pb.Reward if kind in (8, 9) else pb.NotReward
    db.set_collection(pid, item, kind, entry.SerializeToString())
    if not locations and region(item) != scene:
        db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'collection_scene',?,?)", (pid, str(item), scene))
    discovered(session, item, kind)
    collected(session, item, kind)
    fill_level(group, scene, sum(1 for other, _ in in_scene(pid, scene) if other == kind))
    return pb.StatusCode_OK


def claim(session, req, rsp):
    pid, item = session.player_id, req.item_id
    config = _table("CollectionItem", "collection_item").get(item)
    if not config:
        return pb.StatusCode_CFG_NOT_FOUND
    reopen_legacy(pid)
    raw = db.get_collection(pid, item)
    if not raw:
        return pb.StatusCode_NOT_COLLECTED
    kind, blob = raw
    data = pb.PBCollectionRewardData.FromString(blob)
    if kind != config["new_collection_type"] or data.item_id != item:
        return pb.StatusCode_BAD_REQ
    rsp.collection_reward_data.CopyFrom(data)
    if data.status == pb.Reward:
        return pb.StatusCode_COLLECTION_ALREADY_REWARDED
    pool = reward_pool(config)
    if pool:
        status = deliver_once(session, "collection", item, reward_items(pid, pool), rsp.items)
    else:
        inserted = db.conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,'collection',?)",
                                   (pid, str(item))).rowcount
        status = pb.StatusCode_OK if inserted else pb.StatusCode_REPEAT_GET_REWARD
    if status not in (pb.StatusCode_OK, pb.StatusCode_REPEAT_GET_REWARD):
        return status
    data.status = pb.Reward
    db.set_collection(pid, item, kind, data.SerializeToString())
    rsp.collection_reward_data.CopyFrom(data)
    return pb.StatusCode_COLLECTION_ALREADY_REWARDED if status == pb.StatusCode_REPEAT_GET_REWARD else status
