"""Keep furniture instance IDs distinct from the permanent chairs in the same scene."""
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res

_seats_table = None
_reserved = frozenset()


def chair_ids():
    global _seats_table, _reserved
    table=res["HomeChairSeats"]["seats"]
    if table is not _seats_table:
        _seats_table=table
        _reserved=frozenset(map(int,table))
    return _reserved


def reserve_chair_ids():
    """Migrate older placed/held IDs once, in the surrounding realm startup transaction."""
    from private_channels import HOME, owner_for
    from private_garden_actions import fresh_id
    from realm_uids import public_uid
    if db.conn.execute("SELECT 1 FROM realm_garden_migration WHERE version=2").fetchone():
        return
    fixed=chair_ids()
    marks=",".join("?" for _ in fixed)
    records=db.conn.execute(f"SELECT channel_id,player_id,furniture_id,furniture_detail_blob FROM furnitures "
                            f"WHERE scene_id=? AND furniture_id IN ({marks})",(HOME,*fixed)).fetchall()
    for channel,player,old,raw in records:
        new=fresh_id(player,HOME,channel)
        item=pb.FurnitureDetailsInfo.FromString(raw)
        item.furniture_id=new
        db.conn.execute("UPDATE furnitures SET furniture_id=?,furniture_detail_blob=? WHERE scene_id=? "
                        "AND channel_id=? AND player_id=? AND furniture_id=?",
                        (new,item.SerializeToString(),HOME,channel,player,old))
        owner=owner_for(channel)
        if owner:
            db.conn.execute("UPDATE garden_residents SET furniture=? WHERE owner=? AND furniture=?",(new,owner,old))
    for player,old in db.conn.execute(f"SELECT player,fid FROM realm_hand_furniture WHERE fid IN ({marks})",tuple(fixed)).fetchall():
        new=fresh_id(player,HOME,public_uid(player))
        db.conn.execute("UPDATE realm_hand_furniture SET fid=? WHERE player=? AND fid=?",(new,player,old))
    # Saved furniture schemes are resolved against the room and its reserved IDs when applied.
    db.conn.execute("INSERT INTO realm_garden_migration VALUES(2)")
