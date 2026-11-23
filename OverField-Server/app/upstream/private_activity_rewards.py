"""Use the realm's stored login days and achievement counts for native reward claims."""
from collections import Counter
from datetime import datetime, timedelta

from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_dungeons import _table, reward_items
import private_shop as shop
from utils import db


def _claimed(player,system):
    return {k for k, in db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system=?",(player,system))}


def _reward(session,system,key,amounts,response):
    if not amounts or any(item not in _table("Item","item") or num<=0 for item,num in amounts.items()):
        return pb.StatusCode_CFG_NOT_FOUND
    if not db.conn.in_transaction:
        db.conn.execute("BEGIN")
    db.conn.execute("SAVEPOINT activity_reward")
    try:
        if not db.conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,?,?)",
                               (session.player_id,system,str(key))).rowcount:
            return pb.StatusCode_REPEAT_GET_REWARD
        delivery=pb.ShopBuyRsp()
        pack=pb.PackNotice(status=pb.StatusCode_OK)
        for key,num in amounts.items():
            tag=_table("Item","item")[key]["new_bag_item_tag"]
            if tag==pb.EBagItemTag_Card:
                from private_gacha import award
                for _ in range(num):award(session.player_id,key,delivery.items.add(),pack)
            elif tag==pb.EBagItemTag_Fashion and shop.already_owned(session.player_id,key,tag):
                delivery.items.add().CopyFrom(shop.balance(session.player_id,key))
            else:
                shop.award(session.player_id,{key:num},delivery,pack)
    except (shop.UnsupportedGoods,ValueError):
        db.conn.execute("ROLLBACK TO activity_reward")
        return pb.StatusCode_CFG_NOT_FOUND
    finally:
        db.conn.execute("RELEASE activity_reward")
    response.rewards.extend(delivery.items)
    if pack.items:session.send(MsgId.PackNotice,pack,0)
    return pb.StatusCode_OK


def _sign_cfg(aid):
    return _table("ActivitySign","activity_sign").get(aid,{}).get("activity_sign_item",())


def _sign_days(player,activity,max_days,instant):
    aid=activity["i_d"]
    # Use the same 04:00 UTC+8 rollover as the private realm's daily shops.
    day=(instant-timedelta(hours=4)).date().toordinal()
    key=str(aid)
    db.conn.execute("INSERT OR IGNORE INTO private_login_days VALUES (?,?,?,?,?)",
                    (player,aid,day,1,int(instant.timestamp()*1000)))
    db.conn.execute("UPDATE private_login_days SET last_day=?,days=min(days+1,?) WHERE player=? AND activity=? AND last_day<?",
                    (day,max_days,player,aid,day))
    days,first=db.conn.execute("SELECT days,first_ms FROM private_login_days WHERE player=? AND activity=?",(player,aid)).fetchone()
    claimed=db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system='activity_sign' AND key LIKE ?",
                            (player,key+":%"))
    mask=0
    for raw, in claimed:
        suffix=raw.split(":")[-1]
        if suffix.isdigit() and 0<int(suffix)<32:mask|=1<<int(suffix)
    return days,first,mask


def setup():
    import private_events
    private_events.setup()
    db.conn.execute("CREATE TABLE IF NOT EXISTS private_login_days (player INTEGER NOT NULL REFERENCES players(player_id),"
                    "activity INTEGER NOT NULL,last_day INTEGER NOT NULL,days INTEGER NOT NULL,first_ms INTEGER NOT NULL,"
                    "PRIMARY KEY(player,activity)) WITHOUT ROWID")


def login(session):
    import private_events
    private_events.refresh(session, force=True)


def send_signs(session, instant, activities):
    notice=pb.ActivitySignInDataNotice(status=pb.StatusCode_OK)
    for activity in activities:
        if activity.get("new_activity_type")!=1:continue
        days=_sign_cfg(activity.get("activity_i_d",activity["i_d"]))
        if not days:continue
        count,first,mask=_sign_days(session.player_id,activity,max(r["days"] for r in days),instant)
        entry=notice.info.add(activity_id=activity["i_d"],login_day=count,init_time=first//1000,reward_id=mask)
        for column,field in (("opening_time","open_time"),("closing_time","close_time")):
            if activity.get(column):
                ts=datetime.fromisoformat(activity[column]).replace(tzinfo=shop.CN).timestamp()
                setattr(entry,field,int(ts*1000))
    session.send(MsgId.ActivitySignInDataNotice,notice,0)


def sign_in(session,req,rsp):
    import private_events
    instant=shop.now()
    activity=_table("Activity","activity").get(req.activity_id)
    if not activity or activity.get("new_activity_type")!=1:return pb.StatusCode_CFG_NOT_FOUND
    if not private_events.current(activity, instant, private_events.settings()):return pb.StatusCode_Activity_Close
    days=_sign_cfg(activity.get("activity_i_d",req.activity_id))
    entry=next((r for r in days if r["days"]==req.day),None)
    if not entry or not 1<=req.day<32:return pb.StatusCode_CFG_NOT_FOUND
    count,_,mask=_sign_days(session.player_id,activity,max(r["days"] for r in days),instant)
    if req.day>count:return pb.StatusCode_FAIL
    rsp.day=req.day
    amounts=Counter()
    for item in entry.get("activity_sign_reward_item",()):
        amounts[item["item_i_d"]]+=item["item_num"]
    status=_reward(session,"activity_sign",f"{req.activity_id}:{req.day}",amounts,rsp)
    rsp.reward_id=(mask|1<<req.day) if status==pb.StatusCode_OK else mask
    return status


def _conditions(group_id):
    group=_table("AchieveQuest","achieve_quest_group").get(group_id,{})
    return group.get("achieve_quest_group_info",())


def _completed(player,key):
    target=_table("Achieve","achieve").get(key,{}).get("count_param",0)
    raw=db.get_achieve(player,key)
    return target>0 and raw and pb.Achieve.FromString(raw).count>=target


def one_award(session,req,rsp):
    key=req.achieve_id
    row=next((r for group in _table("AchieveQuest","achieve_quest_group").values()
              for r in group.get("achieve_quest_group_info",()) if r.get("achieve_condition_i_d")==key),None)
    if not row:return pb.StatusCode_CFG_NOT_FOUND
    if not _completed(session.player_id,key):return pb.StatusCode_FAIL
    return _reward(session,"achieve_one",key,reward_items(session.player_id,row["reward_i_d"]),rsp)


def group_award(session,req,rsp):
    conditions=_conditions(req.group_id)
    row=next((r for r in _table("AchieveQuest","achieve_quest").values() if r.get("achieve_group_i_d")==req.group_id),None)
    if not conditions or not row:return pb.StatusCode_CFG_NOT_FOUND
    if not all(_completed(session.player_id,c["achieve_condition_i_d"]) for c in conditions):
        return pb.StatusCode_FAIL
    return _reward(session,"achieve_group",req.group_id,reward_items(session.player_id,row["reward_i_d"]),rsp)


def group_list(session,req,rsp):
    rsp.rewarded_group_id_lst.extend(sorted(int(key) for key in _claimed(session.player_id,"achieve_group") if key.isdigit()))
    return pb.StatusCode_OK


def one_group(session,req,rsp):
    group=_conditions(req.group_id)
    if not group:return pb.StatusCode_CFG_NOT_FOUND
    player=session.player_id
    info=rsp.current_group_achieve_info
    info.group_id=req.group_id
    singles=_claimed(player,"achieve_one")
    for row in group:
        key=row["achieve_condition_i_d"]
        raw=db.get_achieve(player,key)
        if raw:info.achieve_lst.add().ParseFromString(raw)
        if str(key) in singles:info.rewarded_achieve_id_lst.append(key)
    rsp.is_reward=str(req.group_id) in _claimed(player,"achieve_group")
    return pb.StatusCode_OK
