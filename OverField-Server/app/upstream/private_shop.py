"""PC ShopInfo/ShopBuy share the selected goods, price schedule and per-role purchase counter."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item
from private_dungeons import _table
from utils.res_loader import res

CN=timezone(timedelta(hours=8))
INSTANCE_TAGS={pb.EBagItemTag_Weapon,pb.EBagItemTag_Armor,pb.EBagItemTag_Poster}
UNSUPPORTED={pb.EBagItemTag_Gift,pb.EBagItemTag_Card,pb.EBagItemTag_Inscription,pb.EBagItemTag_TreasureMap,
             pb.EBagItemTag_Pet,pb.EBagItemTag_MonthlyCard,pb.EBagItemTag_BattlePassCard,
             pb.EBagItemTag_MonthlyGiftCard,pb.EBagItemTag_BattlePassGiftCard,pb.EBagItemTag_MoonCard}


def now():
    return datetime.now(CN)


def in_window(row,instant):
    moment=instant.strftime("%Y-%m-%d %H:%M:%S")
    return row.get("opening_time","")<=moment<= (row.get("closing_time") or "9999")


def counter_key(grid,instant):
    # Legacy 1.1.12 saves use the pool ID and reset period as their purchase key.
    refresh=grid.get("new_shop_refresh_type")
    if refresh==2:
        day=int(instant.replace(hour=0,minute=0,second=0,microsecond=0).timestamp())//86400-(instant.hour<4)
        period=f"d{day}"
    elif refresh==3:
        year,week,_=instant.isocalendar()
        period=f"w{year:04d}-W{week:02d}"
    elif refresh==4:
        period="m"+instant.strftime("%Y%m")
    else:
        period="p"
    return f"{grid['shop_pool_i_d']}:{period}"


def count(player,key):
    row=db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='shop_buy' AND k=?",(player,key)).fetchone()
    return int(row[0]) if row else 0


def select_goods(grid,level,instant):
    pool=_table("Shop","pool").get(grid["shop_pool_i_d"],{})
    return next((v for v in pool.get("items",()) if v.get("min_level",1)<=level<=v.get("max_level",100)
                 and in_window(v,instant)),None)


def grid_info(view,shop,grid,goods,bought):
    view.id=shop
    view.grid_id=grid["grid_i_d"]
    view.pool_id=grid["shop_pool_i_d"]
    view.pool_index=goods.get("index",1)
    view.buy_times=bought


def info(session,req,rsp):
    rsp.shop_id=req.shop_id
    cfg=_table("Shop","grid").get(req.shop_id)
    if cfg is None:
        return pb.StatusCode_CFG_NOT_FOUND
    instant=now()
    if not in_window(_table("Shop","info").get(req.shop_id,{}),instant):
        return pb.StatusCode_OK
    level=db.get_players_info(session.player_id,"level")[0]
    for grid in cfg.get("items",()):
        goods=select_goods(grid,level,instant)
        if goods:
            grid_info(rsp.grids.add(),req.shop_id,grid,goods,count(session.player_id,counter_key(grid,instant)))
    return pb.StatusCode_OK


def quote(goods,bought,quantity):
    charges=Counter()
    for currency in goods.get("shop_currency_item",()):
        if not currency.get("currency_i_d"):
            return None
        unit=currency.get("price",0)
        if bought and currency.get("increasing_i_d"):
            steps=_table("Shop","increasing").get(currency["increasing_i_d"],{}).get("items",())
            if not steps:
                return None
            unit=steps[min(bought-1,len(steps)-1)]["price"]
        # PC OnBuy checks current unit price * selected quantity * discount, rounded to the nearest integer.
        price=Decimal(unit*quantity)*Decimal(str(goods.get("discount",1)))
        charges[currency["currency_i_d"]]+=int(price.to_integral_value(rounding=ROUND_HALF_UP))
    return charges


def balance(player,item):
    raw=db.get_item_detail(player,item)
    return pb.ItemDetail.FromString(raw) if raw else None


def already_owned(player,item,tag):
    if tag in INSTANCE_TAGS:
        return any(pb.ItemDetail.FromString(raw).main_item.item_id==item for raw in db.get_item_detail(player,table="items_s"))
    detail=balance(player,item)
    return bool(detail and (detail.main_item.WhichOneof("item")!="base_item" or detail.main_item.base_item.num>0))


class UnsupportedGoods(Exception):
    pass


def fixed_gift(player,template,level):
    """National shop gift boxes list world/level-specific reward groups. Only exact unconditional grants qualify."""
    world=db.get_players_info(player,"world_level")[0]
    maps=_table("Reward","world_level_pool").get(template,{}).get("world_level_pool_group",())
    group_ids=[r["pool_i_d"] for r in maps if r.get("world_level",0)==world]
    group_ids=group_ids or [r["pool_i_d"] for r in maps if not r.get("world_level")]
    result=Counter()
    for group in group_ids or [template]:
        for row in _table("Reward","reward_pool").get(group,{}).get("reward_pool_group",()):
            source=_table("Reward","reward_item_pool").get(row["reward_pool_group_i_d"],{})
            choices=[a for a in source.get("reward_item_pool_group",()) if a.get("min_level_limit",0)<=level<=a.get("max_level_limit",0xffffffff)]
            if len(choices)!=1:
                return None
            choice=choices[0]
            if choice.get("need_items") or choice.get("item_min_count",1)!=choice.get("item_max_count",1):
                return None
            result[choice["item_i_d"]]+=choice.get("item_min_count",1)
    return result or None


def award(player,amounts,rsp,notice):
    for item,qty in amounts.items():
        tag=_table("Item","item")[item]["new_bag_item_tag"]
        if tag in INSTANCE_TAGS:
            for _ in range(qty):
                instance=make_item(item,1,player)
                kind=instance.main_item.WhichOneof("item") if instance else None
                if kind not in ("weapon","armor","poster"):
                    raise UnsupportedGoods
                db.set_item_detail(player,instance.SerializeToString(),instance_id=getattr(instance.main_item,kind).instance_id)
                rsp.items.add().CopyFrom(instance)
                notice.items.add().CopyFrom(instance)
        else:
            if tag==pb.EBagItemTag_Fashion:
                old=balance(player,item)
                if old and old.main_item.WhichOneof("item")=="outfit":
                    rsp.items.add().CopyFrom(old)
                    continue
            delta=make_item(item,qty,player)
            if not delta or delta.main_item.WhichOneof("item") not in ("base_item","outfit"):
                raise UnsupportedGoods
            rsp.items.add().CopyFrom(delta)
            if delta.main_item.WhichOneof("item")=="base_item":
                old=balance(player,item)
                if old:
                    if old.main_item.WhichOneof("item")!="base_item" or qty+old.main_item.base_item.num>0xffffffff:
                        raise UnsupportedGoods
                    old.main_item.base_item.num+=qty
                    delta=old
            db.set_item_detail(player,delta.SerializeToString(),item)
            notice.items.add().CopyFrom(delta)


def buy(session,req,rsp):
    player=session.player_id
    rsp.shop_id=req.shop_id
    board=_table("Shop","grid").get(req.shop_id,{})
    grid=next((g for g in board.get("items",()) if g["grid_i_d"]==req.grid_id),None)
    instant=now()
    if not grid:
        return pb.StatusCode_CFG_NOT_FOUND
    if grid.get("goods_i_d") or not in_window(_table("Shop","info").get(req.shop_id,{}),instant):
        return pb.StatusCode_MODULE_CLOSE
    level=db.get_players_info(player,"level")[0]
    goods=select_goods(grid,level,instant)
    if not goods or not goods.get("item_i_d"):
        return pb.StatusCode_CFG_NOT_FOUND
    item=goods["item_i_d"]
    if req.item_id and req.item_id!=item:
        return pb.StatusCode_BAD_REQ
    params={r["key"]:r.get("values",[]) for r in res["GlobalConfig"]["config"]["datas"] if r.get("key") in ("ShopBuy_Item_MaxNum","Shop_Dispatched_ItemIDList")}
    quantity=req.buy_times
    if not 1<=quantity<=int(params["ShopBuy_Item_MaxNum"][0]):
        return pb.StatusCode_SHOP_SINGLE_BUY_NUM_LIMIT
    key=counter_key(grid,instant)
    bought=count(player,key)
    limit=grid.get("buy_max_count",-1)
    if limit>=0 and bought+quantity>limit or bought+quantity>0xffffffff:
        return pb.StatusCode_SHOP_GRID_BUY_NUM_LIMIT
    amounts=Counter({item:goods["item_num"]*quantity})
    if goods.get("add_item_i_d") and goods.get("add_item_num"):
        amounts[goods["add_item_i_d"]]+=goods["add_item_num"]*quantity
    catalog=_table("Item","item")
    shipped={int(x) for x in params.get("Shop_Dispatched_ItemIDList",())}
    for template,units in list(amounts.items()):
        if catalog.get(template,{}).get("new_bag_item_tag")==pb.EBagItemTag_Gift:
            contents=fixed_gift(player,template,level)
            if not contents:
                return pb.StatusCode_CFG_NOT_FOUND
            del amounts[template]
            for template_item,per_box in contents.items():
                amounts[template_item]+=units*per_box
    if any(i in shipped or i not in catalog or catalog[i]["new_bag_item_tag"] in UNSUPPORTED for i in amounts):
        return pb.StatusCode_MODULE_CLOSE
    tag=catalog[item]["new_bag_item_tag"]
    if not goods.get("is_repeated_in_bag") or tag==pb.EBagItemTag_Fashion:
        if already_owned(player,item,tag):
            return pb.StatusCode_SHOP_ITEM_ALREADY_IN_BAG
        if quantity>1:
            return pb.StatusCode_SHOP_SINGLE_BUY_NUM_LIMIT
    charges=quote(goods,bought,quantity)
    if charges is None:
        return pb.StatusCode_CFG_NOT_FOUND
    # A finite original limit is required before issuing any free goods.
    if not any(charges.values()) and limit<1:
        return pb.StatusCode_MODULE_CLOSE
    notice=pb.PackNotice(status=pb.StatusCode_OK)
    if 102 in charges:
        stars=balance(player,102)
        available=stars.main_item.base_item.num if stars and stars.main_item.WhichOneof("item")=="base_item" else 0
        missing=max(0,charges[102]-available)
        if missing:
            # The native star-purchase confirmation allows private 108 stones to cover a 102 shortfall.
            charges[108]+=missing
            charges[102]-=missing
    payments={}
    for currency,price in charges.items():
        if price<=0:
            continue
        purse=balance(player,currency)
        if not purse or purse.main_item.WhichOneof("item")!="base_item" or purse.main_item.base_item.num<price:
            return pb.StatusCode_ITEM_NOT_ENOUGH
        purse.main_item.base_item.num-=price
        payments[currency]=purse
    db.conn.execute("SAVEPOINT pc_shop_sale")
    try:
        for currency,purse in payments.items():
            db.set_item_detail(player,purse.SerializeToString(),currency)
            notice.items.add().CopyFrom(purse)
        award(player,amounts,rsp,notice)
        db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'shop_buy',?,?)",(player,key,bought+quantity))
    except UnsupportedGoods:
        db.conn.execute("ROLLBACK TO pc_shop_sale")
        del rsp.items[:]
        return pb.StatusCode_CFG_NOT_FOUND
    except BaseException:
        db.conn.execute("ROLLBACK TO pc_shop_sale")
        raise
    finally:
        db.conn.execute("RELEASE pc_shop_sale")
    grid_info(rsp.grids,req.shop_id,grid,goods,bought+quantity)
    session.send(MsgId.PackNotice,notice,0)
    return pb.StatusCode_OK
