"""Native rod, bait, fishing previews, and transactional catch settlement on the normal realm heartbeat."""
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
import random
from time import monotonic

from network.msg_id import MsgId
from private_crafts import base,daily_proficiency,MAX_COUNT
from private_dungeons import _table,progress
from private_gameplay_transaction import run
from private_request import set_after_commit
import private_shop as shop
from proto import net_pb2 as pb
from server import notice_sync
from utils import db

KIND=pb.LIFE_TYPE_Fishing
BAIT_LEVELS={5021001:1,5021002:3,5021003:5,5021004:7,5021005:7,6021:1}


@dataclass
class Cast:
    place: tuple
    bait: int
    rod: int
    instance: int
    daylight: bool
    energy: tuple
    due: float
    preview: object


def place(session):
    return (session.scene_id,session.channel_id,session.dungeon[0],getattr(session,"mp_room_team",0))


def day_at(scene):
    path=_table("Scene","scene")[scene].get("scene_name","").lower()
    if "白天" in path or "_day" in path and scene!=9999:
        return True
    if "夜晚" in path or "_night" in path:
        return False
    return 6*3600<=notice_sync.tod_time<18*3600


def seasonal_bait():
    # Both national editions sell the special bait and exchange the three seasonal catches.
    rows=_table("Shop","info")
    return any(shop.in_window(rows[i],shop.now()) for i in (19200,20400))


def rod(player,requested,scene_team):
    table=_table("Weapon","weapon")
    def real(detail):
        if detail and detail.main_item.WhichOneof("item")=="weapon":
            weapon=detail.main_item.weapon
            if table.get(weapon.weapon_id,{}).get("new_weapon_type")==18:
                return weapon.weapon_id,weapon.instance_id,weapon.durability
        return None
    raw=db.get_item_detail(player,instance_id=requested)
    if raw:
        return real(pb.ItemDetail.FromString(raw))
    if table.get(requested,{}).get("new_weapon_type")!=18:
        return None
    # The original city loadout can store a starter tool template directly on the actor.
    # Resolve a real inventory instance first when both representations are present.
    exhausted=None
    for raw in db.get_item_detail(player,table="items_s"):
        owned=real(pb.ItemDetail.FromString(raw))
        if owned and owned[0]==requested:
            if owned[2]>0:
                return owned
            exhausted=owned
    if exhausted:
        return exhausted
    if any(a.char_id and a.character_appearance.fishing_rod_instance_id==requested for a in
           (scene_team.char1,scene_team.char2,scene_team.char3)):
        lost=db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='fishing_rod_wear' AND k=?",
                             (player,str(requested))).fetchone()
        return requested,0,max(0,table[requested]["durability"]-(int(lost[0]) if lost else 0))
    return None


def skill_values(session):
    from private_life_skills import refresh,tiers
    refresh(session)
    result={}
    for skill,level in db.conn.execute("SELECT skill,level FROM realm_life_study WHERE player=? AND kind=? AND level>0",
                                       (session.player_id,KIND)):
        levels=tiers(KIND,skill)
        if level<=len(levels):
            result[skill]=levels[level-1].get("param1",0)
    return result


def plan(level,bait,day,skills):
    # National tables contain phase positions and effects, but omit their RNG weights.
    # Select an entry for each casting phase, in its original on-screen order.
    events=_table("Fishing","fishing_event")
    chosen=[(100,random.choice(events[100]["fishing_event_items"])),(200,random.choice(events[200]["fishing_event_items"]))]
    middle=[n for n in (204,210,220,230,240,250,260)
            if (n!=204 or day) and (n not in (210,220,230) or int(events[n]["fishing_event_items"][0]["param1"]) in skills)]
    if middle and random.random()<0.5:
        n=random.choice(middle)
        chosen.append((n,events[n]["fishing_event_items"][0]))
    chosen.append((300,random.choice(events[300]["fishing_event_items"])))
    result=pb.FishingEventResult(cost_time=max(1,round(random.randint(3,20)*(1-skills.get(30300,0)))))
    chance=0.5+skills.get(30100,0)
    extra=0
    classes=Counter()
    sizes=Counter()
    for event,part in chosen:
        result.fishing_event_list.add(i_d=event,index=part["index"])
        kind,value=part.get("new_fish_event_type",0),part["param1"]
        if kind==0:
            result.cost_endurance+=max(0,-int(value))
        elif kind==1:
            classes[int(value)]+=part.get("param2",0)
        elif kind==2:
            chance+=value
        elif kind==3:
            extra+=int(value)
        elif kind==4:
            sizes[(int(value)-30400)//100+1]+=skills.get(int(value),0)
    if random.random()>=max(0,min(1,chance)):
        return result
    quality=_table("Item","item")[bait].get("quality",0)
    limited=bait==6021 and seasonal_bait()
    candidates=[r for r in _table("Fishing","fishing_info").values()
                if (r.get("is_show") or limited and r["i_d"] in (6022,6023,6024))
                and r.get("need_level",1)<=level and r.get("new_day_type",0) in (2,int(not day))]
    weights=[(1+classes[r.get("new_fish_classify_type",0)]+sizes[r.get("new_size_type",0)])*
             (1+quality*(r.get("need_level",1)-1)/10) for r in candidates]
    quantity=1+extra+int(random.random()<skills.get(30200,0))
    for fish in random.choices(candidates,weights=weights,k=quantity):
        result.fish_infos.add(i_d=fish["i_d"],length=random.uniform(fish["min_size"],fish["max_size"]))
    return result


def request(session,req,rsp):
    rsp.is_cancel=req.is_cancel
    if req.is_cancel:
        set_after_commit(session,"fishing_cast",None)
        return pb.StatusCode_OK
    old=getattr(session,"fishing_cast",None)
    if old and old.place==place(session):
        rsp.fishing_event_result.CopyFrom(old.preview)
        return pb.StatusCode_OK
    if (session.dungeon[0] or getattr(session,"mp_room_team",0)
            or session.scene_id not in _table("ScenesConfigAsset","Scenes","ID")
            or req.fishing_pond_id not in _table("FishPond","fish_pond")):
        return pb.StatusCode_BAD_REQ
    pid,bait=session.player_id,req.fishing_box_id
    life=base(pid,KIND)
    if bait not in BAIT_LEVELS:
        return pb.StatusCode_CFG_NOT_FOUND
    if not life or life.level<BAIT_LEVELS[bait]:
        return pb.StatusCode_LifeLvNotEnough
    tool=rod(pid,req.fishing_rod_id,session.scene_player.team) if req.fishing_rod_id else None
    if not tool or not tool[2]:
        return pb.StatusCode_ITEM_NOT_ENOUGH
    tier=_table("Fishing","fishing_level")[life.level]
    energy=tier["need_item_i_d"],tier["need_item_count"]
    for item,qty,status in ((energy[0],energy[1],pb.StatusCode_EnergyNotEnough),(bait,1,pb.StatusCode_ITEM_NOT_ENOUGH)):
        stock=shop.balance(pid,item)
        if not stock or stock.main_item.base_item.num<qty:
            return status
    day=day_at(session.scene_id)
    preview=plan(life.level,bait,day,skill_values(session))
    preview.cost_endurance=min(tool[2],preview.cost_endurance)
    rsp.fishing_event_result.CopyFrom(preview)
    cast=Cast(place(session),bait,*tool[:2],day,energy,monotonic()+preview.cost_time,preview)
    set_after_commit(session,"fishing_cast",cast)
    return pb.StatusCode_OK


@lru_cache(maxsize=1)
def published_goals():
    goals=_table("Achieve","achieve")
    return {entry["achieve_condition_i_d"] for group in _table("AchieveQuest","achieve_quest_group").values()
            for entry in group.get("achieve_quest_group_info",())
            if goals.get(entry["achieve_condition_i_d"],{}).get("new_achieve_condition") in (26,27,28,30,31)}


def achievements(session,cast,catches,pending):
    pid=session.player_id
    life=base(pid,KIND)
    tier=_table("Fishing","fishing_level")[life.level]
    daily={g["achieve_condition_i_d"]:g for g in tier.get("fishing_level_quest",())}
    fish_table=_table("Fishing","fishing_info")
    atlas=set()
    numbers=Counter()
    numbers[(26 if catches else 27,0)]=1
    if catches:
        numbers[30,1 if cast.daylight else 2]=1
    for fid,qty in catches.items():
        fish=fish_table[fid]
        atlas.update(c["achieve_condition_i_d"] for c in fish.get("fish_achieve_rewards",()))
        numbers[28,fid]+=qty
        numbers[31,fish.get("new_size_type",0)]+=qty
        db.conn.execute("""INSERT INTO player_kv VALUES(?,'life_book',?,?) ON CONFLICT(player_id,ns,k)
            DO UPDATE SET v=min(4294967295,CAST(v AS INTEGER)+excluded.v)""",(pid,f"{KIND}:{fid}",qty))
    day=(shop.now()-timedelta(hours=4)).date().isoformat()
    extra=0
    notice=pb.LifeAchieveNotice(status=pb.StatusCode_OK)
    entry=notice.life_achieves.add(life_type=KIND)
    record=pb.AchieveNotice(status=pb.StatusCode_OK)
    goals=_table("Achieve","achieve")
    for aid in sorted(set(daily)|atlas|published_goals()):
        rule=goals[aid]
        value=numbers[rule.get("new_achieve_condition"),(rule.get("param") or [0])[0]]
        if not value:
            continue
        old=progress(pid,aid)
        nxt=min(old+value,MAX_COUNT if aid in daily else rule["count_param"])
        if old>=nxt:
            continue
        item=pb.Achieve(achieve_id=aid,count=nxt)
        db.set_achieve(pid,aid,item.SerializeToString())
        if aid in atlas or aid in daily:
            entry.life_achieve_.add().CopyFrom(item)
        if aid in published_goals():
            record.achieves.add().CopyFrom(item)
        if aid in daily:
            extra+=daily_proficiency(pid,aid,day,old,nxt,rule,daily[aid])
    if record.achieves:
        pending.notices.append((MsgId.AchieveNotice,record))
    if entry.life_achieve_:
        pending.notices.append((MsgId.LifeAchieveNotice,notice))
    if extra:
        life.proficiency_value=min(MAX_COUNT,life.proficiency_value+extra)
        db.set_life(pid,KIND,life.SerializeToString())
        proficiency=pb.LifeProficiencyNotice(status=pb.StatusCode_OK)
        proficiency.life_proficiency.life_type=KIND
        proficiency.life_proficiency.proficiency_value=life.proficiency_value
        pending.notices.append((MsgId.LifeProficiencyNotice,proficiency))


def settle(session,cast,rsp,pending):
    pid=session.player_id
    pending.balances.change(cast.energy[0],-cast.energy[1])
    pending.balances.change(cast.bait,-1)
    catches=Counter(f.i_d for f in cast.preview.fish_infos)
    for fid,qty in catches.items():
        pending.balances.change(_table("Fishing","fishing_info")[fid]["item_i_d"],qty)
    if cast.instance:
        raw=db.get_item_detail(pid,instance_id=cast.instance)
        item=pb.ItemDetail.FromString(raw) if raw else None
        if not item or not item.main_item.HasField("weapon") or item.main_item.weapon.weapon_id!=cast.rod or item.main_item.weapon.durability<=0:
            return pb.StatusCode_ITEM_NOT_ENOUGH
        if cast.preview.cost_endurance:
            item.main_item.weapon.durability=max(0,item.main_item.weapon.durability-cast.preview.cost_endurance)
            db.set_item_detail(pid,item.SerializeToString(),instance_id=cast.instance)
            pending.pack.items.add().CopyFrom(item)
            rsp.items.add().CopyFrom(item)
    elif cast.preview.cost_endurance:
        db.conn.execute("""INSERT INTO player_kv VALUES(?,'fishing_rod_wear',?,?) ON CONFLICT(player_id,ns,k)
            DO UPDATE SET v=CAST(v AS INTEGER)+excluded.v""",(pid,str(cast.rod),cast.preview.cost_endurance))
    achievements(session,cast,catches,pending)
    if catches:
        from private_bless_tasks import record as prayer
        prayer(session,26)
    rsp.items.extend(pending.balances.items.values())
    return pb.StatusCode_OK


def tick(session):
    cast=getattr(session,"fishing_cast",None)
    if not cast:
        return
    if cast.place!=place(session):
        set_after_commit(session,"fishing_cast",None)
        return
    if monotonic()<cast.due:
        return
    result=pb.FishingResultNotice()
    result.status=run(session,cast,result,settle)
    session.send(MsgId.FishingResultNotice,result,0)
    set_after_commit(session,"fishing_cast",None)
