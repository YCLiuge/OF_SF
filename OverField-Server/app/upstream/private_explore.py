"""Private investigation dispatches, completion and collection for the national PC tables."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import time

from network.msg_id import MsgId
from private_dungeons import grant
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res

# Item quantities follow the public of-ps simulation; the national table lists the available item IDs.
AREA_REWARD_COUNT=10
COLLECTION_REWARD_COUNT=5
CN_TIME=timezone(timedelta(hours=8))

def catalog(activity):
    table="explore_activity" if activity else "explore_area"
    return {r["i_d"]:r for r in res["Explore"][table]["datas"]}

def active(config,now):
    return all((not value or check(datetime.fromisoformat(value).replace(tzinfo=CN_TIME).timestamp()*1000))
               for value,check in ((config.get("start_time"),lambda start:now>=start),
                                   (config.get("end_time"),lambda end:now<=end)))

def schema():
    # Individual DDL statements preserve the native handler's surrounding transaction.
    db.conn.execute("""CREATE TABLE IF NOT EXISTS private_explorations (
        player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
        activity INTEGER NOT NULL, area INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
        started_ms INTEGER NOT NULL DEFAULT 0, quick INTEGER NOT NULL DEFAULT 0,
        collection_claimed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(player_id,activity,area))""")
    db.conn.execute("""CREATE TABLE IF NOT EXISTS private_exploration_finds (
        player_id INTEGER NOT NULL, activity INTEGER NOT NULL, area INTEGER NOT NULL, item INTEGER NOT NULL,
        PRIMARY KEY(player_id,activity,area,item), FOREIGN KEY(player_id,activity,area)
        REFERENCES private_explorations(player_id,activity,area) ON DELETE CASCADE)""")

def state(key):
    return db.conn.execute("""SELECT completed,started_ms,quick,collection_claimed FROM private_explorations
        WHERE player_id=? AND activity=? AND area=?""",key).fetchone() or (0,0,0,0)

def info(message,key):
    message.explore_id=key[2]
    message.times,message.start_time,quick,claimed=state(key)
    message.is_quick_finish=bool(quick)
    message.collect_reward=bool(claimed)
    message.collect_item_id.extend(row[0] for row in db.conn.execute("""SELECT item FROM private_exploration_finds
        WHERE player_id=? AND activity=? AND area=? ORDER BY item""",key))

def current_limit():
    rows=res["GlobalConfig"]["config"]["datas"]
    return int(next(row["values"][0] for row in rows if row["key"]=="Explore_ProcessIng_MaxCount"))

def items_for(config,activity):
    group="explore_activity_item" if activity else "explore_area_item"
    return Counter(x["rewar_item_i_d"] for x in config.get(group,()) if x.get("rewar_item_i_d"))

def inventory(pid,item):
    blob=db.get_item_detail(pid,item)
    if not blob:return None
    detail=pb.ItemDetail.FromString(blob)
    return detail if detail.main_item.WhichOneof("item")=="base_item" else None

def grant_private_permits(pid):
    # The private realm pre-issues the two permanent map passes. Story quest state is independent.
    from utils.pb_create import make_item
    permits={row["need_item"]:row.get("need_item_count",1) for row in catalog(False).values() if row.get("need_item")}
    for item,count in permits.items():
        detail=inventory(pid,item)
        if detail is not None and detail.main_item.base_item.num>=count:
            continue
        if detail is None:detail=make_item(item,0,pid)
        if detail.main_item.WhichOneof("item")!="base_item":
            raise ValueError("Unexpected private investigation map permit type")
        detail.main_item.base_item.num=count
        db.set_item_detail(pid,detail.SerializeToString(),item)

def begin(session,config,key,now):
    pid,activity,area=key
    completed,started,_,_=state(key)
    if started:return pb.StatusCode_EXPLORE_IN_PROGRESS
    if activity and not active(config,now):return pb.StatusCode_MODULE_CLOSE
    if activity and config.get("max_times",0) and completed>=config["max_times"]:
        return pb.StatusCode_EXPLORE_MAX_TIMES
    if not activity:
        level=db.get_players_info(pid,"level")[0]
        if level<config.get("need_player_level",0):return pb.StatusCode_PLAYER_LEVEL_LIMIT
        # Area 1 is the starting private region. Higher areas use permanent unlock items.
        ent=config.get("need_item")
        if ent:
            allow=inventory(pid,ent)
            if not allow or allow.main_item.base_item.num<config.get("need_item_count",1):
                return pb.StatusCode_ITEM_NOT_ENOUGH
    count=db.conn.execute("SELECT count(*) FROM private_explorations WHERE player_id=? AND started_ms>0",(pid,)).fetchone()[0]
    if count>=current_limit():return pb.StatusCode_EXPLORE_NUM_LIMIT
    db.conn.execute("""INSERT INTO private_explorations(player_id,activity,area,started_ms) VALUES(?,?,?,?)
        ON CONFLICT(player_id,activity,area) DO UPDATE SET started_ms=excluded.started_ms,quick=0""",(*key,now))
    return pb.StatusCode_OK

def quick_finish(session,config,key,now):
    _,start,quick,_=state(key)
    if not start:return pb.StatusCode_EXPLORE_NOT_EXISTS
    if quick or now>=start+config["need_time"]*1000:return pb.StatusCode_REPEAT_GET_REWARD
    item=config["quick_need_item"]
    price=config["quick_need_item_count"]
    payment=inventory(session.player_id,item)
    if not payment or payment.main_item.base_item.num<price:return pb.StatusCode_ITEM_NOT_ENOUGH
    payment.main_item.base_item.num-=price
    db.set_item_detail(session.player_id,payment.SerializeToString(),item)
    db.conn.execute("UPDATE private_explorations SET quick=1 WHERE player_id=? AND activity=? AND area=?",key)
    change=pb.PackNotice(status=pb.StatusCode_OK,items=[payment])
    session.send(MsgId.PackNotice,change,0)
    return pb.StatusCode_OK

def cancel(config,key,now):
    _,start,quick,_=state(key)
    if not start:return pb.StatusCode_EXPLORE_NOT_EXISTS
    if quick or now>=start+config["need_time"]*1000:return pb.StatusCode_BAD_REQ
    db.conn.execute("UPDATE private_explorations SET started_ms=0,quick=0 WHERE player_id=? AND activity=? AND area=?",key)
    return pb.StatusCode_OK

def payout(session,rsp,config,key,now):
    _,start,quick,_=state(key)
    if not start:return pb.StatusCode_EXPLORE_NOT_EXISTS
    if not quick and now<start+config["need_time"]*1000:return pb.StatusCode_EXPLORE_NOT_FINISH
    entries=items_for(config,key[1])
    if key[1]:
        if special:=config.get("special_reward_item_i_d"):entries[special]+=1
    else:
        entries={item:mult*AREA_REWARD_COUNT for item,mult in entries.items()}
    if not entries:return pb.StatusCode_CFG_NOT_FOUND
    grant(session,entries,rsp.reward_item)
    db.conn.executemany("""INSERT OR IGNORE INTO private_exploration_finds(player_id,activity,area,item)
        VALUES(?,?,?,?)""",((*key,item) for item in entries))
    db.conn.execute("""UPDATE private_explorations SET started_ms=0,quick=0,completed=completed+1
        WHERE player_id=? AND activity=? AND area=?""",key)
    if not key[1]:
        from private_intervals import create_offer
        rsp.interval_id=create_offer(session,key[2])
    return pb.StatusCode_OK

def collect(session,rsp,config,key):
    completed,_,_,claimed=state(key)
    if claimed:return pb.StatusCode_REPEAT_GET_REWARD
    if key[1]:return pb.StatusCode_BAD_REQ
    all_items=items_for(config,False)
    found={r[0] for r in db.conn.execute("""SELECT item FROM private_exploration_finds
         WHERE player_id=? AND activity=? AND area=?""",key)}
    if not completed or not all_items or not set(all_items).issubset(found):
        return pb.StatusCode_EXPLORE_COLLECT_NOT_FINISH
    grant(session,{item:COLLECTION_REWARD_COUNT for item in all_items},rsp.reward_item)
    db.conn.execute("UPDATE private_explorations SET collection_claimed=1 WHERE player_id=? AND activity=? AND area=?",key)
    return pb.StatusCode_OK

def handle(session,req,rsp):
    schema()
    pid=session.player_id
    now=time.time_ns()//1_000_000
    if isinstance(req,pb.ExploreInitReq):
        for activity,field in ((False,rsp.explore),(True,rsp.activity_explore)):
            for area,config in catalog(activity).items():
                key=(pid,int(activity),area)
                if not activity or active(config,now) or state(key)[1]:
                    info(field.add(),key)
        return pb.StatusCode_OK
    area=req.explore_id
    activity=req.is_activity
    rsp.is_activity=activity
    if "explore_id" in rsp.DESCRIPTOR.fields_by_name:rsp.explore_id=area
    config=catalog(activity).get(area)
    if not config:return pb.StatusCode_CFG_NOT_FOUND
    key=(pid,int(activity),area)
    if isinstance(req,pb.ExploreReq):status=begin(session,config,key,now)
    elif isinstance(req,pb.ExploreCancelReq):status=cancel(config,key,now)
    elif isinstance(req,pb.ExploreQuickFinishReq):status=quick_finish(session,config,key,now)
    elif isinstance(req,pb.ExploreRewardReq):status=payout(session,rsp,config,key,now)
    else:status=collect(session,rsp,config,key)
    info(rsp.explore,key)
    return status
