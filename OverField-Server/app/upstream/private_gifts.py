"""Grant catalogued virtual gifts in the selected private realm. No merchant or billing integration."""
from collections import Counter
from datetime import timedelta
import re
from uuid import uuid4

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res
from private_dungeons import _table
import private_shop as shop


def catalog():
    labels = res["PrivateGiftLabels"]
    return [{"shop": board["i_d"], "grid": grid["grid_i_d"], "pool": grid["shop_pool_i_d"],
             "section": labels["sections"][str(board["i_d"])],
             "title": labels["titles"][str(grid["shop_pool_i_d"])]}
            for board in res["Shop"]["grid"]["datas"] for grid in board.get("items", ())
            if grid.get("goods_i_d")]


def setup():
    db.conn.execute("""CREATE TABLE IF NOT EXISTS private_gift_claims(
        player INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
        request TEXT NOT NULL, shop INTEGER NOT NULL, grid INTEGER NOT NULL,
        result BLOB NOT NULL, PRIMARY KEY(player,request)) WITHOUT ROWID""")


def request_key(session, packet_id):
    if not hasattr(session, "gift_session"):
        session.gift_session = uuid4().hex
    return f"{session.gift_session}-{packet_id}" if packet_id else uuid4().hex


def box_contents(player, item, level, choice=0):
    template=_table("GiftBag","gift_bag").get(item)
    if not template or level < template.get("player_lv",1):
        return None
    result=Counter()
    for group in template.get("gift_bag_group",()):
        contents=shop.fixed_gift(player,group["reward_pool_group_i_d"],level)
        if not contents:
            return None
        result.update(contents)
    if template.get("new_special_gift_bag"):
        return Counter({choice:result[choice]}) if choice in result else None
    return result if result and not choice else None


def open_box(session, req, reply):
    player=session.player_id
    if not 1<=req.num<=999:
        return pb.StatusCode_BAD_REQ
    stock=shop.balance(player,req.item_id)
    if not stock or stock.main_item.WhichOneof("item")!="base_item" or stock.main_item.base_item.num<req.num:
        return pb.StatusCode_ITEM_NOT_ENOUGH
    level=db.get_players_info(player,"level")[0]
    contents=box_contents(player,req.item_id,level,req.choose_item_id)
    if not contents:
        return pb.StatusCode_BAD_REQ
    if not db.conn.in_transaction:
        db.conn.execute("BEGIN")
    db.conn.execute("SAVEPOINT private_open_gift")
    notice=pb.PackNotice(status=pb.StatusCode_OK)
    try:
        stock.main_item.base_item.num-=req.num
        db.set_item_detail(player,stock.SerializeToString(),req.item_id)
        notice.items.add().CopyFrom(stock)
        shop.award(player,{i:n*req.num for i,n in contents.items()},reply,notice)
    except (shop.UnsupportedGoods,OverflowError):
        db.conn.execute("ROLLBACK TO private_open_gift")
        del reply.items[:]
        return pb.StatusCode_CFG_NOT_FOUND
    except BaseException:
        db.conn.execute("ROLLBACK TO private_open_gift")
        raise
    finally:
        db.conn.execute("RELEASE private_open_gift")
    session.send(MsgId.PackNotice,notice,0)
    return pb.StatusCode_OK


def claim(player, shop_id, grid_id, request_id, *, pool=0):
    """Caller holds the game lock and commits before sending returned game notices."""
    delivery = pb.PaySendGoodsNotice(shop_id=shop_id)
    pack = pb.PackNotice(status=pb.StatusCode_OK)
    def failure(status):
        return status, pb.PaySendGoodsNotice(), pb.PackNotice(), []
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", request_id):
        return failure(pb.StatusCode_BAD_REQ)
    setup()
    old = db.conn.execute("SELECT shop,grid,result FROM private_gift_claims WHERE player=? AND request=?",
                          (player,request_id)).fetchone()
    if old:
        if old[:2] != (shop_id,grid_id):
            return failure(pb.StatusCode_BAD_REQ)
        delivery.ParseFromString(old[2])
        return pb.StatusCode_OK, delivery, pack, []
    board = _table("Shop","grid").get(shop_id,{})
    grid = next((g for g in board.get("items",()) if g["grid_i_d"]==grid_id and g.get("goods_i_d")),None)
    if not grid or pool not in (0,grid["shop_pool_i_d"],grid.get("first_buy_shop_pool_i_d")):
        return failure(pb.StatusCode_CFG_NOT_FOUND)
    row = db.conn.execute("SELECT level FROM players WHERE player_id=?", (player,)).fetchone()
    if not row:
        return failure(pb.StatusCode_BAD_REQ)
    level = row[0]
    instant = shop.now()
    key = shop.counter_key(grid,instant)
    bought = shop.count(player,key)
    selected = grid.get("first_buy_shop_pool_i_d",grid["shop_pool_i_d"]) if not bought else grid["shop_pool_i_d"]
    alternatives = [entry for entry in _table("Shop","pool")[selected]["items"]
                    if entry.get("min_level",1)<=level<=entry.get("max_level",100)]
    if not alternatives:
        return failure(pb.StatusCode_CFG_NOT_FOUND)
    # Archived private gifts remain selectable. Prefer the dated variant corresponding to today.
    goods = next((g for g in alternatives if shop.in_window(g,instant)),alternatives[-1])
    totals = Counter()
    for item,num in ((goods.get("item_i_d"),goods.get("item_num",0)),
                     (goods.get("add_item_i_d"),goods.get("add_item_num",0))):
        if item and num:
            totals[item]+=num
    extra = []
    activity = next((r for r in res["ActivityGift"]["activity_gift"]["datas"] if r.get("shop_i_d")==shop_id),None)
    if activity and not totals:
        for stage in activity["activity_gift_item"]:
            for reward in stage["a_activity_gift_reward_item"]:
                totals[reward["item_i_d"]]+=reward.get("item_num",0)
        extra.append((MsgId.ActivityGiftBuyNotice, pb.ActivityGiftBuyNotice(
            status=pb.StatusCode_OK, activity_gift_id=activity["i_d"])))
    battle_pass = next((r for r in res["BattlePass"]["battle_pass"]["datas"] if r.get("shop_i_d")==shop_id),None)
    if battle_pass:
        owns_pass = db.conn.execute("SELECT 1 FROM player_kv WHERE player_id=? AND ns='private_pass' AND k=?",
                                    (player,str(battle_pass["i_d"]))).fetchone()
        if not owns_pass:
            for tier in battle_pass.get("battle_pass_level_ups", ()):
                rewards = tier.get("battle_pass_reward", ())
                if len(rewards)>1:
                    premium = rewards[1]
                    totals[premium["item_i_d"]]+=premium.get("item_num",1)
    if not totals:
        return failure(pb.StatusCode_CFG_NOT_FOUND)
    item_table = _table("Item","item")
    for item, quantity in totals.copy().items():
        if item_table.get(item,{}).get("new_bag_item_tag")==pb.EBagItemTag_Gift:
            contents=box_contents(player,item,level)
            if contents:
                del totals[item]
                for part,units in contents.items():
                    totals[part]+=units*quantity
    if any(i not in item_table or n<=0 for i,n in totals.items()):
        return failure(pb.StatusCode_CFG_NOT_FOUND)
    # BEGIN keeps releasing the nested savepoint from committing an enclosing HTTP/game operation.
    if not db.conn.in_transaction:
        db.conn.execute("BEGIN")
    db.conn.execute("SAVEPOINT private_gift")
    try:
        for item,qty in totals.items():
            if item_table[item]["new_bag_item_tag"]==pb.EBagItemTag_Fashion and shop.already_owned(player,item,pb.EBagItemTag_Fashion):
                delivery.items.add().CopyFrom(shop.balance(player,item))
                continue
            shop.award(player,{item:qty},delivery,pack)
        if shop_id==200:
            timestamp=int(instant.timestamp())
            current=db.get_month_card_over_due_time(player) or 0
            expiry=min(max(timestamp,current)+30*86400,timestamp+180*86400)
            db.conn.execute("""INSERT INTO month_card(player_id,over_due_time) VALUES(?,?)
                ON CONFLICT(player_id) DO UPDATE SET over_due_time=excluded.over_due_time""",(player,expiry))
            extra.append((MsgId.MonthCardNotice,pb.MonthCardNotice(status=pb.StatusCode_OK,over_due_time=expiry)))
        if battle_pass:
            db.conn.execute("INSERT OR IGNORE INTO player_kv VALUES(?,'private_pass',?,1)",(player,str(battle_pass["i_d"])))
            extra.append((MsgId.ActivityBattlePassBuyNotice,pb.ActivityBattlePassBuyNotice(
                status=pb.StatusCode_OK,activity_battle_pass_id=battle_pass["i_d"],battle_pass_lv=1)))
        db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'shop_buy',?,?)",(player,key,bought+1))
        delivery.status=pb.StatusCode_OK
        shop.grid_info(delivery.grids,shop_id,grid,goods,bought+1)
        delivery.grids.pool_id=selected
        db.conn.execute("INSERT INTO private_gift_claims VALUES(?,?,?,?,?)",
                        (player,request_id,shop_id,grid_id,delivery.SerializeToString()))
    except (shop.UnsupportedGoods, OverflowError):
        db.conn.execute("ROLLBACK TO private_gift")
        return failure(pb.StatusCode_CFG_NOT_FOUND)
    except BaseException:
        db.conn.execute("ROLLBACK TO private_gift")
        raise
    finally:
        db.conn.execute("RELEASE private_gift")
    return pb.StatusCode_OK, delivery, pack, extra


def month_reward(session, reply):
    player=session.player_id
    now=shop.now()
    if (db.get_month_card_over_due_time(player) or 0)<=int(now.timestamp()):
        return pb.StatusCode_FAIL
    key=(now-timedelta(hours=4)).date().isoformat()
    days=db.get_month_card_reward_days(player) or 0
    reply.month_card_reward_days=days
    fresh=db.conn.execute("INSERT OR IGNORE INTO player_kv VALUES(?,'private_month_day',?,1)",(player,key)).rowcount
    if not fresh:
        return pb.StatusCode_MOON_CARD_DAYS_LIMIT
    config=next(r for r in res["GlobalConfig"]["config"]["datas"] if r["key"]=="MoonCard_Rveryday_Currency")
    item,qty=map(int,config["values"][0].split(":"))
    notice=pb.PackNotice(status=pb.StatusCode_OK)
    shop.award(player,{item:qty},pb.PaySendGoodsNotice(),notice)
    reply.month_card_reward_days=days+1
    db.set_month_card_reward_days(player,days+1)
    session.send(MsgId.PackNotice,notice,0)
    return pb.StatusCode_OK
