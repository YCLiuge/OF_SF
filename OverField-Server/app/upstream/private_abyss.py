"""Abyss teams, seasonal progress and timed dungeon visits for the national PC build."""
import time

from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_PlayerBriefInfo, make_SceneTeam
from private_activity_rewards import _reward
from private_dungeons import _table, config, load, reward_items
import private_shop as shop


def setup():
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS pc_abyss_teams (
            player INTEGER, dungeon INTEGER, mode INTEGER, slot INTEGER, team BLOB NOT NULL,
            PRIMARY KEY(player,dungeon,mode,slot)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS pc_dungeon_clock (
            player INTEGER, dungeon INTEGER, visit INTEGER, mode INTEGER, started INTEGER NOT NULL,
            first_half INTEGER, elapsed INTEGER, PRIMARY KEY(player,dungeon)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS pc_abyss_scores (
            season INTEGER, mode INTEGER, player INTEGER, dungeon INTEGER,
            elapsed INTEGER NOT NULL, stars INTEGER NOT NULL, first_team BLOB NOT NULL, second_team BLOB,
            PRIMARY KEY(season,mode,player,dungeon)) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS pc_abyss_player_scores ON pc_abyss_scores(season,player);
    """)


def windows():
    return _table("Abyss", "abyss_season")


def active(row, instant):
    t = instant.strftime("%Y-%m-%d %H:%M:%S")
    return row.get("start_time", "") <= t <= (row.get("end_time") or "9999")


def stages(row):
    return _table("Abyss", "abyss_stage")[row["season_stage"]]["abyss_stage_group_info"]


_stage_index = None


def stage(did):
    global _stage_index
    seasons, groups = windows(), _table("Abyss","abyss_stage")
    if _stage_index is None or _stage_index[0] is not seasons or _stage_index[1] is not groups:
        lookup = {e["dungeon_i_d"]:(season,e) for season in seasons.values() for e in stages(season)}
        _stage_index = seasons,groups,lookup
    row,entry = _stage_index[2].get(did,(None,None))
    return (row,entry) if row and active(row,shop.now()) else (None,None)


def slots(player, did, mode):
    return {i:pb.Team.FromString(raw) for i,raw in db.conn.execute(
        "SELECT slot,team FROM pc_abyss_teams WHERE player=? AND dungeon=? AND mode=?", (player,did,mode))}


def members(team):
    return (team.char1, team.char2, team.char3)


def permitted(entry, mode):
    return mode in (pb.BONUS_100,pb.BONUS_50,pb.BONUS_0,pb.CHALLENGE) and (
        mode == pb.CHALLENGE) == bool(entry.get("is_challenge_dungeon"))


def team_update(session, req, rsp):
    _, entry = stage(req.dungeon_id)
    if not entry or not permitted(entry, req.bonus_type):
        return pb.StatusCode_DungeonUnlock
    if req.team_index not in ((0,1) if entry.get("is_double_team") else (0,)):
        return pb.StatusCode_BAD_REQ
    team = req.abyss_team
    ids = [x for x in members(team) if x]
    if not team.char1 or len(ids) != len(set(ids)) or any(not db.get_characters(session.player_id,x) for x in ids):
        return pb.StatusCode_BAD_REQ
    other = slots(session.player_id,req.dungeon_id,req.bonus_type).get(1-req.team_index)
    if other and set(ids).intersection(members(other)):
        return pb.StatusCode_BAD_REQ
    clock = current_clock(session,req.dungeon_id)
    if clock:
        return pb.StatusCode_BAD_REQ
    db.conn.execute("INSERT OR REPLACE INTO pc_abyss_teams VALUES(?,?,?,?,?)",
                    (session.player_id,req.dungeon_id,req.bonus_type,req.team_index,team.SerializeToString()))
    return pb.StatusCode_OK


def current_clock(session, did):
    if not did or did != session.dungeon[0]:
        return None
    return db.conn.execute("""SELECT visit,mode,started,first_half,elapsed FROM pc_dungeon_clock
        WHERE player=? AND dungeon=? AND visit=?""", (session.player_id,did,session.dungeon_visit)).fetchone()


def switched(session, req, rsp):
    did = session.dungeon[0]
    clock = current_clock(session,did)
    if not clock or clock[1] != req.bonus_type or clock[4] is not None:
        return pb.StatusCode_BAD_REQ
    second = slots(session.player_id,did,req.bonus_type).get(1)
    if second is None or clock[3] is None:
        return pb.StatusCode_BAD_REQ
    if clock[3] > 0:
        db.conn.execute("UPDATE pc_dungeon_clock SET first_half=-first_half WHERE player=? AND dungeon=?",
                        (session.player_id,did))
    make_SceneTeam(session.player_id,members(second),rsp.team,life_scope=(did,session.dungeon_visit))
    return pb.StatusCode_OK


def operate(session, req, rsp):
    did = session.dungeon[0]
    if not did:
        return pb.StatusCode_PlayerNotInDungeon
    season, entry = stage(did)
    if (entry and not permitted(entry,req.bonus_type)) or (not entry and req.bonus_type != pb.BONUS_100):
        return pb.StatusCode_BAD_REQ
    cfg = config(did)
    if not cfg or (cfg.get("new_mission_type") == 8 and not entry):
        return pb.StatusCode_DungeonUnlock
    pid = session.player_id
    pair = slots(pid,did,req.bonus_type) if entry else {}
    clock = current_clock(session,did)
    stamp = time.time_ns() // 1_000_000
    if req.operate_type == pb.DungeonOperateReq.START:
        if clock:
            return pb.StatusCode_OK if clock[1] == req.bonus_type and clock[4] is None else pb.StatusCode_BAD_REQ
        roster = load(pid,did)
        if entry and (entry.get("is_double_team") and 1 not in pair or
                      0 in pair and members(pair[0]) != (roster.char1,roster.char2,roster.char3)):
            return pb.StatusCode_BAD_REQ
        db.conn.execute("INSERT OR REPLACE INTO pc_dungeon_clock VALUES(?,?,?,?,?,NULL,NULL)",
                        (pid,did,session.dungeon_visit,req.bonus_type,stamp))
        return pb.StatusCode_OK
    if req.operate_type != pb.DungeonOperateReq.END or not clock or clock[1] != req.bonus_type:
        return pb.StatusCode_BAD_REQ
    visit, mode, started, half, elapsed = clock
    if elapsed is None:
        elapsed = max(1, stamp-started)
        if elapsed > 0xffffffff:
            return pb.StatusCode_BAD_REQ
        if entry and entry.get("is_double_team") and half is None:
            db.conn.execute("UPDATE pc_dungeon_clock SET first_half=? WHERE player=? AND dungeon=?", (elapsed,pid,did))
            rsp.consume_time = elapsed
            return pb.StatusCode_OK
        if entry and entry.get("is_double_team") and half > 0:
            rsp.consume_time = half
            return pb.StatusCode_OK
        db.conn.execute("UPDATE pc_dungeon_clock SET elapsed=? WHERE player=? AND dungeon=?", (elapsed,pid,did))
    rsp.consume_time = elapsed
    rsp.star = max((s for s in (1,2,3) if elapsed < 1000 * cfg.get(f"star_timer{s}",0)),default=0)
    if season:
        record_finish(session,load(pid,did))
    return pb.StatusCode_OK


def record_finish(session, data):
    # Finish and timer END can arrive in either order. A fresh visit must supply both.
    clock = current_clock(session,data.dungeon_id)
    if not clock or clock[4] is None:
        return
    season, entry = stage(data.dungeon_id)
    if not season:
        return
    from private_dungeons import ensure_facts_table
    ensure_facts_table()
    if not db.conn.execute("""SELECT 1 FROM private_dungeon_facts
        WHERE player=? AND dungeon=? AND kind='finish' AND point=? AND template=0""",
        (session.player_id,data.dungeon_id,data.enter_times)).fetchone():
        return
    _,mode,_,_,elapsed = clock
    pair = slots(session.player_id,data.dungeon_id,mode)
    first = pair.get(0, pb.Team(char1=data.char1,char2=data.char2,char3=data.char3))
    second = pair.get(1)
    cfg = config(data.dungeon_id)
    stars = max((s for s in (1,2,3) if elapsed < 1000*cfg.get(f"star_timer{s}",0)),default=0)
    db.conn.execute("""INSERT INTO pc_abyss_scores VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(season,mode,player,dungeon) DO UPDATE SET
        elapsed=excluded.elapsed,stars=excluded.stars,first_team=excluded.first_team,second_team=excluded.second_team
        WHERE excluded.elapsed < pc_abyss_scores.elapsed""",
        (season["i_d"],mode,session.player_id,data.dungeon_id,elapsed,stars,first.SerializeToString(),
         second.SerializeToString() if second else None))


def season_info(pid, sid, target, *, include_selection=False):
    target.season_id = sid
    for mode,did,elapsed,first,second in db.conn.execute("""SELECT mode,dungeon,elapsed,first_team,second_team
        FROM pc_abyss_scores WHERE season=? AND player=? ORDER BY mode,dungeon""",(sid,pid)):
        table = target.dungeon_info50 if mode == pb.BONUS_50 else target.dungeon_info0 if mode == pb.BONUS_0 else target.dungeon_info
        result = table[did]
        result.dungeon_id, result.finish_time, result.is_challenge = did,elapsed,mode==pb.CHALLENGE
        result.abyss_teams.add().ParseFromString(first)
        if second:
            result.abyss_teams.add().ParseFromString(second)


    if include_selection:
        allowed = {row["dungeon_i_d"] for row in stages(windows()[sid])}
        selected = {}
        for did,mode,slot,raw in db.conn.execute(
                "SELECT dungeon,mode,slot,team FROM pc_abyss_teams WHERE player=? ORDER BY dungeon,mode,slot", (pid,)):
            if did in allowed:
                selected.setdefault((did,mode), {})[slot] = raw
        for (did,mode), teams in selected.items():
            table = target.dungeon_info50 if mode == pb.BONUS_50 else target.dungeon_info0 if mode == pb.BONUS_0 else target.dungeon_info
            record = table[did]
            record.dungeon_id, record.is_challenge = did, mode == pb.CHALLENGE
            del record.abyss_teams[:]
            for slot in range(max(teams)+1):
                team = record.abyss_teams.add()
                if slot in teams:
                    team.ParseFromString(teams[slot])


def info(session,req,rsp):
    rsp.abyss_info.player_id = session.player_id
    instant = shop.now()
    for sid,row in windows().items():
        if active(row,instant):
            season_info(session.player_id,sid,rsp.abyss_info.season_info[sid],include_selection=True)
            if row.get("start_time"):
                rsp.in_progress_season_id = sid
    return pb.StatusCode_OK


def friend_rank(session,req,rsp):
    if req.season_id not in windows():
        return pb.StatusCode_CFG_NOT_FOUND
    rows = db.conn.execute("""WITH friends AS (
        SELECT ? AS pid UNION SELECT f.friend_id FROM friend f JOIN friend reverse
        ON reverse.player_id=f.friend_id AND reverse.friend_id=f.player_id AND reverse.friend_status=2
        WHERE f.player_id=? AND f.friend_status=2)
        SELECT p.player_id FROM friends JOIN players p ON p.player_id=friends.pid
        LEFT JOIN pc_abyss_scores s ON s.player=p.player_id AND s.season=? AND s.mode=?
        WHERE p.player_id=? OR (COALESCE(p.hide_value,0) & ?)=0 GROUP BY p.player_id
        ORDER BY COUNT(s.dungeon) DESC,SUM(s.elapsed),p.player_id""",
        (session.player_id,session.player_id,req.season_id,pb.BONUS_100,session.player_id,pb.HideType_Abyss_Rank_Type))
    for (pid,) in rows:
        item = rsp.rank_list.add()
        make_PlayerBriefInfo(pid,item.player_info)
        season_info(pid,req.season_id,item.abyss_info)
    return pb.StatusCode_OK


def realm_rank(session,req,rsp):
    if req.season_id not in windows() or req.bonus_type not in pb.AbyssRankType.values():
        return pb.StatusCode_BAD_REQ
    season = windows()[req.season_id]
    if not season.get("start_time"):
        return pb.StatusCode_OK
    required = sum(bool(e.get("is_challenge_dungeon")) == (req.bonus_type == pb.CHALLENGE)
                   for e in stages(season))
    if not required:
        return pb.StatusCode_OK
    rows = db.conn.execute("""SELECT s.player,SUM(s.elapsed) total FROM pc_abyss_scores s
        JOIN players p ON p.player_id=s.player WHERE s.season=? AND s.mode=? AND (COALESCE(p.hide_value,0) & ?)=0
        GROUP BY s.player HAVING COUNT(*)=? ORDER BY total,s.player LIMIT 50""",
        (req.season_id,req.bonus_type,pb.HideType_Abyss_Rank_Type,required))
    for pid,total in rows:
        entry = rsp.rank_list.add(season_id=req.season_id,total_time=min(total,0xffffffff))
        make_PlayerBriefInfo(pid,entry.player_info)
    return pb.StatusCode_OK


def quest_reward(session,req,rsp):
    season = windows().get(req.season_id)
    if not season or not active(season,shop.now()):
        return pb.StatusCode_Activity_Close
    entries = _table("Abyss","season_achieve")[season["season_achieve"]]["season_achieve_group_info"]
    rule = next((r for r in entries if r["achieve_i_d"]==req.condition_id),None)
    condition = _table("Achieve","achieve").get(req.condition_id)
    if not rule or not condition:
        return pb.StatusCode_CFG_NOT_FOUND
    # Legacy dungeon saves have epoch timestamps. Keep their genuine clear counts
    # for single-stage achievements, without treating epoch seconds as best lap times.
    kind = condition.get("new_achieve_condition")
    if kind == 6 and len(condition.get("param",()))==1:
        progress = load(session.player_id,condition["param"][0]).finish_times
    elif kind in (58,59,60):
        scores = db.conn.execute("""SELECT mode,dungeon,stars,elapsed FROM pc_abyss_scores
            WHERE season=? AND player=? AND mode<>?""",(req.season_id,session.player_id,pb.CHALLENGE))
        best_stars, times = {}, {}
        for mode,did,stars,elapsed in scores:
            best_stars[did] = max(stars,best_stars.get(did,0))
            times.setdefault(mode,[]).append(elapsed)
        if kind == 58:
            progress = sum(best_stars.values())
        elif kind == 59:
            progress = len(best_stars)
        else:
            required = sum(not e.get("is_challenge_dungeon") for e in stages(season))
            threshold, = condition["param"]
            progress = int(any(len(t)==required and sum(t)<threshold for t in times.values()))
    else:
        return pb.StatusCode_CFG_NOT_FOUND
    if progress < condition["count_param"]:
        return pb.StatusCode_DungeonCantReward
    return _reward(session,"abyss_quest",f"{req.season_id}:{req.condition_id}",
                   reward_items(session.player_id,rule["reward_i_d"]),rsp)


def player_level_reward(session,req,rsp):
    row = _table("Level","player_level").get(req.level)
    if not row:
        return pb.StatusCode_CFG_NOT_FOUND
    if db.get_players_info(session.player_id,"level")[0] < req.level:
        return pb.StatusCode_PLAYER_LEVEL_LIMIT
    rsp.level = req.level
    if row.get("reward_i_d") == 999999999:
        return pb.StatusCode_OK
    from private_activity_rewards import deliver_once
    return deliver_once(session,"player_level",req.level,reward_items(session.player_id,row["reward_i_d"]),rsp.items)
