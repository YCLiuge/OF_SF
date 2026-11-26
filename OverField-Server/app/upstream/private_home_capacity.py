"""Private home load is measured in national furniture points; owner object quotas are separate."""
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res


def points(template):
    row = res.index("HomeItem", "home_item", "item_i_d").get(template, {})
    return row.get("home_item_point_num", 0)


def maximum():
    config = res.index("GlobalConfig", "config", "key")
    return int(config["PrivateHomeItemPointMax"]["values"][0])


def setup():
    conn = db.conn
    conn.create_function("private_home_weight", 1, lambda raw: points(
        pb.FurnitureDetailsInfo.FromString(raw).furniture_item_id), deterministic=True)
    # This connection owns all live placements. The temp index follows writes and rollbacks,
    # including INSERT OR REPLACE, without parsing room-wide protobufs on every edit.
    conn.execute("""CREATE TEMP TABLE IF NOT EXISTS home_weights(
        scene INTEGER, channel INTEGER, owner INTEGER, fid INTEGER, weight INTEGER,
        PRIMARY KEY(scene,channel,owner,fid)) WITHOUT ROWID""")
    conn.execute("DELETE FROM temp.home_weights")
    cols = "scene_id,channel_id,player_id,furniture_id,private_home_weight(furniture_detail_blob)"
    conn.execute(f"INSERT INTO home_weights SELECT {cols} FROM furnitures WHERE scene_id=9999")
    del_row = """DELETE FROM home_weights WHERE scene=OLD.scene_id AND channel=OLD.channel_id
                 AND owner=OLD.player_id AND fid=OLD.furniture_id;"""
    add_row = """INSERT OR REPLACE INTO home_weights SELECT NEW.scene_id,NEW.channel_id,NEW.player_id,
                 NEW.furniture_id,private_home_weight(NEW.furniture_detail_blob) WHERE NEW.scene_id=9999;"""
    for event, statements in (("insert",add_row),("delete",del_row),("update",del_row+add_row)):
        conn.execute(f"CREATE TEMP TRIGGER IF NOT EXISTS home_weight_{event} AFTER {event} ON main.furnitures BEGIN {statements} END")


def used(channel):
    return db.conn.execute("SELECT COALESCE(SUM(weight),0) FROM home_weights WHERE scene=9999 AND channel=?",
                           (channel,)).fetchone()[0]


def can_place(session, template):
    if session.channel_id < session.realm_rules.uid_start:
        return True
    return used(session.channel_id) + points(template) <= maximum()
