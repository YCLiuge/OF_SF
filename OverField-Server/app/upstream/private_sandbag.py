"""Durable daily and lifetime sandbag drops, personal receipts and cross-channel realm totals."""
from collections import Counter
from datetime import timedelta
import random

from network.msg_id import MsgId
from private_dungeons import _table
from private_gameplay_transaction import run
import private_shop as shop
from proto import net_pb2 as pb
from utils import db


def setup():
    for statement in (
        """CREATE TABLE IF NOT EXISTS sandbag_days(player INTEGER, event INTEGER, day INTEGER, hits INTEGER NOT NULL,
           PRIMARY KEY(player,event,day)) WITHOUT ROWID""",
        """CREATE TABLE IF NOT EXISTS sandbag_totals(event INTEGER PRIMARY KEY,hits INTEGER NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS sandbag_boxes(player INTEGER,event INTEGER,seq INTEGER,day INTEGER,slot INTEGER,
           pool INTEGER,item INTEGER,qty INTEGER,created INTEGER,used INTEGER DEFAULT 0,
           PRIMARY KEY(player,event,seq)) WITHOUT ROWID""",
        """CREATE INDEX IF NOT EXISTS sandbag_box_counts ON sandbag_boxes(player,event,slot,day)""",
    ):
        db.conn.execute(statement)


def progress(player, public_sandbag_activity_id):
    return db.conn.execute("""SELECT COALESCE(sum(hits),0),count(*) FROM sandbag_days
        WHERE player=? AND event=?""",(player,public_sandbag_activity_id)).fetchone()


def total(event):
    row = db.conn.execute("SELECT hits FROM sandbag_totals WHERE event=?",(event,)).fetchone()
    return row[0] if row else 0


def config(event):
    from private_events import current, settings
    activity = _table("Activity","activity").get(event)
    if not activity or activity.get("new_activity_type") != 12:
        return None,pb.StatusCode_CFG_NOT_FOUND
    now = shop.now()
    if current(activity,now,settings(now)) is None:
        return None,pb.StatusCode_Activity_Close
    return _table("InteractionActivity","activity_interaction")[activity["activity_i_d"]],pb.StatusCode_OK


def box(target, values):
    target.seq,target.item_id,target.num,target.create_time,target.pool_id = values[:5]
    target.use_status = bool(values[5])


def snapshot(notice,player,event):
    entry = notice.info.add(activity_id=event,server_progress=total(event))
    claimed = db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system='sandbag_milestone' AND key LIKE ?",
                              (player,str(event)+":%"))
    entry.server_reward.extend(sorted(int(k.split(":")[1]) for k, in claimed))
    day = (shop.now()-timedelta(hours=5)).date().toordinal()
    for values in db.conn.execute("""SELECT seq,item,qty,created,pool,used FROM sandbag_boxes
            WHERE player=? AND event=? AND (used=0 OR day=?) ORDER BY seq""",(player,event,day)):
        box(entry.box_list.add(),values)


def send(session,activities):
    notice = pb.ActivitySandbagNotice(status=pb.StatusCode_OK)
    for row in activities:
        if row.get("new_activity_type")==12:
            snapshot(notice,session.player_id,row["i_d"])
    session.sandbag_meter = {i.activity_id:i.server_progress for i in notice.info}
    if notice.info:
        session.send(MsgId.ActivitySandbagNotice,notice,0)


def heartbeat(session):
    seen = getattr(session,"sandbag_meter",None)
    if not seen:
        return
    notice = pb.ActivitySandbagNotice(status=pb.StatusCode_OK)
    for event,previous in tuple(seen.items()):
        if total(event) != previous:
            snapshot(notice,session.player_id,event)
            seen[event]=notice.info[-1].server_progress
    if notice.info:
        session.send(MsgId.ActivitySandbagNotice,notice,0)


def choose_reward(pool,level):
    groups = _table("Reward","reward_pool")[pool]["reward_pool_group"]
    if len(groups)!=1:
        raise ValueError("Expected one authored sandbag drop group")
    entries = _table("Reward","reward_item_pool")[groups[0]["reward_pool_group_i_d"]]["reward_item_pool_group"]
    eligible = [r for r in entries if r.get("min_level_limit",0)<=level<=r.get("max_level_limit",0xffffffff)]
    row = random.choice(eligible)
    return row["item_i_d"], random.randint(row["item_min_count"],row["item_max_count"])


def hit(session,req,rsp):
    return run(session,req,rsp,hit_once)


def hit_once(session,req,rsp,pending):
    event,player = req.activity_id,session.player_id
    cfg,status = config(event)
    if not cfg:
        return status
    if (session.scene_id!=9999 or session.dungeon[0] or req.npc_id!=cfg["object_i_d"]
            or not 1<=req.hit_count<=100):
        return pb.StatusCode_BAD_REQ
    now=shop.now()
    day=(now-timedelta(hours=5)).date().toordinal()
    db.conn.execute("""INSERT INTO sandbag_days VALUES(?,?,?,?) ON CONFLICT(player,event,day)
        DO UPDATE SET hits=hits+excluded.hits""",(player,event,day,req.hit_count))
    db.conn.execute("""INSERT INTO sandbag_totals VALUES(?,?) ON CONFLICT(event)
        DO UPDATE SET hits=hits+excluded.hits""",(event,req.hit_count))
    used={slot:(daily,lifetime) for slot,daily,lifetime in db.conn.execute("""SELECT slot,sum(day=?),count(*)
        FROM sandbag_boxes WHERE player=? AND event=? GROUP BY slot""",(day,player,event))}
    remaining={r["index"]: r["output_ceiling"]-used.get(r["index"],(0,0))[0 if r.get("reset_type")==1 else 1]
               for r in cfg["activity_interaction_item"]}
    seq, = db.conn.execute("SELECT COALESCE(max(seq),0) FROM sandbag_boxes WHERE player=? AND event=?",(player,event)).fetchone()
    level,=db.get_players_info(player,"level")
    drops=pb.ActivitySandbagDropNotice(status=pb.StatusCode_OK,activity_id=event)
    insert=[]
    for _ in range(req.hit_count):
        available=[r for r in cfg["activity_interaction_item"] if remaining[r["index"]]>0]
        if not available:
            break
        choice=random.choice(available)
        slot,key=choice["index"],choice["reward_pool_group_i_d"]
        remaining[slot]-=1
        item,qty=choose_reward(key,level)
        seq+=1
        created=int(now.timestamp())
        insert.append((player,event,seq,day,slot,key,item,qty,created))
        box(drops.box_list.add(),(seq,item,qty,created,key,0))
    db.conn.executemany("INSERT INTO sandbag_boxes(player,event,seq,day,slot,pool,item,qty,created) VALUES(?,?,?,?,?,?,?,?,?)",insert)
    rsp.activity_id,rsp.npc_id,rsp.hit_count=event,req.npc_id,req.hit_count
    if insert:
        pending.notices.append((MsgId.ActivitySandbagDropNotice,drops))
    meter=pb.ActivitySandbagNotice(status=pb.StatusCode_OK)
    snapshot(meter,player,event)
    pending.notices.append((MsgId.ActivitySandbagNotice,meter))
    return pb.StatusCode_OK


def claim(session,req,rsp):
    return run(session,req,rsp,claim_once)


def claim_once(session,req,rsp,pending):
    event,player=req.activity_id,session.player_id
    cfg,status=config(event)
    if not cfg:
        return status
    if not req.seq_list or len(req.seq_list)!=len(set(req.seq_list)):
        return pb.StatusCode_BAD_REQ
    gains=Counter()
    if req.reward_type==pb.RewardType_Server:
        tiers=_table("InteractionActivity","full_server_activity")[cfg["i_d"]]["full_server_activity_item"]
        by_id={r["index"]:r for r in tiers}
        count=total(event)
        for idx in req.seq_list:
            tier=by_id.get(idx)
            if not tier:
                return pb.StatusCode_BAD_REQ
            if count<tier.get("completion_count",0):
                return pb.StatusCode_FAIL
            if not db.conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,'sandbag_milestone',?)",
                                   (player,f"{event}:{idx}")).rowcount:
                return pb.StatusCode_REPEAT_GET_REWARD
            for row in tier["full_server_activity_reward_item"]:
                gains[row["item_i_d"]]+=row["item_num"]
    elif req.reward_type==pb.RewardType_Box:
        for seq in req.seq_list:
            row=db.conn.execute("SELECT item,qty,used FROM sandbag_boxes WHERE player=? AND event=? AND seq=?",
                                (player,event,seq)).fetchone()
            if not row:
                return pb.StatusCode_BAD_REQ
            if row[2]:
                return pb.StatusCode_REPEAT_GET_REWARD
            gains[row[0]]+=row[1]
            db.conn.execute("UPDATE sandbag_boxes SET used=1 WHERE player=? AND event=? AND seq=?",(player,event,seq))
    else:
        return pb.StatusCode_BAD_REQ
    pending.grant(gains,rsp.rewards)
    rsp.activity_id=event
    rsp.seq_list.extend(req.seq_list)
    meter=pb.ActivitySandbagNotice(status=pb.StatusCode_OK)
    snapshot(meter,player,event)
    pending.notices.append((MsgId.ActivitySandbagNotice,meter))
    return pb.StatusCode_OK
