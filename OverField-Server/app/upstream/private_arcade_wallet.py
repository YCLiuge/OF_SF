"""Arcade coin recovery and daily prayer-fragment conversion using national PC tables."""
import time

from network.msg_id import MsgId
from private_dungeons import _table
from private_events import _day
from private_gameplay_transaction import run
from private_shop import balance,now
from private_request import set_after_commit
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item


def config(name):
    return [int(v) for v in _table("GlobalConfig","config","key")[name]["values"]]


def wallet(player, item):
    raw = balance(player,item)
    return raw.main_item.base_item.num if raw else 0


def read_value(player, key):
    row = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='pc_daily_wallet' AND k=?",
                          (player,key)).fetchone()
    return int(row[0]) if row else 0


def write_value(player,key,value):
    db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'pc_daily_wallet',?,?)",(player,key,value))


def day_count(player, instant):
    return read_value(player, "exchange:"+_day(instant))


def snapshot(session,out):
    steps = _table("DailyTask","daily_task_reward")
    used = day_count(session.player_id,now())
    out.today_converted = used
    out.exchange_times_left = max(0,len(steps)-used)


def exchange(session,req,rsp):
    return run(session,req,rsp,convert)


def convert(session,req,rsp,pending):
    day = _day(now())
    key = "exchange:"+day
    pid = session.player_id
    used = read_value(pid,key)
    steps = _table("DailyTask","daily_task_reward")
    if used >= len(steps):
        return pb.StatusCode_DAILY_WISH_TIMES_NOT_ENOUGH
    totals = {}
    available = {}
    gained = 0
    for index in range(used+1,len(steps)+1):
        row = steps[index]
        item,amount = row["cost_item_i_d"],row["cost_item_num"]
        required = totals.get(item,0)+amount
        if item not in available:
            available[item] = wallet(pid,item)
        if required > available[item]:
            break
        totals[item] = required
        gained += 1
        if not req.exchange_all:
            break
    if not gained:
        return pb.StatusCode_ITEM_NOT_ENOUGH
    for item,amount in totals.items():
        pending.balances.change(item,-amount)
    result = pending.balances.change(125,gained)
    reward = rsp.rewards.add().main_item
    reward.CopyFrom(result)
    reward.base_item.num = gained
    write_value(pid,key,used+gained)
    rsp.today_converted = used+gained
    rsp.exchange_times_left = len(steps)-rsp.today_converted
    return pb.StatusCode_OK


def note_coin_write(player, value):
    cap, = config("GameCoinMax")
    amount = pb.ItemDetail.FromString(value).main_item.base_item.num
    if amount < cap:
        db.conn.execute("""INSERT OR IGNORE INTO player_kv VALUES(?,'pc_daily_wallet','last_coin',?)""",
                        (player,int(time.time())))
    else:
        db.conn.execute("DELETE FROM player_kv WHERE player_id=? AND ns='pc_daily_wallet' AND k='last_coin'",(player,))


def recover(session):
    pid = session.player_id
    stamp = int(time.time())
    if stamp < getattr(session,"coin_check_after",0):
        return
    cap, = config("GameCoinMax")
    interval, = config("GameCoinTimeRecover")
    raw = balance(pid,117)
    held = raw.main_item.base_item.num if raw else config("GameCoinIni")[0]
    anchor = read_value(pid,"last_coin") or stamp
    if held < cap:
        units = min(cap-held, max(0, stamp-anchor)//interval)
    else:
        units = 0
    if raw is None or units:
        raw = make_item(117,held+units,pid)
        db.set_item_detail(pid,raw.SerializeToString(),117)
        notice = pb.PackNotice(status=pb.StatusCode_OK)
        notice.items.add().CopyFrom(raw)
        session.send(MsgId.PackNotice,notice,0)
    if held+units >= cap:
        # Do not cap a legacy or operator-granted balance. The timer begins on spending.
        if read_value(pid,"last_coin"):
            db.conn.execute("DELETE FROM player_kv WHERE player_id=? AND ns='pc_daily_wallet' AND k='last_coin'",(pid,))
        return set_after_commit(session,"coin_check_after",stamp+interval)
    anchor += units*interval
    write_value(pid,"last_coin",anchor)
    set_after_commit(session,"coin_check_after",anchor+interval)


def buy(session,req,rsp):
    if not req.buy_count:
        return pb.StatusCode_BAD_REQ
    # A previously capped balance may have been spent since the last login snapshot.
    session.coin_check_after = 0
    recover(session)
    return run(session,req,rsp,purchase)


def purchase(session,req,rsp,pending):
    cap, = config("GameCoinMax")
    item,unit,coins = config("GameCoinBuy")
    pid = session.player_id
    held = wallet(pid,117)
    if held >= cap:
        return pb.StatusCode_FAIL
    count = min(req.buy_count,(cap-held+coins-1)//coins)
    for currency,delta in ((item,-count*unit),(117,min(cap-held,count*coins))):
        result = pending.balances.change(currency,delta)
        rsp.item_details.add().main_item.CopyFrom(result)
    if held+count*coins >= cap:
        db.conn.execute("DELETE FROM player_kv WHERE player_id=? AND ns='pc_daily_wallet' AND k='last_coin'",(pid,))
    return pb.StatusCode_OK
