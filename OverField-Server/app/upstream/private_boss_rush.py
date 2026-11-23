"""Seasonal Boss Rush using native stage/team records and the selected realm's saved progress."""
import hashlib
import time

from proto import net_pb2 as pb
from private_activity_rewards import _reward
from private_dungeons import _table, reward_items
from private_friends import state
from private_shop import balance
from network.msg_id import MsgId
from utils import db
from utils.pb_create import make_PlayerBriefInfo


def now_ms():
    return time.time_ns()//1_000_000


def season_row(sid):
    return _table("BossRush","boss_rush_season").get(sid)


def stages(row):
    return _table("BossRush","boss_rush_stage")[row["season_stage"]]["boss_rush_stage_group_info"]


def conditions(row):
    return _table("BossRush","boss_rush_achieve")[row["season_achieve"]]["boss_rush_achieve_group_info"]


def save(player,info):
    db.set_boss_rush_info(player,info.season_id,info.SerializeToString())


def entry(player,season,key):
    row=db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='boss_rush' AND k=?",
                        (player,f"{season}:{key}")).fetchone()
    return row[0] if row else None


def put(player,season,key,value):
    db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'boss_rush',?,?)",(player,f"{season}:{key}",value))


def finish(player,info):
    """End a run, retain each best team's public record and update its season-specific score goals."""
    for stage in info.stage_infos:
        if stage.total_score>stage.best_score:
            stage.best_score=stage.total_score
            put(player,info.season_id,f"best-{stage.stage_index}",stage.SerializeToString())
        stage.total_score=stage.total_damage_hp=stage.total_consume_time=0
        stage.ClearField("teams")
    info.best_total_score=sum(s.best_score for s in info.stage_infos)
    info.ClearField("used_characters")
    info.challenge_end_time=0
    info.current_stage_index=-1
    put(player,info.season_id,"phase","idle")
    save(player,info)
    for cfg in conditions(season_row(info.season_id)):
        key=cfg["achieve_i_d"]
        rule=_table("Achieve","achieve").get(key,{})
        if rule.get("new_achieve_condition")!=178:continue
        old=db.get_achieve(player,key)
        value=pb.Achieve.FromString(old) if old else pb.Achieve(achieve_id=key)
        if info.best_total_score>value.count:
            value.count=info.best_total_score
            db.set_achieve(player,key,value.SerializeToString())


def load(player,sid):
    from handlers.BossRushInfo import read_season
    return read_season(player,sid,now_ms()//1000).info


def active(player,sid):
    if not season_row(sid):return None
    info=load(player,sid)
    return info if info.challenge_end_time and 0<=info.current_stage_index<len(info.stage_infos) else None


def start(session,req,rsp):
    pid,sid=session.player_id,req.season_id
    row=season_row(sid)
    if not row or not 0<=req.stage_index<len(stages(row)):return pb.StatusCode_CFG_NOT_FOUND
    info=load(pid,sid)
    clock=now_ms()
    if not info.start_time<=clock<=info.end_time:return pb.StatusCode_Activity_Close
    cfg=stages(row)[req.stage_index]
    # Begin-day gates use the date in the national calendar, inclusive of the opening day.
    day=(clock+8*3_600_000)//86_400_000-(info.start_time+8*3_600_000)//86_400_000+1
    dungeon=_table("Dungeon","dungeon").get(cfg["dungeon_i_d"])
    if not dungeon or cfg.get("begin_day",1)>day:return pb.StatusCode_Activity_Close
    if db.get_players_info(pid,"level")[0]<dungeon.get("need_player_level",1):return pb.StatusCode_BAD_REQ
    if info.challenge_end_time:
        if info.current_stage_index!=req.stage_index:return pb.StatusCode_ChallengeNotEnd
    else:
        cost=row.get("battle_cost_item")
        if cost:
            item=balance(pid,cost)
            if not item or not item.main_item.HasField("base_item") or item.main_item.base_item.num<1:
                return pb.StatusCode_ITEM_NOT_ENOUGH
            item.main_item.base_item.num-=1
            db.set_item_detail(pid,item.SerializeToString(),cost)
            update=pb.PackNotice(status=pb.StatusCode_OK)
            update.items.add().CopyFrom(item)
            session.send(MsgId.PackNotice,update,0)
        info.current_stage_index=req.stage_index
        info.challenge_end_time=min(clock//1000+row["battle_time"],info.end_time//1000)
        info.ClearField("used_characters")
        put(pid,sid,"phase","ready")
        put(pid,sid,"checkpoint",b"")
        put(pid,sid,"settlement",b"")
        save(pid,info)
    rsp.season_id=sid
    rsp.stage_info.CopyFrom(info.stage_infos[req.stage_index])
    return pb.StatusCode_OK


def enter(session,req,rsp):
    pid,sid=session.player_id,req.season_id
    info=active(pid,sid)
    if not info:return pb.StatusCode_ChallengeNotStart
    stage=info.stage_infos[info.current_stage_index]
    phase=entry(pid,sid,"phase") or "ready"
    members=list(req.characters)
    if phase in ("entered","fighting"):
        if not stage.teams or members!=list(stage.teams[-1].characters):return pb.StatusCode_ChallengeNotEnd
    else:
        cfg=stages(season_row(sid))[stage.stage_index]
        dungeon=_table("Dungeon","dungeon")[cfg["dungeon_i_d"]]
        if (not 1<=len(members)<=dungeon.get("use_character_max_number",3) or len(set(members))!=len(members)
                or any(not cid or not db.get_characters(pid,cid) for cid in members)):
            return pb.StatusCode_BAD_REQ
        if set(info.used_characters).intersection(members):return pb.StatusCode_CHARACTER_ENTER_BOSS_RUSH
        stage.teams.add().characters.extend(members)
        info.used_characters.extend(members)
        save(pid,info)
        put(pid,sid,"phase","entered")
        put(pid,sid,"settlement",b"")
    checkpoint=entry(pid,sid,"checkpoint")
    if checkpoint:rsp.battle_info.ParseFromString(checkpoint)
    rsp.battle_info.stage_index=stage.stage_index
    rsp.season_id=sid
    return pb.StatusCode_OK


def battle(session,req,rsp):
    pid,sid=session.player_id,req.season_id
    info=active(pid,sid)
    if not info:return pb.StatusCode_ChallengeNotStart
    phase=entry(pid,sid,"phase")
    if phase=="entered":
        put(pid,sid,"battle-start",now_ms())
        put(pid,sid,"phase","fighting")
    elif phase!="fighting":return pb.StatusCode_ChallengeNotStart
    rsp.stage_index=info.current_stage_index
    rsp.season_id=sid
    rsp.battle_start_time=int(entry(pid,sid,"battle-start"))
    return pb.StatusCode_OK


def leave(session,req,rsp):
    pid,sid=session.player_id,req.season_id
    if not season_row(sid):return pb.StatusCode_CFG_NOT_FOUND
    info=load(pid,sid)
    signature=hashlib.sha256(req.SerializeToString(deterministic=True)).digest()
    receipt=entry(pid,sid,"settlement")
    if receipt and receipt[:32]==signature:
        rsp.ParseFromString(receipt[32:])
        return pb.StatusCode_OK
    index=info.current_stage_index
    if not info.challenge_end_time or not 0<=index<len(info.stage_infos) or entry(pid,sid,"phase")!="fighting":
        return pb.StatusCode_ChallengeNotStart
    stage=info.stage_infos[index]
    cfg=stages(season_row(sid))[index]
    dungeon=_table("Dungeon","dungeon")[cfg["dungeon_i_d"]]
    if not stage.teams:return pb.StatusCode_ChallengeNotStart
    if req.HasField("battle_info"):
        checkpoint=req.battle_info
        if checkpoint.stage_index!=index or len(checkpoint.monsters)>64 or len({m.monster_id for m in checkpoint.monsters})!=len(checkpoint.monsters):
            return pb.StatusCode_BAD_REQ
        if any(not m.monster_id or m.remain_hp>cfg["max_h_p"] for m in checkpoint.monsters):
            return pb.StatusCode_BAD_REQ
        put(pid,sid,"checkpoint",checkpoint.SerializeToString())
    remaining=max(0,cfg["max_h_p"]-stage.total_damage_hp)
    damage=min(req.total_damage_hp,remaining)
    elapsed=max(0,now_ms()-int(entry(pid,sid,"battle-start")))
    elapsed=min(elapsed,dungeon["fight_time"]*1000)
    last=stage.teams[-1]
    last.total_damage_hp=damage
    last.total_consume_time=elapsed
    stage.total_score+=damage
    stage.total_damage_hp+=damage
    stage.total_consume_time+=elapsed
    put(pid,sid,"phase","ready")
    if stage.total_damage_hp>=cfg["max_h_p"]:
        stage.is_passed=True
        finish(pid,info)
    else:
        save(pid,info)
    rsp.stage_best_score=stage.best_score
    rsp.best_total_score=info.best_total_score
    rsp.total_rank_ratio=info.total_rank_ratio
    put(pid,sid,"settlement",signature+rsp.SerializeToString())
    return pb.StatusCode_OK


def terminate(session,req,rsp):
    pid,sid=session.player_id,req.season_id
    if not season_row(sid):return pb.StatusCode_CFG_NOT_FOUND
    info=load(pid,sid)
    if info.challenge_end_time:
        finish(pid,info)
    elif entry(pid,sid,"phase")!="idle":return pb.StatusCode_ChallengeNotStart
    rsp.season_id=sid
    return pb.StatusCode_OK


def friend_rank(session,req,rsp):
    pid,sid=session.player_id,req.season_id
    if not season_row(sid):return pb.StatusCode_CFG_NOT_FOUND
    friends=db.conn.execute("SELECT f.friend_id FROM friend f JOIN friend g ON g.player_id=f.friend_id AND g.friend_id=f.player_id "
                            "WHERE f.player_id=? AND f.friend_status=? AND g.friend_status=?",(pid,pb.FriendStatus_FRIEND,pb.FriendStatus_FRIEND))
    board=[]
    for player in [pid,*(row[0] for row in friends)]:
        if db.get_boss_rush_info(player,sid):
            score=load(player,sid).best_total_score
            if score:board.append((score,player))
    rsp.season_id=sid
    for position,(score,player) in enumerate(sorted(board,key=lambda row:(-row[0],row[1]))):
        row=rsp.rank_info.add()
        make_PlayerBriefInfo(player,row.player_info)
        row.rank_info.player_id=player
        row.rank_info.score=score
        row.rank_info.rank_ratio=position*10_000//len(board)
    return pb.StatusCode_OK


def detail(session,req,rsp):
    pid=session.player_id
    target=req.player_id or pid
    if target!=pid and (state(pid,target)!=pb.FriendStatus_FRIEND or state(target,pid)!=pb.FriendStatus_FRIEND):
        return pb.StatusCode_FRIEND_NOT_EXIST
    rsp.player_id=target
    for sid in _table("BossRush","boss_rush_season"):
        if not db.get_boss_rush_info(target,sid):continue
        info=load(target,sid)
        if not info.best_total_score:continue
        row=rsp.details.add(season_id=sid,score=info.best_total_score,rank_ratio=info.total_rank_ratio)
        for stage in info.stage_infos:
            public=row.stage.add(stage_index=stage.stage_index,best_score=stage.best_score,is_passed=stage.is_passed)
            best=entry(target,sid,f"best-{stage.stage_index}")
            if best:public.teams.extend(pb.BossRushStageInfo.FromString(best).teams)
    return pb.StatusCode_OK


def claim(session,req,rsp):
    pid,sid=session.player_id,req.season_id
    season=season_row(sid)
    if not season:return pb.StatusCode_CFG_NOT_FOUND
    cfg=next((r for r in conditions(season) if r.get("achieve_i_d")==req.achieve_id),None)
    goal=_table("Achieve","achieve").get(req.achieve_id,{})
    if not cfg or goal.get("new_achieve_condition")!=178 or not goal.get("count_param"):
        return pb.StatusCode_CFG_NOT_FOUND
    if load(pid,sid).best_total_score<goal["count_param"]:return pb.StatusCode_FAIL
    rsp.season_id=sid
    rsp.achieve_id=req.achieve_id
    return _reward(session,"boss_rush_quest",f"{sid}:{req.achieve_id}",reward_items(pid,cfg["reward_i_d"]),rsp)
