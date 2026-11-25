"""State, field prizes and scene synchronization for national riddles, encounters and guide voices."""
from collections import Counter
from datetime import timedelta
from functools import partial

from network.msg_id import MsgId
from private_boxes import loot
import private_encounter_gifts as gifts
from private_dungeons import _table, progress, report, reward_items
from private_gameplay_transaction import Rejected, run
from private_shop import now
from private_request import set_after_commit
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item, make_QuestNotice

# These prototype entries have no corresponding prefab in the mounted 1.1.12 manifest.
UNRELEASED = {999,4070,4071,4072}
RIDDLE, ENCOUNTER = 1,2


def setup(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS realm_field_activities(
            player INTEGER NOT NULL, scene INTEGER NOT NULL, kind INTEGER NOT NULL, id INTEGER NOT NULL,
            state INTEGER NOT NULL, wins INTEGER NOT NULL DEFAULT 0, chest INTEGER NOT NULL DEFAULT 0,
            started INTEGER NOT NULL DEFAULT 0, available INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(player,scene,kind,id));
        CREATE INDEX IF NOT EXISTS field_active ON realm_field_activities(player,scene) WHERE started<>0;
        CREATE TABLE IF NOT EXISTS realm_riddle_daily(
            player INTEGER PRIMARY KEY, day TEXT NOT NULL, count INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS field_pending_prizes ON private_boxes(player_id,zone)
            WHERE dynamic=1 AND claimed=0 AND template IN (2001,2002);
        CREATE INDEX IF NOT EXISTS field_chest_indexes ON private_boxes(player_id,dynamic,idx);
    """)


def recover_puzzle_prizes(s, all_scenes):
    # The client spawns a dynamic puzzle chest only from the finish response.
    # Carry uncollected contents across visits using the national lost-challenge mail.
    params = (s.player_id,) if all_scenes else (s.player_id, s.scene_id)
    scope = "" if all_scenes else " AND zone=?"
    rows = db.conn.execute("SELECT zone,idx,data FROM private_boxes WHERE player_id=? "
                           "AND dynamic=1 AND claimed=0 AND template IN (2001,2002)" + scope, params).fetchall()
    totals = Counter()
    for zone, idx, blob in rows:
        chest = pb.TreasureBoxData.FromString(blob)
        if chest.type != pb.ETreasureBoxType_Riddle:
            continue
        for detail in chest.rewards:
            totals[detail.main_item.item_id] += detail.main_item.base_item.num
        del chest.rewards[:]
        db.conn.execute("UPDATE private_boxes SET data=?,claimed=1 WHERE player_id=? AND zone=? AND dynamic=1 AND idx=?",
                        (chest.SerializeToString(),s.player_id,zone,idx))
    if totals:
        from private_wishes import send_mail
        send_mail(s.player_id,"挑战遗失物","你在探索过程中遗失的物品，有其他探索队的好心人给你寄了过来~",
                  totals.items(),now(),s,sender="系统",model_id=3)


def depart(s, all_scenes=False):
    # A stopped Unity encounter has no actors left to settle. Preserve wins and prizes.
    if all_scenes or getattr(s,"has_field_run",False):
        where, values = ("player=?",(s.player_id,)) if all_scenes else ("player=? AND scene=?",(s.player_id,s.scene_id))
        db.conn.execute("UPDATE realm_field_activities SET state=?, started=0 WHERE started<>0 AND "+where,
                        (pb.Close,*values))
        recover_puzzle_prizes(s,all_scenes)
    set_after_commit(s,"has_field_run",False)
    if getattr(s,"field_voices",None):
        set_after_commit(s,"field_voices",set())


def map_of_riddle(cfg):
    # Scene folders for the two variants of world 1/2 are shared. The original
    # prefab suffix distinguishes e.g. FlipDrum_1001 and MusicBell_3001.
    path = cfg["asset_full_name"]
    region = path.split("/")[2]
    if region in ("SceneChristmas", "SceneHotpot"):
        return 1001 if region=="SceneChristmas" else 1002
    variant = cfg["i_d"] % 100000 // 1000
    return variant or 1


def configured(kind,key,scene):
    if kind==RIDDLE:
        cfg = _table("Riddle","riddle").get(key)
        return cfg if cfg and map_of_riddle(cfg)==scene else None
    cfg = _table("BattleEncounter","battle_encounter").get(key)
    return cfg if cfg and key not in UNRELEASED and key//1000==scene else None


def load(pid,scene,kind,key):
    return db.conn.execute("SELECT state,wins,chest,started,available FROM realm_field_activities "
                           "WHERE player=? AND scene=? AND kind=? AND id=?",(pid,scene,kind,key)).fetchone()


def save(s,kind,key,state,wins,chest,started,available):
    db.conn.execute("INSERT OR REPLACE INTO realm_field_activities VALUES(?,?,?,?,?,?,?,?,?)",
                    (s.player_id,s.scene_id,kind,key,state,wins,chest,started,available))


def config_limit(key):
    return int(_table("GlobalConfig","config","key")[key]["values"][0])


def daily_template(s,cfg,wins):
    day = (now()-timedelta(hours=5)).date().isoformat()
    entry = db.conn.execute("SELECT count FROM realm_riddle_daily WHERE player=? AND day=?", (s.player_id,day)).fetchone()
    count = entry[0] if entry else 0
    db.conn.execute("INSERT OR REPLACE INTO realm_riddle_daily VALUES(?,?,?)",(s.player_id,day,count+1))
    if count >= config_limit("RiddleTreasureLimitCountEveryDay"):
        return 0
    return cfg["repeat_reward_pool_i_d" if wins else "reward_pool_i_d"]


def create_chest(s,kind,template):
    if not template:
        return 0
    zone = s.scene_id
    if kind==RIDDLE:
        amounts = reward_items(s.player_id, template)
        if not amounts:
            raise Rejected(pb.StatusCode_CFG_NOT_FOUND)
        items = [make_item(item,amount,s.player_id) for item,amount in amounts.items()]
    else:
        # National clients specify the chest template. The ordinary realm box drop
        # policy supplies its contents where the publisher did not ship a prize table.
        items = loot(s,template,True)
    idx, = db.conn.execute("SELECT COALESCE(MAX(idx),0)+1 FROM private_boxes WHERE player_id=? AND dynamic=1",
                           (s.player_id,)).fetchone()
    info=pb.TreasureBoxData(index=idx,box_id=template,state=pb.TreasureBoxState_Open,
        type=pb.ETreasureBoxType_Riddle if kind==RIDDLE else pb.ETreasureBoxType_Encounter)
    for item in items:
        item.pack_type=pb.ItemDetail.PackType_TempStorageArea
        info.rewards.add().CopyFrom(item)
    db.conn.execute("INSERT INTO private_boxes(player_id,zone,dynamic,idx,template,data) VALUES(?,?,1,?,?,?)",
                    (s.player_id,zone,idx,template,info.SerializeToString()))
    return idx


def show_chest(s,idx,details):
    if not idx or not gifts.visible(s,idx):
        return 0
    row=db.conn.execute("SELECT template,data FROM private_boxes WHERE player_id=? AND zone=? AND dynamic=1 AND idx=?"
                        " AND claimed=0",(s.player_id,s.scene_id,idx)).fetchone()
    if not row:
        return 0
    if details is not None:
        details.index, details.box_id=idx,row[0]
        details.max_quality=max((item.extra_quality for item in pb.TreasureBoxData.FromString(row[1]).rewards),default=0)
    return row[0]


_goal_cache = None


def credit(s,kind,key):
    global _goal_cache
    table = _table("Achieve","achieve")
    if _goal_cache is None or _goal_cache[0] is not table:
        goals={}
        for aid,row in table.items():
            condition=row.get("new_achieve_condition")
            if condition not in (5,45,102,103,115,118) or not row.get("count_param"):
                continue
            args=row.get("param",[])
            if len(args)<=1:
                goals.setdefault((condition,args[0] if args else 0),[]).append((aid,row["count_param"]))
        _goal_cache=(table,goals)
    candidates=_goal_cache[1]
    keys=((102,s.scene_id),(118,0)) if kind==RIDDLE else ((103,s.scene_id),(5,s.scene_id),(45,key),(115,0))
    applicable=[row for goal in keys for row in candidates.get(goal,())]
    report(s,((aid,min(limit,progress(s.player_id,aid)+1)) for aid,limit in applicable))
    if kind==ENCOUNTER:
        from private_bless_tasks import ensure
        ensure(s,publish=True)
    q=make_QuestNotice(s,{aid for aid,_ in applicable})
    if q:
        s.send(MsgId.QuestNotice,q,0)


def character_experience(s,encounter):
    if encounter!=1073:
        return
    # The public community reference records 137 character XP for this encounter.
    curves=_table("Character","level")
    notice=pb.TeamCharExpUpdateNotice(status=pb.StatusCode_OK)
    for key in dict.fromkeys(db.get_players_info(s.player_id,"team")[0]):
        if not key:
            continue
        blob=db.get_characters(s.player_id,key)
        if not blob:
            continue
        ch=pb.Character.FromString(blob[0])
        growth=_table("Character","character")[key]["growth_level_i_d"]
        levels={r["level"]:r.get("need_exp",0) for r in curves[growth]["level_info"]}
        ch.exp+=137
        while ch.level<ch.max_level and levels.get(ch.level) and ch.exp>=levels[ch.level]:
            ch.exp-=levels[ch.level]
            ch.level+=1
        if ch.level>=ch.max_level:
            ch.exp=0
        db.set_character(s.player_id,key,ch.SerializeToString())
        notice.char_levels.add(character_id=key,level=ch.level,exp=ch.exp)
    if notice.char_levels:
        def sync_team():
            for member in (s.scene_player.team.char1,s.scene_player.team.char2,s.scene_player.team.char3):
                for update in notice.char_levels:
                    if member.char_id==update.character_id:
                        member.char_lv=update.level
        s.commit_callbacks.append((sync_team,()))
        s.send(MsgId.TeamCharExpUpdateNotice,notice,0)


def update(kind,s,req,rsp,write):
    key=req.riddle_id if kind==RIDDLE else req.encounter_id
    cfg=configured(kind,key,s.scene_id)
    phase=req.battle_state
    if s.dungeon[0] or not cfg or phase not in (pb.Start,pb.Reset,pb.Finish,pb.Fail,pb.Close):
        return pb.StatusCode_BAD_REQ
    if kind==RIDDLE and req.is_win and phase!=pb.Finish:
        return pb.StatusCode_BAD_REQ
    pid,scene=s.player_id,s.scene_id
    prior=load(pid,scene,kind,key)
    state,wins,chest,started,available=prior or (pb.Close,0,0,0,0)
    stamp=int(now().timestamp())
    expired = kind==ENCOUNTER and started and stamp-started>cfg["live_timer"]
    victory=phase==pb.Finish and (kind==ENCOUNTER or req.is_win)
    preview=0
    if phase==pb.Start:
        if kind==ENCOUNTER:
            if stamp<available:
                return pb.StatusCode_BAD_REQ
            preview=gifts.for_battle(s,key,available,started and not expired)
        if not started or expired:
            started=stamp
        state=pb.Start
        set_after_commit(s,"has_field_run",True)
    elif victory:
        if state!=pb.Finish or not wins:
            if state!=pb.Start or not started or expired:
                return pb.StatusCode_BAD_REQ
            template = daily_template(s,cfg,wins) if kind==RIDDLE else gifts.for_battle(s,key,available,started)
            chest=create_chest(s,kind,template)
            if kind==ENCOUNTER and chest:
                gifts.earned(s,chest,stamp)
            if kind==RIDDLE and not template:
                # The published cap refers to a fallback pool. No retail row for
                # pool 3 is present in 1.1.12. Only award a configured fallback.
                extra=reward_items(pid,config_limit("RiddleTreasureLimitRewardPoolID"))
                if extra:
                    write.grant(extra,pb.ClaimQuestRewardRsp().items)
            if kind==ENCOUNTER and not wins:
                character_experience(s,key)
            credit(s,kind,key)
            state,wins,started=pb.Finish,wins+1,0
            available=stamp+cfg["c_d"] if kind==ENCOUNTER else 0
        show_chest(s,chest,rsp.dynamic_treasure_box_base_info)
    elif phase in (pb.Reset,pb.Close,pb.Fail,pb.Finish):
        if kind==ENCOUNTER and started:
            available=stamp+cfg["c_d"]
        if phase==pb.Reset and kind==RIDDLE and started:
            state=pb.Start
        else:
            state=pb.Close if kind==ENCOUNTER else (pb.Fail if phase==pb.Finish else phase)
            started=0
    save(s,kind,key,state,wins,chest,started,available)
    if kind==RIDDLE:
        info=rsp.riddle_data
        info.riddle_id,info.state,info.is_win=key,phase,victory
    else:
        rsp.encounter.battle_id=key
        rsp.encounter.state=state
        rsp.encounter.box_id=show_chest(s,chest,None) if state==pb.Finish else preview
    return pb.StatusCode_OK


def encounter_state(s, key, row, instant):
    data=pb.BattleEncounterData(battle_id=key,state=pb.Start)
    if row:
        state,wins,chest,started,available=row
        if available>instant or started and instant-started<=configured(ENCOUNTER,key,s.scene_id)["live_timer"]:
            data.state=state
            if state==pb.Finish:
                data.box_id=show_chest(s,chest,None)
    return data


def mark_previews(s, entries, records, stamp):
    available=[]
    for entry in entries:
        if entry.state!=pb.Start:
            continue
        key=entry.battle_id
        prior=records.get(key)
        started,cycle=prior[3:] if prior else (0,0)
        live=bool(started and stamp-started<=configured(ENCOUNTER,key,s.scene_id)["live_timer"])
        available.append((key,cycle,live))
    marks=gifts.previews(s,available)
    for entry in entries:
        if entry.battle_id in marks:
            entry.box_id=marks[entry.battle_id]


def encounters(s,req,rsp):
    ids=tuple(dict.fromkeys(req.encounter_ids))
    configured_ids=_table("BattleEncounter","battle_encounter")
    if s.dungeon[0] or len(req.encounter_ids)>512 or any(k not in configured_ids or k//1000!=s.scene_id for k in ids):
        return pb.StatusCode_BAD_REQ
    instant=int(now().timestamp())
    records=dict((row[0],row[1:]) for row in db.conn.execute("SELECT id,state,wins,chest,started,available "
                  "FROM realm_field_activities WHERE player=? AND scene=? AND kind=?",(s.player_id,s.scene_id,ENCOUNTER)))
    for key in ids:
        if key in UNRELEASED:
            rsp.encounters.add(battle_id=key,state=pb.Close)
        else:
            rsp.encounters.add().CopyFrom(encounter_state(s,key,records.get(key),instant))
    mark_previews(s,rsp.encounters,records,instant)
    return pb.StatusCode_OK


def voice(s,req,rsp):
    cfg=_table("VoiceRegion","region").get(req.voice_id)
    if not cfg or s.scene_id!=100 or s.dungeon[0] or cfg.get("new_voice_region_refresh_type")!=1:
        return pb.StatusCode_BAD_REQ
    heard=getattr(s,"field_voices",set())
    if req.voice_id not in heard:
        set_after_commit(s,"field_voices",heard|{req.voice_id})
        set_after_commit(s,"voice_scope",(s.scene_id,s.channel_id))
    rsp.voice_id=req.voice_id
    return pb.StatusCode_OK


def fill(s,data):
    if not getattr(s,"field_connected",False):
        depart(s,all_scenes=True)
        set_after_commit(s,"field_connected",True)
    if s.dungeon[0]:
        return
    if s.scene_id==100 and getattr(s,"voice_scope",None)==(s.scene_id,s.channel_id):
        data.region_voices.extend(sorted(getattr(s,"field_voices",())))
    instant=int(now().timestamp())
    encounter_records={}
    for kind,key,state,wins,chest,started,available in db.conn.execute("SELECT kind,id,state,wins,chest,started,available "
            "FROM realm_field_activities WHERE player=? AND scene=?",(s.player_id,s.scene_id)):
        if kind==RIDDLE:
            data.riddles.add(riddle_id=key,is_win=bool(wins) and state==pb.Finish,state=state)
        elif kind==ENCOUNTER:
            encounter_records[key]=(state,wins,chest,started,available)
            data.encounter_data.add().CopyFrom(encounter_state(s,key,encounter_records[key],instant))
    mark_previews(s,data.encounter_data,encounter_records,instant)


riddle = partial(run,operation=partial(update,RIDDLE))
encounter = partial(run,operation=partial(update,ENCOUNTER))
