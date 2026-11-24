"""Private activity invitation codes, returning players, task progress and per-period rewards."""
from datetime import datetime, timedelta
import secrets
import time

from network.msg_id import MsgId
from private_activity_rewards import deliver_once
from private_dungeons import _table
from private_social_profile import config
from proto import net_pb2 as pb
from server.scene_data import get_session
from utils import db
import private_shop as shop

INVITE, RECALL, RETURN = 8, 10, 11


def setup():
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS referral_presence(
            player INTEGER PRIMARY KEY,last_seen INTEGER NOT NULL,return_until INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS referral_codes(
            activity INTEGER NOT NULL,owner INTEGER NOT NULL,code TEXT NOT NULL UNIQUE,PRIMARY KEY(activity,owner));
        CREATE TABLE IF NOT EXISTS referral_links(
            activity INTEGER NOT NULL,owner INTEGER NOT NULL,guest INTEGER NOT NULL,prior_orders INTEGER NOT NULL,
            PRIMARY KEY(activity,guest));
        CREATE INDEX IF NOT EXISTS referral_owner ON referral_links(activity,owner);
        CREATE TABLE IF NOT EXISTS referral_login_days(
            activity INTEGER NOT NULL,guest INTEGER NOT NULL,day TEXT NOT NULL,PRIMARY KEY(activity,guest,day));
    """)


def touch(pid,instant,login):
    stamp=int(instant.timestamp())
    row=db.conn.execute("SELECT last_seen,return_until FROM referral_presence WHERE player=?",(pid,)).fetchone()
    if row and row[0] >= stamp:
        return row[1]
    until=row[1] if row else 0
    if login and row and stamp-row[0]>=config("ReturningUserJudgement")*86400:
        until=stamp+config("ReturningDurationTime")*86400
    db.conn.execute("""INSERT INTO referral_presence VALUES(?,?,?) ON CONFLICT(player) DO UPDATE
        SET last_seen=MAX(last_seen,excluded.last_seen),return_until=excluded.return_until""",(pid,stamp,until))
    return until


def is_returning(pid,instant):
    row=db.conn.execute("SELECT return_until FROM referral_presence WHERE player=?",(pid,)).fetchone()
    return bool(row and row[0]>int(instant.timestamp()))


def row_for(aid,instant):
    from private_events import current, settings
    base=_table("Activity","activity").get(aid)
    return current(base,instant,settings(instant)) if base else None


def related(aid,kind):
    # Reruns reuse template 80001/90001. Pair the original calendar rows from the same run.
    rows=_table("Activity","activity")
    base=rows[aid]
    return next((r["i_d"] for r in rows.values() if r.get("new_activity_type")==kind
                 and r.get("opening_time")==base.get("opening_time") and r.get("closing_time")==base.get("closing_time")),0)


def tasks(row):
    template=row["activity_i_d"]
    if row["new_activity_type"]==INVITE:
        group=_table("ActivityInvite","activity_invite")[template]["invitor_quest_group"]
        entries=_table("ActivityInvite","activity_invite_quest")[group]["activity_invite_quest_pool_item"]
        return [(r["achieve_i_d"],r.get("complete_i_d"),r.get("activity_invite_pool_reward_item",())) for r in entries]
    entries=_table("InteractionActivity","recall_activity")[template]["recall_activity_item"]
    return [(r["achieve_condition_i_d"],None,r.get("recall_activity_reward_item",())) for r in entries]


def order_count(pid):
    if not db.conn.execute("SELECT 1 FROM sqlite_schema WHERE name='private_gift_claims'").fetchone():
        return 0
    return db.conn.execute("SELECT count(*) FROM private_gift_claims WHERE player=?",(pid,)).fetchone()[0]


def counts(pid,row):
    aid=row["i_d"]
    kind=row["new_activity_type"]
    if kind==RETURN:
        from private_sandbag import progress
        hits,days=progress(pid,related(aid,12))
        return {183:hits,184:days,82:1}
    total,at_level=db.conn.execute("""SELECT COUNT(*),COALESCE(SUM(p.level>=3),0) FROM referral_links l
        JOIN players p ON p.player_id=l.guest WHERE l.activity=? AND l.owner=?""",(aid,pid)).fetchone()
    if kind==RECALL:
        days=db.conn.execute("""SELECT COUNT(*) FROM referral_login_days d JOIN referral_links l
            USING(activity,guest) WHERE l.activity=? AND l.owner=?""",(aid,pid)).fetchone()[0]
        return {185:total,186:days}
    paid=0
    if db.conn.execute("SELECT 1 FROM sqlite_schema WHERE name='private_gift_claims'").fetchone():
        paid=db.conn.execute("""SELECT COUNT(*) FROM referral_links l WHERE l.activity=? AND l.owner=?
            AND (SELECT COUNT(*) FROM private_gift_claims c WHERE c.player=l.guest)>l.prior_orders""",(aid,pid)).fetchone()[0]
    return {170:total,171:at_level,173:paid}


def code_for(aid,pid):
    saved=db.conn.execute("SELECT code FROM referral_codes WHERE activity=? AND owner=?",(aid,pid)).fetchone()
    if saved:
        return saved[0]
    while True:
        code=secrets.token_hex(5).upper()
        if db.conn.execute("INSERT OR IGNORE INTO referral_codes VALUES(?,?,?)",(aid,pid,code)).rowcount:
            return code


def claimed(pid,aid):
    return {int(k) for k, in db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system=?",
                                             (pid,f"referral_task_{aid}"))}


def day_seen(pid,aid,instant):
    day=(instant-timedelta(hours=config("SystemRefreshTime"))).date().isoformat()
    db.conn.execute("INSERT OR IGNORE INTO referral_login_days VALUES(?,?,?)",(aid,pid,day))


def view(s,row,instant,notify=True):
    pid,aid,kind=s.player_id,row["i_d"],row["new_activity_type"]
    family=related(aid,RECALL) if kind==RETURN else aid
    registered=db.conn.execute("SELECT 1 FROM referral_links WHERE activity=? AND guest=?",(family,pid)).fetchone()
    if kind==RETURN and registered and (notify or getattr(s,"referral_day",None)!=(instant-timedelta(hours=5)).date()):
        day_seen(pid,family,instant)
        s.referral_day=(instant-timedelta(hours=5)).date()
    progress=counts(pid,row)
    awards=claimed(pid,aid)
    key=(aid,tuple(progress.items()),frozenset(awards),bool(registered))
    if not notify and getattr(s,"referral_views",{}).get(aid)==key:
        return
    notice=pb.ActivityInviteNotice(status=pb.StatusCode_OK) if kind==INVITE else pb.ActivityRecallNotice(status=pb.StatusCode_OK)
    entry=notice.info.add()
    entry.base.activity_id,entry.base.activity_type=aid,kind
    for field,attr in (("open_time","opening_time"),("close_time","closing_time")):
        if row.get(attr):
            setattr(entry.base,field,int(datetime.fromisoformat(row[attr]).replace(tzinfo=shop.CN).timestamp()*1000))
    if kind!=RETURN:
        entry.invite_code=code_for(aid,pid)
    entry.been_invited=bool(registered)
    entry.rewarded_tasks.extend(sorted(awards))
    if kind==INVITE:
        all_counts=dict(db.conn.execute("""SELECT activity,COUNT(*) FROM referral_links WHERE owner=? AND activity IN
            (SELECT activity FROM referral_codes WHERE owner=?) GROUP BY activity""",(pid,pid)))
        invite_counts={k:v for k,v in all_counts.items() if _table("Activity","activity")[k]["new_activity_type"]==INVITE}
        entry.invited=progress[170]
        entry.invited_total=sum(invite_counts.values())
        update=pb.ActivityInviteCountUpdateNotice(status=pb.StatusCode_OK,invited_total=entry.invited_total)
        update.invited_count.update(invite_counts)
        s.send(MsgId.ActivityInviteCountUpdateNotice,update,0)
    tracker=pb.AchieveNotice(status=pb.StatusCode_OK)
    for goal,_,_ in tasks(row):
        config_row=_table("Achieve","achieve")[goal]
        tracker.achieves.add(achieve_id=goal,count=min(config_row.get("count_param",1),progress.get(config_row["new_achieve_condition"],0)))
    s.send(MsgId.AchieveNotice,tracker,0)
    s.send(MsgId.ActivityInviteNotice if kind==INVITE else MsgId.ActivityRecallNotice,notice,0)
    s.referral_views[aid]=key


def refresh(s,rows,instant):
    s.referral_views={}
    s.referral_rows=[r for r in rows if r.get("new_activity_type") in (INVITE,RECALL,RETURN)
                     and (r["new_activity_type"]!=RETURN or is_returning(s.player_id,instant))]
    for row in s.referral_rows:
        view(s,row,instant)
    s.referral_next=time.monotonic()+10


def heartbeat(s,instant):
    if getattr(s,"referral_rows",()) and time.monotonic()>=s.referral_next:
        s.referral_next=time.monotonic()+10
        for row in s.referral_rows:
            if row["new_activity_type"]!=RETURN or is_returning(s.player_id,instant):
                view(s,row,instant,False)


def use(s,req,rsp,kind):
    pid=s.player_id
    code=req.code.strip().upper()
    if len(code)!=10 or not code.isascii():
        return pb.StatusCode_INVITE_CODE_NOT_EXIST
    found=db.conn.execute("SELECT activity,owner FROM referral_codes WHERE code=?",(code,)).fetchone()
    if not found or found[1]==pid:
        return pb.StatusCode_INVITE_CODE_NOT_EXIST
    aid,owner=found
    instant=shop.now()
    row=row_for(aid,instant)
    if not row or row["new_activity_type"]!=kind:
        return pb.StatusCode_ACTIVITY_INVITE_NOT_OPEN
    if kind==RECALL:
        paired=row_for(related(aid,RETURN),instant)
        if not paired or not is_returning(pid,instant):
            return pb.StatusCode_ACTIVITY_INVITE_NOT_OPEN
        items=_table("InteractionActivity","homecoming_reward")[paired["activity_i_d"]]["homecoming_reward_item"]
    else:
        items=_table("ActivityInvite","activity_invite")[row["activity_i_d"]]["activity_invite_item"]
    if db.conn.execute("SELECT 1 FROM referral_links WHERE activity=? AND guest=?",(aid,pid)).fetchone():
        return pb.StatusCode_INVITE_CODE_ALREADY_FILLED
    result=deliver_once(s,"referral_bind",aid,{r["item_i_d"]:r["item_num"] for r in items},rsp.rewards)
    if result!=pb.StatusCode_OK:
        return result
    db.conn.execute("INSERT INTO referral_links VALUES(?,?,?,?)",(aid,owner,pid,order_count(pid)))
    if kind==RECALL:
        day_seen(pid,aid,instant)
    for peer in get_session():
        if peer.player_id in (owner,pid):
            for active in getattr(peer,"referral_rows",()):
                if active["i_d"]==aid or (kind==RECALL and active["i_d"]==paired["i_d"]):
                    view(peer,active,instant)
    return pb.StatusCode_OK


def claim(s,req,rsp,kinds):
    rsp.activity_id,rsp.condition_id=req.activity_id,req.condition_id
    instant=shop.now()
    row=row_for(req.activity_id,instant)
    if not row or row.get("new_activity_type") not in kinds:
        return pb.StatusCode_ACTIVITY_INVITE_NOT_OPEN
    pid,aid,goal=s.player_id,req.activity_id,req.condition_id
    if row["new_activity_type"]==RETURN and not is_returning(pid,instant):
        return pb.StatusCode_ACTIVITY_INVITE_NOT_OPEN
    entry=next((t for t in tasks(row) if t[0]==goal),None)
    if not entry or not entry[2]:
        return pb.StatusCode_CFG_NOT_FOUND
    previous=claimed(pid,aid)
    if goal in previous:
        return pb.StatusCode_ACTIVITY_INVITE_TASK_CLAIMED
    spec=_table("Achieve","achieve")[goal]
    if ((entry[1] and entry[1] not in previous) or
            counts(pid,row).get(spec["new_achieve_condition"],0)<spec["count_param"]):
        return pb.StatusCode_ACTIVITY_INVITE_TASK_NOT_COMPLETED
    status=deliver_once(s,f"referral_task_{aid}",goal,{r["item_i_d"]:r["item_num"] for r in entry[2]},rsp.rewards)
    if status==pb.StatusCode_OK:
        view(s,row,instant)
    return status


def invite_use(s,req,rsp):
    return use(s,req,rsp,INVITE)


def recall_use(s,req,rsp):
    return use(s,req,rsp,RECALL)


def invite_claim(s,req,rsp):
    return claim(s,req,rsp,(INVITE,))


def recall_claim(s,req,rsp):
    return claim(s,req,rsp,(RECALL,RETURN))
