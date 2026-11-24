"""Anniversary scratch tickets from virtual purchases. Codes are unique within this realm."""
from datetime import datetime
from secrets import randbelow

from network.msg_id import MsgId
from private_dungeons import _table
from private_gameplay_transaction import Rejected, StackChanges, run
import private_shop as shop
from proto import net_pb2 as pb
from realm_plaza import KOI, koi_deadline
from utils import db


def setup():
    db.conn.execute("""CREATE TABLE IF NOT EXISTS koi_accounts(
        event INTEGER, player INTEGER, paid_cents INTEGER NOT NULL DEFAULT 0, issued INTEGER NOT NULL DEFAULT 0,
        expires INTEGER NOT NULL, settled INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(event,player)) WITHOUT ROWID""")
    db.conn.execute("""CREATE TABLE IF NOT EXISTS koi_codes(
        event INTEGER, player INTEGER, seq INTEGER, code TEXT NOT NULL,
        PRIMARY KEY(event,player,seq), UNIQUE(event,code)) WITHOUT ROWID""")


def spec():
    return _table("ActivityJinli","jinli")[KOI]


def active(instant):
    from private_events import current, settings
    row = _table("Activity","activity")[KOI]
    return current(row,instant,settings(instant))


def timestamp(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=shop.CN).timestamp())


def snapshot(player,row):
    cfg = spec()
    cells = db.conn.execute("SELECT paid_cents,issued FROM koi_accounts WHERE event=? AND player=?",
                            (KOI,player)).fetchone() or (0,0)
    codes = db.conn.execute("SELECT code FROM koi_codes WHERE event=? AND player=? ORDER BY seq",(KOI,player))
    message = pb.ActivityKoiNotice(status=pb.StatusCode_OK)
    entry = message.info.add(total_payment_num=cells[0]//100)
    if cells[1] < cfg["params4"]:
        entry.next_reward_payment_num = (cells[1] // cfg["params3"] + 1)*cfg["params1"]
    entry.base.activity_id,entry.base.activity_type = KOI,pb.EActivityType_ActivityJinli
    entry.base.open_time,entry.base.close_time = [timestamp(row[k])*1000 for k in ("opening_time","closing_time")]
    entry.codes.extend(r[0] for r in codes)
    return message


def remember(player,row):
    db.conn.execute("""INSERT INTO koi_accounts(event,player,expires) VALUES(?,?,?) ON CONFLICT(event,player)
        DO UPDATE SET expires=excluded.expires,settled=0 WHERE expires<>excluded.expires""",
                    (KOI,player,timestamp(row["closing_time"])))


def can_exchange(row, instant):
    return row and instant.strftime("%Y-%m-%d %H:%M:%S") <= koi_deadline(row["opening_time"],row["closing_time"])


def virtual_purchase(player, instant, goods, result, pack, notifications):
    row = active(instant)
    if not can_exchange(row,instant):
        return
    cents = sum(p.get("price",0) for p in goods.get("shop_currency_item",()) if not p.get("currency_i_d"))
    if cents <= 0:
        return
    remember(player,row)
    cfg = spec()
    paid,issued = db.conn.execute("SELECT paid_cents,issued FROM koi_accounts WHERE event=? AND player=?",
                                 (KOI,player)).fetchone()
    paid = min(0xffffffff*100,paid+cents)
    entitled = min(cfg["params4"],paid//(cfg["params1"]*100)*cfg["params3"])
    if entitled > issued:
        shop.award(player,{cfg["params2"]:entitled-issued},result,pack)
    db.conn.execute("UPDATE koi_accounts SET paid_cents=?,issued=? WHERE event=? AND player=?",
                    (paid,entitled,KOI,player))
    notifications.append((MsgId.ActivityKoiNotice,snapshot(player,row)))


def send(session, activities, instant):
    player = session.player_id
    row = next((r for r in activities if r["i_d"]==KOI),None)
    if row:
        remember(player,row)
        session.send(MsgId.ActivityKoiNotice,snapshot(player,row),0)
        return
    expired = db.conn.execute("SELECT 1 FROM koi_accounts WHERE player=? AND event=? AND settled=0 AND expires<?",
                              (player,KOI,int(instant.timestamp()))).fetchone()
    if not expired:
        return
    ticket = spec()["params2"]
    item = shop.balance(player,ticket)
    qty = item.main_item.base_item.num if item else 0
    if qty:
        # National item 6044 promises ten star stones per unused ticket after the event ends.
        changes = StackChanges(player)
        changes.change(ticket,-qty)
        try:
            changes.change(102,10*qty)
        except Rejected:
            return
        pack = pb.PackNotice(status=pb.StatusCode_OK)
        changes.flush(pack)
        session.send(MsgId.PackNotice,pack,0)
    db.conn.execute("UPDATE koi_accounts SET settled=1 WHERE player=? AND event=?",(player,KOI))


def use(session,req,rsp):
    return run(session,req,rsp,issue_codes)


def issue_codes(session,req,rsp,pending):
    if req.activity_id != KOI:
        return pb.StatusCode_CFG_NOT_FOUND
    cfg = spec()
    if not 1 <= req.num <= cfg["params4"]:
        return pb.StatusCode_BAD_REQ
    instant,player = shop.now(),session.player_id
    row = active(instant)
    if not can_exchange(row,instant):
        return pb.StatusCode_Activity_Close
    count, = db.conn.execute("SELECT count(*) FROM koi_codes WHERE event=? AND player=?",(KOI,player)).fetchone()
    if count+req.num > cfg["params4"]:
        return pb.StatusCode_BAD_REQ
    pending.balances.change(cfg["params2"],-req.num)
    remember(player,row)
    for seq in range(count+1,count+req.num+1):
        for _ in range(128):
            code = f"{randbelow(100_000_000):08}"
            created = db.conn.execute("""INSERT INTO koi_codes VALUES(?,?,?,?)
                ON CONFLICT(event,code) DO NOTHING""",(KOI,player,seq,code))
            if created.rowcount:
                rsp.codes.append(code)
                break
        else:
            return pb.StatusCode_FAIL
    rsp.activity_id, rsp.num = KOI,req.num
    pending.notices.append((MsgId.ActivityKoiNotice,snapshot(player,row)))
    return pb.StatusCode_OK
