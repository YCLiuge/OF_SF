"""Native incapacitation state for world parties, individual dungeon visits and MP rooms."""
from network.msg_id import MsgId
from private_dungeons import _table, config, load, progress, report
from proto import net_pb2 as pb
from utils import db


def setup():
    db.conn.execute("""CREATE TABLE IF NOT EXISTS pc_downed_characters(
        player INTEGER, scope INTEGER, visit INTEGER, actor INTEGER,
        PRIMARY KEY(player,scope,visit,actor)) WITHOUT ROWID""")
    db.conn.execute("""CREATE TABLE IF NOT EXISTS pc_dungeon_revivals(
        player INTEGER,dungeon INTEGER,visit INTEGER,used INTEGER NOT NULL,
        PRIMARY KEY(player,dungeon)) WITHOUT ROWID""")


def actors(team):
    return (team.char1,team.char2,team.char3)


def apply(player,team,scope,visit):
    down = {a for a, in db.conn.execute("""SELECT actor FROM pc_downed_characters
        WHERE player=? AND scope=? AND visit=?""",(player,scope,visit))}
    for actor in actors(team):
        if actor.char_id:
            actor.is_dead = actor.char_id in down


def start_visit(player,dungeon,visit):
    db.conn.execute("DELETE FROM pc_downed_characters WHERE player=? AND scope=? AND visit<>?",(player,dungeon,visit))


def members(player,did,roster):
    split = db.conn.execute("""SELECT teams.team FROM pc_dungeon_clock AS clock JOIN pc_abyss_teams AS teams
        ON teams.player=clock.player AND teams.dungeon=clock.dungeon AND teams.mode=clock.mode AND teams.slot=1
        WHERE clock.player=? AND clock.dungeon=? AND clock.visit=? AND clock.first_half<0""",
                            (player,did,roster.enter_times)).fetchone()
    team = pb.Team.FromString(split[0]) if split else roster
    return (team.char1,team.char2,team.char3)


_goals = None


def objectives():
    global _goals
    table = _table("Achieve","achieve")
    groups = _table("AchieveQuest","achieve_quest_group")
    if _goals is None or _goals[0] is not table or _goals[1] is not groups:
        published = {entry["achieve_condition_i_d"] for group in groups.values()
                     for entry in group.get("achieve_quest_group_info",())}
        _goals = table,groups,[(a,row["count_param"]) for a,row in table.items()
                               if a in published and row.get("new_achieve_condition")==131 and not row.get("param")]
    return _goals[2]


def context(session):
    pid,did=session.player_id,session.dungeon[0]
    if did:
        data=load(pid,did)
        return did,session.dungeon_visit,members(pid,did,data)
    party=tuple(a.char_id for a in actors(session.scene_player.team))
    room=getattr(session,"mp_room_team",0)
    if room:
        from private_mp_rooms import round_for
        active=round_for(room)
        if active and pid in active["active"]:
            return -1,active["opened"],party
    return 0,0,party


def notify(session,scope,visit,party,actor,down):
    notice=pb.ServerSceneSyncDataNotice(status=pb.StatusCode_OK)
    entry=notice.data.add(player_id=session.player_id).server_data.add(action_type=pb.SceneActionType_UPDATE_CHARACTER_DEAD)
    view=entry.player
    view.CopyFrom(session.scene_player)
    if scope>0:
        # This is a solo instance even if another owner is playing the same dungeon template.
        view.ClearField("team")
        from utils.pb_create import make_SceneTeam
        make_SceneTeam(session.player_id,party,view.team,life_scope=(scope,visit))
        session.send(MsgId.ServerSceneSyncDataNotice,notice,0)
        return
    apply(session.player_id,view.team,scope,visit)
    group=session.social_rooms.get((session.scene_id,session.channel_id),())
    room=getattr(session,"mp_room_team",0)
    for peer in group:
        if not peer.dungeon[0] and getattr(peer,"mp_room_team",0)==room:
            peer.send(MsgId.ServerSceneSyncDataNotice,notice,0)
    if not scope:
        member=next(a for a in actors(session.scene_player.team) if a.char_id==actor)
        session.commit_callbacks.append((setattr,(member,"is_dead",down)))


def change(session,actor,down):
    pid=session.player_id
    scope,visit,party=context(session)
    if not actor or actor not in party:
        return pb.StatusCode_BAD_REQ
    key=pid,scope,visit,actor
    was_down=db.conn.execute("""SELECT 1 FROM pc_downed_characters WHERE
        player=? AND scope=? AND visit=? AND actor=?""",key).fetchone() is not None
    if down==was_down:
        return pb.StatusCode_OK
    if down:
        if scope<0:
            start_visit(pid,scope,visit)
        db.conn.execute("INSERT INTO pc_downed_characters VALUES(?,?,?,?)",key)
        report(session,[(aid,min(limit,progress(pid,aid)+1)) for aid,limit in objectives()])
    else:
        if scope>0:
            limit=config(scope).get("respawn_times",0)
            prior=db.conn.execute("SELECT used FROM pc_dungeon_revivals WHERE player=? AND dungeon=? AND visit=?",
                                  (pid,scope,visit)).fetchone()
            used=prior[0] if prior else 0
            if used>=limit:
                return pb.StatusCode_FAIL
            db.conn.execute("INSERT OR REPLACE INTO pc_dungeon_revivals VALUES(?,?,?,?)",(pid,scope,visit,used+1))
        db.conn.execute("DELETE FROM pc_downed_characters WHERE player=? AND scope=? AND visit=? AND actor=?",key)
    notify(session,scope,visit,party,actor,down)
    return pb.StatusCode_OK


def dead(session,req,rsp):
    return change(session,req.character_id,True)


def revive(session,req,rsp):
    return change(session,req.character_id,False)
