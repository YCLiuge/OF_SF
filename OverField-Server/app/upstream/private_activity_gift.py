"""Native login-day gift claims, including migrated immediate-delivery purchase receipts."""
from collections import Counter
from datetime import datetime

from network.msg_id import MsgId
from private_activity_rewards import _sign_days, deliver_once
from private_dungeons import _table
import private_shop as shop
from proto import net_pb2 as pb
from utils import db


def setup():
    from private_gifts import setup as setup_purchases
    setup_purchases()
    db.conn.execute("CREATE INDEX IF NOT EXISTS private_gifts_owner_shop ON private_gift_claims(player,shop)")
    db.conn.execute("""CREATE TABLE IF NOT EXISTS private_activity_gift_modes(
        player INTEGER, gift INTEGER, staged INTEGER NOT NULL, PRIMARY KEY(player,gift)) WITHOUT ROWID""")


def mode(player,gift):
    row = db.conn.execute("SELECT staged FROM private_activity_gift_modes WHERE player=? AND gift=?",
                          (player,gift["i_d"])).fetchone()
    if row:
        return bool(row[0])
    if db.conn.execute("SELECT 1 FROM private_gift_claims WHERE player=? AND shop=? LIMIT 1",
                       (player,gift["shop_i_d"])).fetchone():
        # All six old private gift-shop editions delivered every tier in their successful purchase.
        return False
    return None


def edition(gift):
    return next(a for a in _table("Activity","activity").values() if a.get("new_activity_type")==2
                and a.get("activity_i_d",a["i_d"])==gift["i_d"])


def new_staged(gift):
    from private_events import current, settings
    a=edition(gift)
    now=shop.now()
    overrides=settings(now)
    return bool(overrides.get(a["i_d"],{}).get("staged_gifts") and current(a,now,overrides))


def notice(player,activities,instant):
    result=pb.ActivityGiftDataNotice(status=pb.StatusCode_OK)
    for a in activities:
        if a.get("new_activity_type")!=2:
            continue
        aid=a["i_d"]
        gift=_table("ActivityGift","activity_gift")[a["activity_i_d"]]
        purchased=mode(player,gift)
        info=result.info.add(activity_id=aid,has_buy=purchased is not None)
        for col,field in (("opening_time","open_time"),("closing_time","close_time")):
            if a.get(col):
                setattr(info,field,int(datetime.fromisoformat(a[col]).replace(tzinfo=shop.CN).timestamp()*1000))
        stages=gift["activity_gift_item"]
        rules=_table("Achieve","achieve")
        limit=max(rules[s["achieve_i_d"]]["count_param"] for s in stages)
        count,_,_=_sign_days(player,a,limit,instant)
        existing={key for key, in db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system='activity_gift' AND key LIKE ?",
                                                  (player,f"{aid}:%"))}
        for stage in stages:
            goal=stage["achieve_i_d"]
            result.quest_info_lst.add(achieve_id=goal,count=min(count,rules[goal]["count_param"]))
            if purchased is False or f"{aid}:{goal}" in existing:
                info.rewarded_id_lst.append(goal)
    return result


def send(session,activities,instant):
    req=notice(session.player_id,activities,instant)
    if req.info:
        session.send(MsgId.ActivityGiftDataNotice,req,0)


def claim(session,req,rsp):
    from private_events import current,settings
    now=shop.now()
    a=_table("Activity","activity").get(req.activity_id)
    if not a or a.get("new_activity_type")!=2:
        return pb.StatusCode_CFG_NOT_FOUND
    a=current(a,now,settings(now))
    if a is None:
        return pb.StatusCode_Activity_Close
    gift=_table("ActivityGift","activity_gift")[a["activity_i_d"]]
    stage=next((s for s in gift["activity_gift_item"] if s["achieve_i_d"]==req.achieve_id),None)
    if not stage:
        return pb.StatusCode_BAD_REQ
    owned=mode(session.player_id,gift)
    if owned is None:
        return pb.StatusCode_FAIL
    if not owned:
        return pb.StatusCode_ActivityGift_Rewarded
    view=notice(session.player_id,[a],now)
    if req.achieve_id in view.info[0].rewarded_id_lst:
        return pb.StatusCode_ActivityGift_Rewarded
    count=next(v.count for v in view.quest_info_lst if v.achieve_id==req.achieve_id)
    if count<_table("Achieve","achieve")[req.achieve_id]["count_param"]:
        return pb.StatusCode_FAIL
    amounts=Counter()
    for row in stage["a_activity_gift_reward_item"]:
        amounts[row["item_i_d"]]+=row["item_num"]
    status=deliver_once(session,"activity_gift",f"{req.activity_id}:{req.achieve_id}",amounts,rsp.rewards)
    if status==pb.StatusCode_OK:
        send(session,[a],now)
    return status
