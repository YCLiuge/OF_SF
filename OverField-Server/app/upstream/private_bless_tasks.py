"""CN daily prayer objectives, native quest rows, manual rewards and next-day mail."""
from collections import Counter
from datetime import timedelta
import json
import random

from network.msg_id import MsgId
from private_dungeons import _table,reward_items
from private_events import _day
from private_gameplay_transaction import run
from private_shop import now
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_Quest

# First authored step of chapter 1 (the prologue has chapter id 10000).
UNLOCK=101011
# These goals depend on separate field-event, part-time, tide or interaction services.
# Never assign a goal that this build cannot verify on the server.
UNWIRED={522007,523011,523012,523013,523014,523015,523018}
_catalog=None


def catalog():
    global _catalog
    definition=_table("Quest","quest")
    if _catalog and _catalog[0] is definition:return _catalog[1]
    groups=_table("Quest","condition_set_group")
    goals=_table("Achieve","achieve")
    found={}
    for qid,row in definition.items():
        if row.get("new_type")!=pb.EQuestType_RandomEventQuest or row.get("reward")!=2000600:continue
        phases=groups[row["condition_set_group_i_d"]]["quest_condition_set"]
        ids=[cid for phase in phases for cid in phase["achieve_condition_i_d"]]
        if len(ids)!=1:continue
        cid=ids[0];rule=goals[cid]
        found[qid]=(cid,rule["new_achieve_condition"],tuple(rule.get("param",())),rule["count_param"])
    _catalog=(definition,found)
    return found


def selected(player):
    row=db.conn.execute("SELECT k,v FROM player_kv WHERE player_id=? AND ns='prayer_daily' LIMIT 1",(player,)).fetchone()
    return (row[0],json.loads(row[1])) if row else ("",[])


def unlocked(player):
    raw=db.get_quest(player,UNLOCK)
    if raw and pb.Quest.FromString(raw).status in (pb.QuestStatus_Completed,pb.QuestStatus_Finish):return True
    raw=db.get_chapter(player,10001)
    return bool(raw and 1000101 in pb.Chapter.FromString(raw).rewarded_story_ids)


def choose(player,day):
    return random.Random(f"OF_SF/prayer/{player}/{day}").sample(sorted(catalog().keys()-UNWIRED),4)


def settle_mail(session,ids,instant,publish):
    from private_wishes import send_mail
    sums=Counter()
    for qid in ids:
        raw=db.get_quest(session.player_id,qid)
        if raw and pb.Quest.FromString(raw).status==pb.QuestStatus_Completed:
            sums.update(reward_items(session.player_id,_table("Quest","quest")[qid]["reward"]))
    if sums:
        send_mail(session.player_id,"祈愿任务奖励","你似乎忘记领取昨天的祈愿奖励，其他探索队的好心人给你寄了过来。",
                  sums.items(),instant,session if publish else None,sender="系统",model_id=20)


def set_reward_phase(quest,status):
    # The PC quest list and its claim button derive readiness from the conditions themselves.
    quest.status=status
    for condition in quest.conditions:
        condition.status=status


def repair_pending(player,ids):
    definitions=catalog()
    for qid in ids:
        if qid not in definitions:continue
        raw=db.get_quest(player,qid)
        if not raw:continue
        quest=pb.Quest.FromString(raw)
        if quest.status!=pb.QuestStatus_Completed or quest.complete_count or len(quest.conditions)!=1:continue
        condition=quest.conditions[0]
        cid,_,_,target=definitions[qid]
        if condition.condition_id==cid and condition.progress==target and condition.status==pb.QuestStatus_Finish:
            set_reward_phase(quest,pb.QuestStatus_Completed)
            db.set_quest(player,qid,quest.SerializeToString())


def ensure(session,*,publish=False,instant=None,repair_on_login=False):
    instant=instant or now();day=_day(instant)
    pid=session.player_id
    before,old=selected(pid)
    if before==day:
        if repair_on_login:repair_pending(pid,old)
        return old
    if not unlocked(pid):return []
    if old:settle_mail(session,old,instant,publish)
    all_ids=tuple(catalog())
    marks=",".join("?" for _ in all_ids)
    db.conn.execute(f"DELETE FROM quests WHERE player_id=? AND quest_id IN ({marks})",(pid,*all_ids))
    for qid in all_ids:session.quests.pop(qid,None)
    ids=choose(pid,day)
    notice=pb.QuestNotice(status=pb.StatusCode_OK)
    from private_first_quest import save
    for qid in ids:
        quest=make_Quest(pid,qid)
        save(session,quest);notice.quests.add().CopyFrom(quest)
    db.conn.execute("DELETE FROM player_kv WHERE player_id=? AND ns='prayer_daily'",(pid,))
    db.conn.execute("INSERT INTO player_kv VALUES(?,'prayer_daily',?,?)",(pid,day,json.dumps(ids)))
    if publish:
        from private_arcade_wallet import snapshot
        summary=pb.PlayerDailyTask()
        snapshot(session,summary)
        announcement=pb.DailyTaskUpdateNotice(status=pb.StatusCode_OK,today_converted=summary.today_converted,
                                             exchange_times_left=summary.exchange_times_left)
        announcement.tasks.update(dict(enumerate(ids,1)))
        session.send(MsgId.QuestNotice,notice,0)
        session.send(MsgId.DailyTaskUpdateNotice,announcement,0)
    return ids


def fill_tasks(session,out):
    out.tasks.clear()
    out.tasks.update(dict(enumerate(ensure(session),1)))


def tick(session):
    instant=now()
    if instant.timestamp()<getattr(session,"prayer_next_check",0):return
    ensure(session,publish=True,instant=instant)
    tomorrow=(instant-timedelta(hours=5)+timedelta(days=1)).replace(hour=5,minute=0,second=0,microsecond=0)
    from private_request import set_after_commit
    set_after_commit(session,"prayer_next_check",tomorrow.timestamp())


def record(session,kind,*,amount=1,param=None,only=None):
    if amount<=0 or not getattr(session,"logged_in",False):return
    # Rotation also handles a native action whose packet arrives before the first ping after 05:00.
    ids=ensure(session,publish=True)
    from private_first_quest import saved,save
    changed=pb.QuestNotice(status=pb.StatusCode_OK)
    for qid in ids:
        cid,event,params,target=catalog()[qid]
        if event!=kind or only is not None and qid!=only or params!=(() if param is None else (param,)):continue
        quest=saved(session.player_id,qid)
        if not quest or quest.status!=pb.QuestStatus_InProgress:continue
        cond=quest.conditions[0];cond.progress=min(target,cond.progress+amount)
        if cond.progress>=target:
            set_reward_phase(quest,pb.QuestStatus_Completed)
        save(session,quest);changed.quests.add().CopyFrom(quest)
    if changed.quests:session.send(MsgId.QuestNotice,changed,0)


def wallet_write(pid,item,raw):
    from server.scene_data import online_session
    session=online_session(pid)
    if not session or not getattr(session,"logged_in",False):return
    previous=db.get_item_detail(pid,item)
    before=pb.ItemDetail.FromString(previous).main_item.base_item.num if previous else 0
    after=pb.ItemDetail.FromString(raw).main_item.base_item.num
    if item in (10,11,101) and before>after:record(session,{10:80,11:81,101:85}[item],amount=before-after)
    elif item in (101,124) and after>before:record(session,2,param=item,amount=after-before)


def claim(session,req,rsp):
    if req.quest_type!=pb.EQuestType_RandomEventQuest or req.quest_id not in catalog():return pb.StatusCode_BAD_REQ
    if req.quest_id not in ensure(session,publish=True):return pb.StatusCode_Quest_Not_Finish
    return run(session,req,rsp,claim_one)


def claim_one(session,req,rsp,change):
    from private_first_quest import saved,save
    current=saved(session.player_id,req.quest_id)
    if current is None:return pb.StatusCode_Quest_Not_Finish
    if current.status==pb.QuestStatus_Finish:return pb.StatusCode_REPEAT_GET_REWARD
    if current.status!=pb.QuestStatus_Completed:return pb.StatusCode_Quest_Not_Finish
    goods=reward_items(session.player_id,_table("Quest","quest")[req.quest_id]["reward"])
    if not goods:return pb.StatusCode_CFG_NOT_FOUND
    change.grant(goods,rsp.items)
    set_reward_phase(current,pb.QuestStatus_Finish)
    current.complete_count=1
    save(session,current)
    rsp.quest.CopyFrom(current)
    notice=pb.QuestNotice(status=pb.StatusCode_OK)
    notice.quests.add().CopyFrom(current)
    change.notices.append((MsgId.QuestNotice,notice))
    return pb.StatusCode_OK
