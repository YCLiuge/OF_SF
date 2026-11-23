"""Read the private realm's own wishlist and friend data."""
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_PlayerBriefInfo


def by_friend(session, req, rsp):
    target = req.player_id
    rsp.player_id = target
    if not target:
        return pb.StatusCode_OK
    if target != session.player_id and db.get_friend_info(session.player_id, target, "friend_status")[0] != 2:
        return pb.StatusCode_FRIEND_NOT_EXIST
    rows = db.conn.execute("SELECT shop,grid,pool,created FROM private_wishes WHERE player_id=? ORDER BY created,shop,grid",(target,))
    for shop,grid,pool,created in rows:
        rsp.wish_list.add(shop_id=shop, grid_id=grid, pool_id=pool, create_time=created)
    return pb.StatusCode_OK


def by_shop(session, req, rsp):
    rows = db.conn.execute("""
        SELECT f.friend_id,f.alias,f.friend_tag,f.friend_intimacy,f.friend_background
          FROM friend AS f JOIN private_wishes AS w ON w.player_id=f.friend_id
         WHERE f.player_id=? AND f.friend_status=2 AND w.shop=? AND w.grid=?
         ORDER BY f.friend_id""",(session.player_id,req.shop_id,req.grid_id))
    for friend_id,alias,tag,intimacy,background in rows:
        entry = rsp.info.add(alias=alias, friend_tag=tag, friend_intimacy=intimacy, friend_background=background)
        make_PlayerBriefInfo(friend_id, entry.info)
    # This private realm has never had a successful gift-send operation.
    return pb.StatusCode_OK
