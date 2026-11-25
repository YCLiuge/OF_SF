"""Limited native capsule machines, discounts, completion gifts and per-edition history."""
from collections import Counter
from datetime import datetime
import random

from network.msg_id import MsgId
from utils.res_loader import res
from private_gameplay_transaction import run
from proto import net_pb2 as pb
import private_shop as shop
from utils import db


def setup():
    db.conn.execute("""CREATE TABLE IF NOT EXISTS private_wheel_draws(
        player INTEGER NOT NULL, event INTEGER NOT NULL, sequence INTEGER NOT NULL, slot INTEGER NOT NULL,
        item INTEGER NOT NULL, quantity INTEGER NOT NULL, drawn_at INTEGER NOT NULL, discounted INTEGER NOT NULL,
        PRIMARY KEY(player,event,sequence)) WITHOUT ROWID""")


def config(activity):
    sheet = res.index("ActivityTurntable", "activity_turntable")
    return sheet[activity.get("activity_i_d",activity["i_d"])]


def pool(cfg):
    return res.index("ActivityTurntable","turntable_pool")[cfg["turntable_pool_i_d"]]["turntable_pool_item"]


def state(player, event):
    rows = db.conn.execute("""SELECT slot,count(*),max(discounted) FROM private_wheel_draws
        WHERE player=? AND event=? GROUP BY slot""", (player,event)).fetchall()
    return {idx: count for idx,count,_ in rows}, not any(discount for _,_,discount in rows)


def slots_left(slots, counts):
    return {s["index"]: s["limit_num"] - counts.get(s["index"],0) for s in slots}


def finished(slots, left):
    return not any(left[s["index"]] for s in slots if s.get("is_special"))


def snapshot(message, activity, slots, counts, discount):
    entry = message.activities.add(activity_id=activity["i_d"], single_discount=discount)
    for column,field in (("opening_time","open_time"),("closing_time","close_time")):
        if activity.get(column):
            value = datetime.fromisoformat(activity[column]).replace(tzinfo=shop.CN)
            setattr(entry,field,int(value.timestamp()*1000))
    left = slots_left(slots, counts)
    closed = finished(slots,left)
    for s in slots:
        idx = s["index"]
        cell = entry.items[idx]
        cell.index,cell.item_id,cell.item_num = idx,s["item_i_d"],s["item_num"]
        cell.remain_count = 0 if closed else left[idx]
        cell.is_special = s.get("is_special", False)


def send(session, activities):
    notice = pb.ActivityTurntableDataNotice(status=pb.StatusCode_OK)
    for activity in activities:
        if activity.get("new_activity_type") == 6:
            counts, discount = state(session.player_id, activity["i_d"])
            snapshot(notice,activity,pool(config(activity)),counts,discount)
    if notice.activities:
        session.send(MsgId.ActivityTurntableDataNotice, notice, 0)


def draw(session, req, rsp):
    return run(session, req, rsp, settle)


def settle(session, req, rsp, pending):
    from private_events import current, settings
    activity = res.index("Activity","activity").get(req.activity_id)
    if not activity or activity.get("new_activity_type") != 6:
        return pb.StatusCode_CFG_NOT_FOUND
    if req.draw_times not in (1,10):
        return pb.StatusCode_BAD_REQ
    instant = shop.now()
    activity = current(activity,instant,settings(instant))
    if activity is None:
        return pb.StatusCode_Activity_Close
    cfg = config(activity)
    slots = pool(cfg)
    player, event = session.player_id, req.activity_id
    counts, discount = state(player,event)
    left = slots_left(slots,counts)
    if finished(slots,left):
        return pb.StatusCode_TURNTABLE_NO_REWARD_REMAIN
    discounted = discount and req.draw_times == 1
    if discounted:
        item, price = map(int,res.index("GlobalConfig","config","key")["TurntableFirstOneNeed"]["values"])
    else:
        item, price = cfg["cost_item_i_d"], cfg["one_need_item_num"]
    # The native ten-draw confirmation checks the full ticket price. Any unused tickets
    # on the final batch are returned to the same balance before it is published.
    pending.balances.change(item,-price * req.draw_times)
    gained = Counter()
    log = []
    seq = sum(counts.values())
    now = int(instant.timestamp())
    for step in range(req.draw_times):
        choices = [s for s in slots if left[s["index"]] > 0]
        hit = random.choices(choices,weights=[s["weight"] for s in choices],k=1)[0]
        idx = hit["index"]
        left[idx] -= 1
        counts[idx] = counts.get(idx,0) + 1
        gained[hit["item_i_d"]] += hit["item_num"]
        rsp.reward_indexes.append(idx)
        log.append((player,event,seq+step+1,idx,hit["item_i_d"],hit["item_num"],now,int(discounted)))
        if finished(slots,left):
            for s in slots:
                remaining = left[s["index"]]
                if remaining:
                    gained[s["item_i_d"]] += remaining * s["item_num"]
            break
    if len(log) != req.draw_times:
        pending.balances.change(item,(req.draw_times-len(log)) * price)
    pending.grant(gained,rsp.rewards)
    db.conn.executemany("INSERT INTO private_wheel_draws VALUES(?,?,?,?,?,?,?,?)",log)
    rsp.activity_id = event
    notice = pb.ActivityTurntableDataNotice(status=pb.StatusCode_OK)
    snapshot(notice,activity,slots,counts,discount and not discounted)
    pending.notices.append((MsgId.ActivityTurntableDataNotice,notice))
    return pb.StatusCode_OK


def record(session, req, rsp):
    activity = res.index("Activity","activity").get(req.activity_id)
    if not activity or activity.get("new_activity_type") != 6:
        return pb.StatusCode_CFG_NOT_FOUND
    page = max(req.page,1)
    player, event = session.player_id, req.activity_id
    total, = db.conn.execute("SELECT count(*) FROM private_wheel_draws WHERE player=? AND event=?",
                             (player,event)).fetchone()
    rsp.activity_id, rsp.page, rsp.total_page = event, page, (total+9)//10
    for idx,item,quantity,when in db.conn.execute("""SELECT slot,item,quantity,drawn_at FROM private_wheel_draws
            WHERE player=? AND event=? ORDER BY sequence DESC LIMIT 10 OFFSET ?""", (player,event,(page-1)*10)):
        rsp.records.add(index=idx,item_id=item,item_num=quantity,draw_time=when)
    return pb.StatusCode_OK
