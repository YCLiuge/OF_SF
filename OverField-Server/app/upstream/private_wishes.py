"""Store native wish lists, charge gift orders and deliver both parties' mail in one transaction."""
from datetime import timedelta
import time

from network.msg_id import MsgId
from proto import net_pb2 as pb
from server.scene_data import online_session
from utils import db
from utils.pb_create import make_PlayerBriefInfo
from private_crafts import payments, save_pack
from utils.res_loader import res
from private_social_profile import config, friend_points
import private_shop as shop


def setup():
    db.conn.executescript("""
        CREATE INDEX IF NOT EXISTS private_wishes_grid ON private_wishes(shop,grid,player_id);
        CREATE TABLE IF NOT EXISTS private_friend_since(
            a INTEGER NOT NULL,b INTEGER NOT NULL,since INTEGER NOT NULL,PRIMARY KEY(a,b)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS private_wish_orders(
            id INTEGER PRIMARY KEY, giver INTEGER NOT NULL, recipient INTEGER NOT NULL,
            shop INTEGER NOT NULL, grid INTEGER NOT NULL, pool INTEGER NOT NULL, created INTEGER NOT NULL,
            item INTEGER NOT NULL, units INTEGER NOT NULL, extra INTEGER NOT NULL, extra_units INTEGER NOT NULL,
            replacement INTEGER NOT NULL, price INTEGER NOT NULL, unique_goods INTEGER NOT NULL,
            closing TEXT NOT NULL, sent INTEGER NOT NULL, count_at_send INTEGER NOT NULL,
            mail INTEGER NOT NULL DEFAULT 0, UNIQUE(recipient,shop,grid,created));
        CREATE INDEX IF NOT EXISTS private_wish_pending ON private_wish_orders(recipient,id) WHERE mail=0;
    """)


def friendship_changed(player, other, status):
    pair = tuple(sorted((player, other)))
    if status == pb.FriendStatus_FRIEND:
        db.conn.execute("INSERT OR IGNORE INTO private_friend_since VALUES(?,?,?)", (*pair, int(time.time())))
    else:
        db.conn.execute("DELETE FROM private_friend_since WHERE a=? AND b=?", pair)


def weekly(player, instant):
    day = (instant - timedelta(hours=config("SystemRefreshTime"))).date()
    monday = (day - timedelta(days=day.weekday())).isoformat()
    row = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='wish_week' AND k='sent'", (player,)).fetchone()
    previous, _, count = str(row[0]).partition("/") if row else ("", "", "")
    return monday, int(count) if previous == monday else 0


def by_friend(session, req, rsp):
    target = req.player_id
    rsp.player_id = target
    if not target:
        return pb.StatusCode_OK
    if target != session.player_id and db.get_friend_info(session.player_id, target, "friend_status")[0] != 2:
        return pb.StatusCode_FRIEND_NOT_EXIST
    rows = db.conn.execute("SELECT shop,grid,pool,created FROM private_wishes WHERE player_id=? ORDER BY created,shop,grid",(target,))
    for board,grid,pool,created in rows:
        rsp.wish_list.add(shop_id=board, grid_id=grid, pool_id=pool, create_time=created)
    rsp.week_send_count = weekly(session.player_id, shop.now())[1]
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
    rsp.week_send_count = weekly(session.player_id, shop.now())[1]
    return pb.StatusCode_OK


def selection(player, board, grid_id, instant):
    grid = next((g for g in res.index("Shop","grid").get(board,{}).get("items",())
                 if g["grid_i_d"] == grid_id and g.get("is_gift")), None)
    info = res.index("Shop","info").get(board,{})
    if not grid or not shop.in_window(info,instant):
        return None
    goods = shop.select_goods(grid,db.get_players_info(player,"level")[0],instant)
    return (grid, goods, info) if goods and goods.get("item_i_d") else None


def operate(session, req, rsp):
    player = session.player_id
    key = (player, req.shop_id, req.grid_id)
    rsp.operate_type, rsp.shop_id, rsp.grid_id = req.operate_type, req.shop_id, req.grid_id
    old = db.conn.execute("SELECT pool FROM private_wishes WHERE player_id=? AND shop=? AND grid=?", key).fetchone()
    if req.operate_type == pb.WishListOperateType_Del:
        if old is None:
            return pb.StatusCode_WISH_LIST_NOT_EXIST
        rsp.pool_id = old[0]
        db.conn.execute("DELETE FROM private_wishes WHERE player_id=? AND shop=? AND grid=?",key)
        return pb.StatusCode_OK
    if req.operate_type != pb.WishListOperateType_Add:
        return pb.StatusCode_BAD_REQ
    instant = shop.now()
    chosen = selection(*key,instant)
    if not chosen:
        return pb.StatusCode_WISH_LIST_SHOP_ERR
    rsp.pool_id = chosen[0]["shop_pool_i_d"]
    if old:
        return pb.StatusCode_WISH_LIST_EXIST
    size = db.conn.execute("SELECT COUNT(*) FROM private_wishes WHERE player_id=?",(player,)).fetchone()[0]
    if size >= config("WishListsMaximumNumber"):
        return pb.StatusCode_WISH_LIST_LIMIT
    # Distinguish new wishes for the same product even after an immediate delete/re-add.
    stamp_key = f"{req.shop_id}/{req.grid_id}"
    last = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='wish_stamp' AND k=?",(player,stamp_key)).fetchone()
    created = max(int(instant.timestamp()), int(last[0])+1 if last else 0)
    db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'wish_stamp',?,?)",(player,stamp_key,created))
    db.conn.execute("INSERT INTO private_wishes VALUES(?,?,?,?,?)",(*key,rsp.pool_id,created))
    return pb.StatusCode_OK


def send_mail(pid, title, content, items, instant, peer, *, sender="罗塔售后部", model_id=0, overdue_days=0):
    prev = db.conn.execute("SELECT max(mail_id) FROM mail WHERE player_id=?",(pid,)).fetchone()[0] or 0
    seq = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='wish_mail' AND k='last'",(pid,)).fetchone()
    mid = max(prev, int(seq[0]) if seq else 0) + 1
    msg = pb.MailBriefData(mail_id=mid, title=title, sender=sender, model_id=model_id, content=content,
                           send_time=int(instant.timestamp()),overdue_day=overdue_days,content_type=pb.MailContentType_TEXT)
    for item,num in items:
        if item and num:
            msg.items.add(item_id=item, num=num)
    db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'wish_mail','last',?)",(pid,mid))
    db.set_mail(pid,mid,msg.SerializeToString())
    if peer:
        peer.send(MsgId.ReceiveMailNotice,pb.ReceiveMailNotice(status=pb.StatusCode_OK,mail=msg),0)
    return mid


def deliver(session):
    target = session.player_id
    instant = shop.now()
    orders = db.conn.execute("""SELECT id,giver,shop,grid,pool,item,units,extra,extra_units,replacement,
        price,unique_goods,closing FROM private_wish_orders WHERE recipient=? AND mail=0 ORDER BY id""",(target,)).fetchall()
    for oid,giver,board,grid,pool,item,units,extra,extra_units,replacement,price,unique,closing in orders:
        tag = res.index("Item","item")[item]["new_bag_item_tag"]
        duplicate = unique and shop.already_owned(target,item,tag)
        if unique and not duplicate:
            # An earlier outstanding mail also reserves a unique product until its attachment is claimed.
            other = db.conn.execute("""SELECT m.mail_blob FROM private_wish_orders o JOIN mail m
                ON m.player_id=o.recipient AND m.mail_id=o.mail
                WHERE o.recipient=? AND o.item=? AND o.mail>0""",(target,item))
            for raw, in other:
                letter = pb.MailBriefData.FromString(raw)
                if letter.mail_state != pb.MailState_CLAIMED and any(x.item_id==item for x in letter.items):
                    duplicate = True
                    break
        expired = closing and instant.strftime("%Y-%m-%d %H:%M:%S") > closing
        rewards = [(replacement,price)] if expired or duplicate else [(item,units),(extra,extra_units)]
        sender = db.get_players_info(giver,"player_name")[0]
        content = f"好友 {sender} 送来了心愿单商品 #{item} × {units}。"
        if expired or duplicate:
            content += "商品已经下架或重复，等价货币已随信附上。"
        mid = send_mail(target,f"来自{sender}的赠礼",content,rewards,instant,session)
        db.conn.execute("UPDATE private_wish_orders SET mail=? WHERE id=?",(mid,oid))
        notice = pb.WishListBuyNotice(status=pb.StatusCode_OK,shop_id=board)
        notice.grids.id, notice.grids.grid_id, notice.grids.pool_id = board, grid, pool
        session.send(MsgId.WishListBuyNotice,notice,0)


def buy(session, req, rsp):
    giver, target, wish = session.player_id, req.player_id, req.info
    rsp.player_id = target
    rsp.info.CopyFrom(wish)
    if giver == target or not target:
        return pb.StatusCode_BAD_REQ
    points = friend_points(giver,target)
    if points is None:
        return pb.StatusCode_FRIEND_NOT_EXIST
    key = (target,wish.shop_id,wish.grid_id)
    if wish.create_time:
        receipt = db.conn.execute("""SELECT giver,pool,count_at_send FROM private_wish_orders
            WHERE recipient=? AND shop=? AND grid=? AND created=?""",(*key,wish.create_time)).fetchone()
        if receipt and receipt[0] == giver and wish.pool_id in (0,receipt[1]):
            rsp.week_send_count = receipt[2]
            return pb.StatusCode_OK
    existed = db.conn.execute("SELECT pool,created FROM private_wishes WHERE player_id=? AND shop=? AND grid=?",key).fetchone()
    if not existed or wish.create_time not in (0,existed[1]):
        return pb.StatusCode_WISH_LIST_NOT_EXIST
    pool,created = existed
    if wish.pool_id not in (0,pool):
        return pb.StatusCode_WISH_LIST_SHOP_ERR
    instant = shop.now()
    selected = selection(*key,instant)
    if not selected or selected[0]["shop_pool_i_d"] != pool:
        return pb.StatusCode_WISH_LIST_SHOP_ERR
    grid,goods,board_info = selected
    virtual = bool(grid.get("goods_i_d"))
    required = "CommodityGiftIntimacyLimitNumber" if virtual else "RhombicStoneGiftIntimacyLimitNumber"
    if points < config(required):
        return pb.StatusCode_FRIEND_INTIMACY_NOT_ENOUGH
    since = db.conn.execute("SELECT since FROM private_friend_since WHERE a=? AND b=?",tuple(sorted((giver,target)))).fetchone()
    if since and int(instant.timestamp()) - since[0] < config("CommodityGiftFriendLimitTime") * 86400:
        return pb.StatusCode_FRIEND_DAYS_NOT_ENOUGH
    parts = goods.get("shop_currency_item",[])
    if len(parts)!=1:
        return pb.StatusCode_CFG_NOT_FOUND
    price = parts[0].get("wishlist_price") or parts[0].get("price",0)
    item = goods["item_i_d"]
    if not price or goods.get("item_num",0)<1:
        return pb.StatusCode_CFG_NOT_FOUND
    monday,used = weekly(giver,instant)
    if virtual:
        # The original recharge grid exchanges 600 price units for 60 paid stones.
        replacement = 108
        if price % 10:
            return pb.StatusCode_CFG_NOT_FOUND
        price = price//10
    else:
        if parts[0].get("currency_i_d") != 108:
            return pb.StatusCode_CFG_NOT_FOUND
        if used >= config("CommodityGiftLimitNumber"):
            return pb.StatusCode_WISH_LIST_SEND_LIMIT
        costs,status = payments(giver,{108:price})
        if status != pb.StatusCode_OK:
            return status
        save_pack(session,costs)
        replacement = 102
        used += 1
        db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'wish_week','sent',?)",(giver,f"{monday}/{used}"))
    deadline = min((v for v in (goods.get("closing_time"),board_info.get("closing_time")) if v),default="")
    db.conn.execute("""INSERT INTO private_wish_orders(giver,recipient,shop,grid,pool,created,item,units,extra,
        extra_units,replacement,price,unique_goods,closing,sent,count_at_send) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (giver,*key,pool,created,item,goods["item_num"],goods.get("add_item_i_d",0),goods.get("add_item_num",0),
         replacement,price,not goods.get("is_repeated_in_bag"),deadline,int(instant.timestamp()),used))
    db.conn.execute("DELETE FROM private_wishes WHERE player_id=? AND shop=? AND grid=?",key)
    name = db.get_players_info(target,"player_name")[0]
    send_mail(giver,"ROTA速达",f"已为好友 {name} 寄出心愿单商品 #{item}。",[],instant,session)
    peer = online_session(target)
    if peer:
        deliver(peer)
    rsp.week_send_count = used
    return pb.StatusCode_OK
