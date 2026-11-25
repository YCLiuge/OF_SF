"""Native PC team beacons and the two multiplayer plaza courses in TeamGameplay."""
import json
import random
import time

from network.msg_id import MsgId
from private_arcade_wallet import config
from private_crafts import payments, save_pack
from utils.res_loader import res
from private_room_actions import distance
from proto import net_pb2 as pb
from utils import db


def initialize():
    # Staged alongside the request's inventory writes and rolled back with them.
    # Room sessions are transient and do not outlive a realm process.
    db.conn.executescript("""
        CREATE TEMP TABLE IF NOT EXISTS mp_beacons(team INTEGER PRIMARY KEY,scene INTEGER NOT NULL,
            channel INTEGER NOT NULL, data BLOB NOT NULL);
        CREATE INDEX IF NOT EXISTS mp_beacons_scene ON mp_beacons(scene,channel);
        CREATE TEMP TABLE IF NOT EXISTS mp_rounds(team INTEGER PRIMARY KEY,data TEXT NOT NULL);
    """)


def beacon_for(team):
    row = db.conn.execute("SELECT data FROM mp_beacons WHERE team=?", (team,)).fetchone()
    return pb.MPBeacon.FromString(row[0]) if row else None


def in_game(team):
    return bool(db.conn.execute("SELECT 1 FROM mp_rounds WHERE team=?", (team,)).fetchone())


def write_beacon(team, beacon):
    db.conn.execute("INSERT OR REPLACE INTO mp_beacons VALUES(?,?,?,?)",
                    (team,beacon.scene_id,beacon.channel_label,beacon.SerializeToString()))


def round_for(team):
    row = db.conn.execute("SELECT data FROM mp_rounds WHERE team=?", (team,)).fetchone()
    return json.loads(row[0]) if row else None


def write_round(team, state):
    db.conn.execute("INSERT OR REPLACE INTO mp_rounds VALUES(?,?)", (team,json.dumps(state,separators=(',',':'))))


def scene_beacons(s, data):
    for raw, in db.conn.execute("SELECT data FROM mp_beacons WHERE scene=? AND channel=?", (s.scene_id,s.channel_id)):
        data.m_p_beacons.add().ParseFromString(raw)


def relay_beacon(s, row, beacon, before=None):
    import private_mp_teams as teams
    team = row[0]
    msg = pb.MPTeamBeaconNotice(status=pb.StatusCode_OK,team_id=team,beacon=beacon)
    teams.notify(beacon.members,MsgId.MPTeamBeaconNotice,msg)
    if before is not None and before != beacon.can_start:
        notice = pb.MPBeaconCanStartNotice(status=pb.StatusCode_OK,team_id=team,can_start=beacon.can_start)
        teams.notify(beacon.members,MsgId.MPBeaconCanStartNotice,notice)
    notice = pb.SceneMPBeaconNotice(status=pb.StatusCode_OK)
    notice.beacons[team].CopyFrom(beacon)
    for peer in s.social_rooms.get((beacon.scene_id,beacon.channel_label),()):
        if peer.running:
            peer.send(MsgId.SceneMPBeaconNotice,notice,0)


def nearby(s,beacon):
    # Network vector coordinates are centimetres in the PC scene record.
    return (s.scene_id,s.channel_id)==(beacon.scene_id,beacon.channel_label) and (
        distance(s.scene_player.team.char1.pos,beacon.position) <= (100*config("BeaconRange")[0])**2)


def members_present(s,beacon):
    return {p.player_id:p for p in s.social_rooms.get((beacon.scene_id,beacon.channel_label),())
            if p.running and p.player_id in beacon.members and nearby(p,beacon)}


def populate(row, beacon):
    import private_mp_teams as teams
    rules = teams.mode_rules(row[3],row[4])
    previous = dict(beacon.members)
    beacon.members.clear()
    for pid,_ in teams.roster(row[0]):
        beacon.members[pid] = pid==row[2] or previous.get(pid,False)
    beacon.owner_id,beacon.name = row[2],row[1]
    beacon.mode_info.mode_id,beacon.mode_info.sub_mode_id = row[3],row[4]
    beacon.mode_info.game_mode = rules["new_play_mode"]
    beacon.can_start = (beacon.status==pb.MPBeaconStatus_CALLING_IN and
                        len(beacon.members)>=rules["player_min"] and all(beacon.members.values()))


def roster_changed(s,row):
    beacon = beacon_for(row[0])
    if beacon:
        before = beacon.can_start
        populate(row,beacon)
        write_beacon(row[0],beacon)
        relay_beacon(s,row,beacon,before)


def remove_beacon(s,row):
    beacon = beacon_for(row[0])
    if beacon:
        previous = beacon.can_start
        beacon.status,beacon.can_start = pb.MPBeaconStatus_UNPLACED,False
        relay_beacon(s,row,beacon,previous)
        db.conn.execute("DELETE FROM mp_beacons WHERE team=?", (row[0],))


def beacon_action(s,req,rsp):
    import private_mp_teams as teams
    row,status = teams.leader(s)
    if not row:return status
    if in_game(row[0]):return pb.StatusCode_MP_TEAM_GAME_STARTED
    team = rsp.team_id = row[0]
    beacon = beacon_for(team)
    before = bool(beacon and beacon.can_start)
    if req.action == pb.MPBeaconAction_PLACE:
        if beacon:return pb.StatusCode_MP_BEACON_DUPLICATED
        if s.scene_id!=9999 or s.dungeon[0]:return pb.StatusCode_NOT_IN_MAIN_CITY
        beacon = pb.MPBeacon(scene_id=s.scene_id,channel_label=s.channel_id)
    elif req.action not in (pb.MPBeaconAction_MOVE,pb.MPBeaconAction_RETRIVE,pb.MPBeaconAction_CALL_IN):
        return pb.StatusCode_BAD_REQ
    elif not beacon:
        return pb.StatusCode_MP_BEACON_NOT_EXIST
    if req.action in (pb.MPBeaconAction_PLACE,pb.MPBeaconAction_MOVE):
        if not req.HasField("position"):return pb.StatusCode_BAD_REQ
        if (s.scene_id,s.channel_id)!=(beacon.scene_id,beacon.channel_label):
            return pb.StatusCode_MP_BEACON_CANNOT_MOVE
        beacon.position.CopyFrom(req.position)
        beacon.rotation.CopyFrom(req.rotation)
        if not nearby(s,beacon):return pb.StatusCode_MP_MEMBERS_NOT_AROUND
        beacon.status = pb.MPBeaconStatus_PLACED
        beacon.members.clear()
    elif req.action == pb.MPBeaconAction_RETRIVE:
        remove_beacon(s,row)
        beacon.status,beacon.can_start = pb.MPBeaconStatus_UNPLACED,False
        rsp.beacon.CopyFrom(beacon)
        return pb.StatusCode_OK
    else:
        if beacon.status!=pb.MPBeaconStatus_PLACED:return pb.StatusCode_MP_BEACON_CANNOT_CALL_IN
        if not nearby(s,beacon):return pb.StatusCode_MP_MEMBERS_NOT_AROUND
        currency,amount=teams.mode_rules(row[3],row[4])["room_cost"]
        _,status=payments(s.player_id,{currency:amount})
        if status!=pb.StatusCode_OK:return status
        beacon.status = pb.MPBeaconStatus_CALLING_IN
    populate(row,beacon)
    write_beacon(team,beacon)
    rsp.beacon.CopyFrom(beacon)
    relay_beacon(s,row,beacon,before)
    return pb.StatusCode_OK


def beacon_ready(s,req,rsp):
    import private_mp_teams as teams
    team = teams.team_for(s.player_id)
    if not team or req.team_id not in (0,team):return pb.StatusCode_MP_NOT_IN_TEAM
    beacon = beacon_for(team)
    if not beacon:return pb.StatusCode_MP_BEACON_NOT_EXIST
    if beacon.status!=pb.MPBeaconStatus_CALLING_IN:return pb.StatusCode_MP_BEACON_CANNOT_START
    if req.is_ready and not nearby(s,beacon):return pb.StatusCode_MP_MEMBERS_NOT_AROUND
    row = teams.team_row(team)
    before = beacon.can_start
    beacon.members[s.player_id] = req.is_ready
    populate(row,beacon)
    write_beacon(team,beacon)
    relay_beacon(s,row,beacon,before)
    return pb.StatusCode_OK


def swap(s,req,rsp):
    import private_mp_teams as teams
    team = teams.team_for(s.player_id)
    if not team:return pb.StatusCode_MP_NOT_IN_TEAM
    if in_game(team):return pb.StatusCode_MP_TEAM_GAME_STARTED
    if not req.character_id or not teams.character(s.player_id,req.character_id):
        return pb.StatusCode_MP_CHARACTER_INVALID
    if dict(teams.roster(team))[s.player_id]==req.character_id:
        return pb.StatusCode_MP_CHARACTER_UNCHANGED
    db.conn.execute("UPDATE private_mp_members SET character=? WHERE player=?", (req.character_id,s.player_id))
    rsp.new_info.character.ParseFromString(db.get_characters(s.player_id,req.character_id)[0])
    teams.member_update(teams.team_row(team))
    return pb.StatusCode_OK


def room_info(state,pid,target):
    target.is_ready = pid in state["ready"]
    target.game_start_time = state["start"]
    if state["mode"]==pb.EMultiplayerPlayMode_DestroyObject:
        hit = state["objects"]
        target.destroy_object.remain_object_list.extend(i for i in range(1,state["entities"]+1) if str(i) not in hit)
    else:
        target.magic_platform.show_platform_list.extend(sorted(state["visible"]))
        target.magic_platform.finished_player_list.extend(map(int,state["finish"]))


def restore_member(s,row,notice):
    beacon = beacon_for(row[0])
    if beacon:
        notice.beacon.CopyFrom(beacon)
        if beacon.mode_info.gameplay_id:
            notice.m_p_team.base.mode_info.gameplay_id=beacon.mode_info.gameplay_id
    state = round_for(row[0])
    if state and s.player_id in state["active"]:
        room_info(state,s.player_id,notice.m_p_room)
        s.commit_callbacks.append((setattr,(s,"mp_room_team",row[0])))


def start(s,req,rsp):
    import private_mp_teams as teams
    row,status = teams.leader(s)
    if not row:return status
    team = row[0]
    if in_game(team):return pb.StatusCode_MP_TEAM_GAME_STARTED
    rules=teams.mode_rules(row[3],row[4])
    beacon=beacon_for(team)
    if not beacon:return pb.StatusCode_MP_BEACON_NOT_PLACED
    if len(beacon.members)<rules["player_min"]:return pb.StatusCode_MP_TEAM_MEMBER_NOT_ENOUGH
    if not beacon.can_start:return pb.StatusCode_MP_TEAM_NOT_READY
    peers = members_present(s,beacon)
    if set(peers)!=set(beacon.members):return pb.StatusCode_MP_MEMBERS_NOT_AROUND
    entries = res.index("TeamGameplay","sub_mode")[row[4]]["sub_mode_items"]
    levels = res.index("ScenesConfigAsset","MiniGameInfos","ID")
    supported = [e["index"] for e in entries if e["index"] in levels]
    if not supported:return pb.StatusCode_CFG_NOT_FOUND
    level=random.choice(supported)
    data=levels[level]
    currency,amount=rules["room_cost"]
    charge,status=payments(s.player_id,{currency:amount})
    if charge is None:return status
    state=dict(mode=rules["new_play_mode"],level=level,entities=data["EntityCount"],active=list(peers),
               players=list(peers),ready=[],start=0,opened=millis(),ended=False,objects={},visible=[1],
               finish={},contacts={},hides={})
    write_round(team,state)
    beacon.mode_info.gameplay_id=level
    beacon.status,beacon.can_start=pb.MPBeaconStatus_STARTED,False
    write_beacon(team,beacon)
    save_pack(s,charge)
    relay_beacon(s,row,beacon,True)
    for pid,peer in peers.items():
        enter=pb.MPRoomEnterNotice(status=pb.StatusCode_OK,game_mode_info=beacon.mode_info)
        room_info(state,pid,enter.room_info)
        peer.send(MsgId.MPRoomEnterNotice,enter,0)
        from private_vehicle_seats import depart as leave_vehicle
        s.commit_callbacks.append((leave_vehicle,(peer,)))
        s.commit_callbacks.append((setattr,(peer,"mp_room_team",team)))
    s.send(MsgId.MPRoomAssignHostNotice,pb.MPRoomAssignHostNotice(status=pb.StatusCode_OK,room_id=team),0)
    rsp.team_id=team
    return pb.StatusCode_OK


def millis():
    return time.time_ns()//1_000_000


def round_members(s):
    import private_mp_teams as teams
    team=teams.team_for(s.player_id)
    state=round_for(team) if team else None
    return team,state if state and s.player_id in state["active"] else None


def send_events(state, events):
    if events:
        import private_mp_teams as teams
        notice=pb.MPPlayRoomEventNotice(status=pb.StatusCode_OK,events=events)
        teams.notify(state["active"],MsgId.MPPlayRoomEventNotice,notice)


def send_event(state,event):
    send_events(state,(event,))


def ready(s,req,rsp):
    team,state=round_members(s)
    if not state:return pb.StatusCode_PLAYER_NOT_IN_MP_ROOM
    if state["start"] or state["ended"]:return pb.StatusCode_MP_TEAM_GAME_STARTED
    if req.ready and s.player_id not in state["ready"]:
        state["ready"].append(s.player_id)
    elif not req.ready and s.player_id in state["ready"]:
        state["ready"].remove(s.player_id)
    if set(state["ready"])==set(state["active"]):
        state["start"]=millis()+1000*config("TeamPlayStartCountDown")[0]
        send_event(state,pb.MPPlayRoomEvent(event_type=pb.MPPlayRoomEvent_GameReady,time=state["start"],
                                            game_start_time=state["start"],play_mode=state["mode"]))
        event=pb.MPPlayRoomEvent(event_type=pb.MPPlayRoomEvent_GameStart,time=state["start"],
                                  game_start_time=state["start"],play_mode=state["mode"])
        send_event(state,event)
    write_round(team,state)
    return pb.StatusCode_OK
from collections import Counter
from datetime import timedelta


def periods():
    from private_shop import now
    day=(now()-timedelta(hours=5)).date()
    return day.isoformat(),(day-timedelta(days=day.weekday())).isoformat()


def allowances(player):
    keys={"d":"GameCredits_DayMax","w":"GameCredits_WeekMax","bd":"GameCredits_DayBonus",
          "bw":"GameCredits_WeekBonus","md":"GameCredits_DayMag","mw":"GameCredits_WeekMag"}
    settings=res.index("GlobalConfig","config","key")
    day,week=periods()
    used=dict(db.conn.execute("SELECT k,CAST(v AS INTEGER) FROM player_kv WHERE player_id=? AND ns='arcade_score' AND k IN (?,?)",
                              (player,"d:"+day,"w:"+week)))
    return {key:max(0,int((settings[name].get("values") or [0])[0])-
                         used.get(key+":"+(week if key.endswith("w") else day),0)) for key,name in keys.items()}


def reward_info(s,req,rsp):
    values=allowances(s.player_id)
    rsp.score_day_remain,rsp.score_week_remain=values["d"],values["w"]
    rsp.extra_bonus_day_remain,rsp.extra_bonus_week_remain=values["bd"],values["bw"]
    rsp.extra_mag_day_remain,rsp.extra_mag_week_remain=values["md"],values["mw"]
    return pb.StatusCode_OK


def award_score(player,stars,settlement,*,units=1):
    from private_gameplay_transaction import GameplayWrite
    from private_shop import fixed_gift
    if not stars:return None
    # National Reward has exactly three arcade-credit prize pools in ascending order.
    # National MiniGameGroup does not export the star-to-pool mapping.
    prizes=fixed_gift(player,6000+stars,db.get_players_info(player,"level")[0])
    if not prizes:return None
    currency=config("GameCredits_ItemId")[0]
    amount=prizes[currency]*units
    quotas=allowances(player)
    # Zero in the native daily score cap disables the cap.
    for key,name in (("d","GameCredits_DayMax"),("w","GameCredits_WeekMax")):
        if config(name)[0]>0:amount=min(amount,quotas[key])
    from private_arcade_wallet import wallet
    amount=min(amount,0xffffffff-wallet(player,currency))
    if not amount:return None
    pending=GameplayWrite(player)
    pending.grant({currency:amount},settlement.rewards)
    pending.balances.flush(pending.pack)
    day,week=periods()
    for key in ("d:"+day,"w:"+week):
        db.conn.execute("""INSERT INTO player_kv VALUES(?,'arcade_score',?,?)
            ON CONFLICT(player_id,ns,k) DO UPDATE SET v=CAST(v AS INTEGER)+excluded.v""",(player,key,amount))
    return pending.pack


def settle(team,state,stamp):
    import private_mp_teams as teams
    if state["ended"]:return
    state["ended"]=True
    state["closed_at"]=stamp
    data=res.index("ScenesConfigAsset","MiniGameInfos","ID")[state["level"]]
    duration=max(0,min(stamp-state["start"],data["Duration"]*1000))
    ranked=pb.MPPlayRoomRankInfo()
    if state["mode"]==pb.EMultiplayerPlayMode_DestroyObject:
        scores=Counter(state["objects"].values())
        for pid in sorted(state["players"],key=lambda p:(-scores[p],p)):
            ranked.destroy_object.add(player_id=pid,count=scores[pid])
        points=len(state["objects"])
    else:
        for pid,elapsed in sorted(state["finish"].items(),key=lambda row:(row[1],int(row[0]))):
            ranked.magic_platform.add(player_id=int(pid),time=elapsed)
        points=len(state["finish"])*100//len(state["players"])
    ranked.star=sum(points>=value for value in data["RewardParams"])
    finish=pb.MPPlayRoomEvent(event_type=pb.MPPlayRoomEvent_GameFinish,time=stamp,
                              play_mode=state["mode"],star=ranked.star,game_start_time=state["start"])
    send_event(state,finish)
    for player in state["active"]:
        notice=pb.MPPlayRoomSettlementNotice(status=pb.StatusCode_OK,gameplay_id=state["level"],
                    play_mode=state["mode"],total_consume_time=duration,rank=ranked)
        pack=award_score(player,ranked.star,notice)
        if pack:teams.notify([player],MsgId.PackNotice,pack)
        teams.notify([player],MsgId.MPPlayRoomSettlementNotice,notice)
    write_round(team,state)


def close(s,team):
    import private_mp_teams as teams
    state=round_for(team)
    if not state:return
    db.conn.execute("DELETE FROM mp_rounds WHERE team=?",(team,))
    teams.notify(state["active"],MsgId.MPRoomDismissNotice,pb.MPRoomDismissNotice(status=pb.StatusCode_OK,team_id=team))
    beacon=beacon_for(team)
    if beacon:
        row=teams.team_row(team)
        beacon.members.clear()
        beacon.mode_info.gameplay_id=0
        beacon.status=pb.MPBeaconStatus_PLACED
        populate(row,beacon)
        write_beacon(team,beacon)
        relay_beacon(s,row,beacon)


def remaining_platforms(state):
    shown={1}
    for contacts in state["contacts"].values():
        for pad in contacts:
            shown.update(i for i in (pad-1,pad,pad+1) if 1<=i<=state["entities"])
    return shown


def platform_event(state,pid,kind,pad,stamp):
    key=str(pid)
    if pad not in state["visible"]:return False
    if kind in (pb.MPPlayRoomEvent_EnterMagicArea,pb.MPPlayRoomEvent_LeaveMagicArea):
        contacts=state["contacts"].setdefault(key,[])
        if kind==pb.MPPlayRoomEvent_EnterMagicArea:
            if pad not in contacts:contacts.append(pad)
            if pad==state["entities"] and key not in state["finish"]:
                state["finish"][key]=max(0,stamp-state["start"])
                send_event(state,pb.MPPlayRoomEvent(event_type=pb.MPPlayRoomEvent_GameFinish,id=pad,
                    player_id=pid,time=stamp,play_mode=state["mode"],game_start_time=state["start"]))
        elif pad in contacts:
            contacts.remove(pad)
        update_platforms(state,stamp)
    return True


def update_platforms(state,stamp):
    shown=remaining_platforms(state)
    for pad in sorted(shown):
        resumed=state["hides"].pop(str(pad),None)
        if pad not in state["visible"] or resumed is not None:
            if pad not in state["visible"]:state["visible"].append(pad)
            send_event(state,pb.MPPlayRoomEvent(event_type=pb.MPPlayRoomEvent_ShowPlatform,id=pad,time=stamp))
    for pad in set(state["visible"])-shown:
        if str(pad) not in state["hides"]:
            state["hides"][str(pad)]=stamp+1000*config("TeamPlayPlatformHideWaitTime")[0]
            send_event(state,pb.MPPlayRoomEvent(event_type=pb.MPPlayRoomEvent_StartHidePlatform,id=pad,time=stamp))


def events(s,req,rsp):
    team,state=round_members(s)
    if not state:return pb.StatusCode_PLAYER_NOT_IN_MP_ROOM
    if not state["start"] or state["ended"]:return pb.StatusCode_MP_NOT_STARTING
    stamp=millis()
    if stamp<state["start"]:return pb.StatusCode_MP_NOT_STARTING
    if not req.events or len(req.events)>state["entities"]:return pb.StatusCode_BAD_REQ
    balloon=state["mode"]==pb.EMultiplayerPlayMode_DestroyObject
    allowed=(pb.MPPlayRoomEvent_DestroyObject,) if balloon else (pb.MPPlayRoomEvent_EnterPlatform,
             pb.MPPlayRoomEvent_LeavePlatform,pb.MPPlayRoomEvent_EnterMagicArea,pb.MPPlayRoomEvent_LeaveMagicArea)
    # Reject the entire batch before publishing notices or updating inventory.
    for ev in req.events:
        if (ev.event_type not in allowed or not 1<=ev.id<=state["entities"] or
                ev.player_id not in (0,s.player_id) or ev.game_start_time not in (0,state["start"]) or
                ev.play_mode not in (0,state["mode"])):
            return pb.StatusCode_BAD_REQ
    level=res.index("ScenesConfigAsset","MiniGameInfos","ID")[state["level"]]
    if stamp>=state["start"]+1000*level["Duration"]:
        settle(team,state,stamp)
        return pb.StatusCode_MP_NOT_STARTING
    if balloon:
        changes=[]
        for ev in req.events:
            if str(ev.id) in state["objects"]:
                continue
            state["objects"][str(ev.id)]=s.player_id
            changes.append(pb.MPPlayRoomEvent(event_type=ev.event_type,id=ev.id,player_id=s.player_id,
                play_mode=state["mode"],time=stamp,count=len(state["objects"])))
        send_events(state,changes)
    else:
        for ev in req.events:
            platform_event(state,s.player_id,ev.event_type,ev.id,stamp)
    if len(state["objects"])==state["entities"] or all(str(pid) in state["finish"] for pid in state["active"]):
        settle(team,state,stamp)
    else:
        write_round(team,state)
    return pb.StatusCode_OK


def tick(s,stamp=None):
    team=getattr(s,"mp_room_team",0)
    if not team:return
    state=round_for(team)
    stamp=millis() if stamp is None else stamp
    if not state:
        s.mp_room_team=0
        return
    if state["ended"]:
        if stamp>=state["closed_at"]+1000*config("TeamGameRestrictJoin")[0]:close(s,team)
        return
    if not state["start"]:
        if stamp>=state["opened"]+1000*config("TeamPlayForceStartWaitTime")[0]:close(s,team)
        return
    duration=res.index("ScenesConfigAsset","MiniGameInfos","ID")[state["level"]]["Duration"]
    if stamp>=state["start"]+1000*duration:
        settle(team,state,stamp)
        return
    due=[pad for pad,expires in state["hides"].items() if expires<=stamp]
    if due:
        for pad in due:
            del state["hides"][pad]
            state["visible"].remove(int(pad))
            send_event(state,pb.MPPlayRoomEvent(event_type=pb.MPPlayRoomEvent_HidePlatform,id=int(pad),time=stamp))
        write_round(team,state)


def leave(s,req,rsp):
    import private_mp_teams as teams
    team,state=round_members(s)
    if not state:return pb.StatusCode_PLAYER_NOT_IN_MP_ROOM
    rsp.team_id=team
    state["active"].remove(s.player_id)
    if s.player_id in state["ready"]:state["ready"].remove(s.player_id)
    state["contacts"].pop(str(s.player_id),None)
    teams.notify(state["active"],MsgId.MPPlayRoomExitNotice,
                 pb.MPPlayRoomExitNotice(status=pb.StatusCode_OK,team_id=team,player_id=s.player_id))
    if not state["active"]:
        close(s,team)
    elif not state["start"]:
        close(s,team)  # A loading player left before the game began.
    else:
        if state["mode"]==pb.EMultiplayerPlayMode_MagicPlatform and not state["ended"]:
            update_platforms(state,millis())
        write_round(team,state)
    return pb.StatusCode_OK


def scene_departed(s,previous):
    import private_mp_teams as teams
    team=teams.team_for(s.player_id)
    beacon=beacon_for(team) if team else None
    if not beacon or (beacon.scene_id,beacon.channel_label)!=previous:return
    row=teams.team_row(team)
    if not in_game(team):
        if row[2]==s.player_id:
            remove_beacon(s,row)
        elif beacon.members.get(s.player_id):
            before=beacon.can_start
            beacon.members[s.player_id]=False
            populate(row,beacon)
            write_beacon(team,beacon)
            relay_beacon(s,row,beacon,before)
