"""Native paid recovery purchases. The gameplay host commits wallet, stat and daily counter together."""
from private_request import cache_after_commit
from datetime import timedelta
from time import time

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item
from private_dungeons import _table
from private_shop import balance, now, CN


def seconds():
    return int(time())


def level_cap(pid, resource):
    row = db.get_players_info(pid,"level")
    return _table("Level","player_level").get(row[0],{}).get("player_"+resource.lower(),0) if row else 0


def read_vitality(pid):
    row=db.conn.execute("SELECT item_detail_blob FROM items WHERE player_id=? AND item_id=10",(pid,)).fetchone()
    return pb.ItemDetail.FromString(row[0]) if row else None


def clock_value(pid):
    row=db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='recovery_clock' AND k='vitality'",(pid,)).fetchone()
    return int(row[0]) if row else None


def clock_set(pid, tick):
    db.conn.execute("INSERT OR REPLACE INTO player_kv(player_id,ns,k,v) VALUES(?,'recovery_clock','vitality',?)",(pid,tick))


def note_vitality_write(pid, blob):
    updated=pb.ItemDetail.FromString(blob)
    if updated.main_item.WhichOneof("item")!="base_item":
        return
    cap=level_cap(pid,"Vitality")
    if not cap:
        return
    anchor=clock_value(pid)
    if updated.main_item.base_item.num>=cap:
        if anchor!=0:
            clock_set(pid,0)
    elif not anchor:
        # From a full balance the timer starts when gameplay actually spends, not on the next login.
        clock_set(pid,seconds())


def settle_vitality(pid):
    data=read_vitality(pid)
    if not data or data.main_item.WhichOneof("item")!="base_item":
        return
    cap=level_cap(pid,"Vitality")
    if not cap:
        return
    anchor=clock_value(pid)
    current=data.main_item.base_item.num
    if current>=cap:
        if anchor!=0:
            clock_set(pid,0)
        return
    instant=seconds()
    if not anchor:
        # Legacy saves have no trustworthy recovery anchor. Preserve the stored inventory.
        clock_set(pid,instant)
        return
    interval=int(_table("GlobalConfig","config","key")["Player_Recover_Vitality_Second"]["values"][0])
    ticks=(instant-anchor)//interval
    if ticks>0:
        data.main_item.base_item.num=min(cap,current+ticks)
        db.conn.execute("UPDATE items SET item_detail_blob=? WHERE player_id=? AND item_id=10",(data.SerializeToString(),pid))
        clock_set(pid,0 if data.main_item.base_item.num==cap else anchor+ticks*interval)


def daily_count(pid, key, instant):
    hours = int(_table("GlobalConfig","config","key")["SystemRefreshTime"]["values"][0])
    day = (instant.astimezone(CN) - timedelta(hours=hours)).date().isoformat()
    row = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='recovery_buy' AND k=?",(pid,key)).fetchone()
    saved_day, _, count = str(row[0]).partition("/") if row else ("","","")
    return day, int(count) if saved_day == day else 0


def stack(pid, item):
    data = balance(pid,item)
    return data if data is not None else make_item(item,0,pid)


def handle(session, name, raw, packet_id):
    getattr(pb,name+"Req").FromString(raw)
    rsp = getattr(pb,name+"Rsp")()
    buying = name.endswith("Buy")
    vitality = name.startswith("PlayerVitality")
    resource = "Vitality" if vitality else "Energy"
    key = resource.lower()
    item = 10 if vitality else 11
    counter = key+"_buy_num"
    pid = session.player_id
    day,count = daily_count(pid,key,now())
    setattr(rsp,counter,count)
    current = stack(pid,item)
    rsp.status = pb.StatusCode_OK
    if not current or current.main_item.WhichOneof("item") != "base_item":
        rsp.status = pb.StatusCode_CFG_NOT_FOUND
    elif not buying:
        if vitality:
            rsp.items.add().CopyFrom(current)
        else:
            rsp.energy_item.CopyFrom(current)
    else:
        purchase(session,rsp,name,packet_id,resource,item,day,count,current)
    session.send(getattr(MsgId,name+"Rsp"),rsp,packet_id)


def purchase(session, rsp, name, packet_id, resource, item, day, count, current):
    receipts = getattr(session,"recovery_receipts",None)
    receipt = name, packet_id
    if packet_id and receipts is not None and receipt in receipts:
        rsp.ParseFromString(receipts[receipt])
        return
    pid = session.player_id
    vitality = item == 10
    options = _table("GlobalConfig","config","key")["Player_"+resource+"_Buy_Times"]["values"]
    cap = level_cap(pid,resource)
    if cap <= 0 or not options:
        rsp.status = pb.StatusCode_CFG_NOT_FOUND
        return
    if count >= len(options):
        rsp.status = pb.StatusCode_VITALITY_BUY_NUM_LIMIT if vitality else pb.StatusCode_EnergyBuyTimesLimit
        return
    owned = current.main_item.base_item.num
    if owned >= cap:
        rsp.status = pb.StatusCode_VITALITY_FULL if vitality else pb.StatusCode_FAIL
        return
    currency, cost, restore = map(int,options[count].split(":"))
    if currency == item or min(currency,cost,restore)<=0:
        rsp.status = pb.StatusCode_CFG_NOT_FOUND
        return
    wallet = stack(pid,currency)
    if not wallet or wallet.main_item.WhichOneof("item")!="base_item" or wallet.main_item.base_item.num<cost:
        rsp.status = pb.StatusCode_ITEM_NOT_ENOUGH
        return
    wallet.main_item.base_item.num -= cost
    current.main_item.base_item.num = min(cap,owned + restore)
    for changed in (wallet,current):
        db.set_item_detail(pid,changed.SerializeToString(),changed.main_item.item_id)
    key=resource.lower()
    db.conn.execute("INSERT OR REPLACE INTO player_kv(player_id,ns,k,v) VALUES(?,'recovery_buy',?,?)",
                    (pid,key,f"{day}/{count+1}"))
    setattr(rsp,key+"_buy_num",count+1)
    rsp.items.add().CopyFrom(current)
    session.send(MsgId.PackNotice,pb.PackNotice(status=pb.StatusCode_OK,items=[wallet,current]),0)
    if packet_id:
        cache_after_commit(session, "recovery_receipts", receipt, rsp.SerializeToString())
