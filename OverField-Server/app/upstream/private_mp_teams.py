"""Same-realm native multiplayer team membership with SQLite invitations and role checks."""
import time
import unicodedata

from network.msg_id import MsgId
from utils.res_loader import res
from private_friends import state as friend_state
from proto import net_pb2 as pb
from server.scene_data import get_session, online_session
from utils import db
from utils.pb_create import make_PlayerBriefInfo
import private_mp_rooms as rooms


def initialize():
    rooms.initialize()
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS private_mp_teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, leader INTEGER NOT NULL,
            mode INTEGER NOT NULL, sub_mode INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS private_mp_members (
            joined INTEGER PRIMARY KEY AUTOINCREMENT, player INTEGER NOT NULL UNIQUE, team INTEGER NOT NULL,
            character INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS mp_members_team ON private_mp_members(team,joined);
        CREATE TABLE IF NOT EXISTS private_mp_invites (
            team INTEGER NOT NULL, player INTEGER NOT NULL, inviter INTEGER NOT NULL, expires INTEGER NOT NULL,
            PRIMARY KEY(team,player)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS private_mp_kicks (
            team INTEGER NOT NULL, player INTEGER NOT NULL, expires INTEGER NOT NULL,
            PRIMARY KEY(team,player)) WITHOUT ROWID;
    """)


def team_for(player):
    row=db.conn.execute("SELECT team FROM private_mp_members WHERE player=?",(player,)).fetchone()
    return row[0] if row else 0


def team_row(team):
    return db.conn.execute("SELECT id,name,leader,mode,sub_mode FROM private_mp_teams WHERE id=?",(team,)).fetchone()


def roster(team):
    return db.conn.execute("SELECT player,character FROM private_mp_members WHERE team=? ORDER BY joined",(team,)).fetchall()


def mode_rules(mode,sub):
    options=res.index("TeamGameplay","mode").get(mode,{}).get("mode_items",[])
    if not sub and options:
        return options[0]
    return next((row for row in options if row["sub_mode_id"]==sub),None)


def checked_name(session,text,default=False):
    maximum=min(session.realm_rules.mp_team_name_length,
                int(res.index("GlobalConfig","config","key")["TeamNameMaxLen"]["values"][0]))
    text=unicodedata.normalize("NFC",text).strip()
    if not text and default:
        text=(session.player_name+"的队伍")[:maximum]
    if not text:
        return "",pb.StatusCode_MP_TEAM_NAME_EMPTY
    if len(text)>maximum:
        return "",pb.StatusCode_MP_TEAM_NAME_TOO_LONG
    if text.isdecimal():
        return "",pb.StatusCode_MP_TEAM_NAME_PURE_DIGIT
    if any(unicodedata.category(c)[0]=="C" or c in "<>" for c in text):
        return "",pb.StatusCode_MP_TEAM_NAME_INVALID
    return text,pb.StatusCode_OK


def character(player,requested):
    if not requested:
        requested=db.get_players_info(player,"character_id")[0]
    return requested if requested and db.get_characters(player,requested) else 0


def fill_base(session,base,row,members):
    team,name,_,mode,sub=row
    options=mode_rules(mode,sub)
    base.team_id=team
    base.team_name=name
    base.current_members=len(members)
    base.max_members=max(len(members),min(session.realm_rules.mp_team_members,options["player_max"]))
    base.mode_info.mode_id=mode
    base.mode_info.sub_mode_id=sub
    base.mode_info.game_mode=options.get("new_play_mode",0)


def fill_members(target,leader,members):
    target.leader=leader
    for pid,avatar in members:
        entry=target.members[pid]
        entry.player_id=pid
        make_PlayerBriefInfo(pid,entry.player_brief)
        entry.player_info.character.ParseFromString(db.get_characters(pid,avatar)[0])


def detail(session,target,row,members):
    fill_base(session,target.base,row,members)
    fill_members(target.members,row[2],members)


def notify(players,cmd,notice):
    targets=set(players)
    for peer in get_session():
        if peer.player_id in targets:
            peer.send(cmd,notice,0)


def member_update(row):
    members=roster(row[0])
    update=pb.MPTeamMemberNotice(status=pb.StatusCode_OK,team_id=row[0])
    fill_members(update.members,row[2],members)
    notify((pid for pid,_ in members),MsgId.MPTeamMemberNotice,update)


def create(session,req,rsp):
    player=session.player_id
    if team_for(player):
        return pb.StatusCode_MP_ALREADY_IN_TEAM
    if session.realm_rules.mp_team_limit and db.conn.execute("SELECT COUNT(*) FROM private_mp_teams").fetchone()[0]>=session.realm_rules.mp_team_limit:
        return pb.StatusCode_MP_TEAM_CANNOT_JOIN
    name,status=checked_name(session,req.team_name,default=True)
    if status!=pb.StatusCode_OK:return status
    rules=mode_rules(req.mode_id,req.sub_mode_id)
    if not rules:return pb.StatusCode_CFG_NOT_FOUND
    avatar=character(player,req.character_id)
    if not avatar:return pb.StatusCode_MP_CHARACTER_INVALID
    team=db.conn.execute("INSERT INTO private_mp_teams(name,leader,mode,sub_mode) VALUES(?,?,?,?)",
                         (name,player,req.mode_id,rules["sub_mode_id"])).lastrowid
    db.conn.execute("INSERT INTO private_mp_members(player,team,character) VALUES(?,?,?)",(player,team,avatar))
    db.conn.execute("DELETE FROM private_mp_invites WHERE player=?",(player,))
    detail(session,rsp.team_detail,team_row(team),roster(team))
    return pb.StatusCode_OK


def info(session,req,rsp):
    team=team_for(session.player_id)
    if not team or (req.team_id and req.team_id!=team):
        return pb.StatusCode_MP_NOT_IN_TEAM
    detail(session,rsp.team_detail,team_row(team),roster(team))
    return pb.StatusCode_OK


def blocked(first,second):
    return pb.FriendStatus_BLACK in (friend_state(first,second),friend_state(second,first))


def invite(session,req,rsp):
    player,target=session.player_id,req.target_id
    row=team_row(team_for(player))
    if not row:return pb.StatusCode_MP_INVITER_NOT_IN_TEAM
    if rooms.in_game(row[0]):return pb.StatusCode_MP_TEAM_GAME_STARTED
    if team_for(target):return pb.StatusCode_MP_INVITEE_IN_TEAM
    target_session=online_session(target)
    if target_session is None:return pb.StatusCode_MP_TEAM_CANNOT_JOIN
    if blocked(player,target) or blocked(row[2],target):return pb.StatusCode_MP_LEADER_BLOCKED
    if (target_session.scene_id,target_session.channel_id)!=(session.scene_id,session.channel_id):
        return pb.StatusCode_MP_INVITEE_NOT_SAME_CHANNEL
    now=int(time.time())
    if db.conn.execute("SELECT 1 FROM private_mp_kicks WHERE team=? AND player=? AND expires>?",(row[0],target,now)).fetchone():
        return pb.StatusCode_MP_IN_TEAM_KICK_CD
    members=roster(row[0])
    notice=pb.MPTeamInviteNotice(status=pb.StatusCode_OK,inviter=player)
    fill_base(session,notice.team_base,row,members)
    if len(members)>=notice.team_base.max_members:return pb.StatusCode_MP_TEAM_FULL
    db.conn.execute("DELETE FROM private_mp_invites WHERE expires<=?",(now,))
    db.conn.execute("INSERT OR REPLACE INTO private_mp_invites VALUES(?,?,?,?)",(row[0],target,player,now+120))
    target_session.send(MsgId.MPTeamInviteNotice,notice,0)
    return pb.StatusCode_OK


def join(session,req,rsp):
    player=session.player_id
    if team_for(player):return pb.StatusCode_MP_ALREADY_IN_TEAM
    row=team_row(req.team_id)
    if not row:return pb.StatusCode_MP_TEAM_NOT_EXIST
    if rooms.in_game(row[0]):return pb.StatusCode_MP_TEAM_GAME_STARTED
    invitation=db.conn.execute("""SELECT inviter FROM private_mp_invites i JOIN private_mp_members m
        ON i.inviter=m.player AND i.team=m.team WHERE i.team=? AND i.player=? AND i.expires>?""",
        (row[0],player,int(time.time()))).fetchone()
    if not invitation:
        beacon=rooms.beacon_for(row[0])
        if not beacon or (beacon.scene_id,beacon.channel_label)!=(session.scene_id,session.channel_id):
            return pb.StatusCode_MP_TEAM_CANNOT_JOIN
        if db.conn.execute("SELECT 1 FROM private_mp_kicks WHERE team=? AND player=? AND expires>?",
                           (row[0],player,int(time.time()))).fetchone():
            return pb.StatusCode_MP_IN_TEAM_KICK_CD
    if blocked(row[2],player) or (invitation and blocked(invitation[0],player)):return pb.StatusCode_MP_LEADER_BLOCKED
    members=roster(row[0])
    limit=min(session.realm_rules.mp_team_members,mode_rules(row[3],row[4])["player_max"])
    if len(members)>=limit:return pb.StatusCode_MP_TEAM_FULL
    avatar=character(player,req.character_id)
    if not avatar:return pb.StatusCode_MP_CHARACTER_INVALID
    db.conn.execute("INSERT INTO private_mp_members(player,team,character) VALUES(?,?,?)",(player,row[0],avatar))
    db.conn.execute("DELETE FROM private_mp_invites WHERE player=?",(player,))
    detail(session,rsp.team_detail,row,roster(row[0]))
    member_update(row)
    rooms.roster_changed(session, row)
    return pb.StatusCode_OK


def remove_member(team,player):
    db.conn.execute("DELETE FROM private_mp_members WHERE team=? AND player=?",(team,player))
    db.conn.execute("DELETE FROM private_mp_invites WHERE team=? AND (player=? OR inviter=?)",(team,player,player))


def remove_team(team):
    for name in ("private_mp_invites","private_mp_kicks","private_mp_members"):
        db.conn.execute("DELETE FROM "+name+" WHERE team=?",(team,))
    db.conn.execute("DELETE FROM private_mp_teams WHERE id=?",(team,))


def quit(session,req,rsp):
    player=session.player_id
    row=team_row(team_for(player))
    if not row:return pb.StatusCode_MP_NOT_IN_TEAM
    team=row[0]
    if rooms.in_game(team):return pb.StatusCode_MP_TEAM_GAME_STARTED
    if row[2]==player:
        rooms.remove_beacon(session,row)
    remove_member(team,player)
    members=roster(team)
    if not members:
        remove_team(team)
    else:
        if row[2]==player:
            db.conn.execute("UPDATE private_mp_teams SET leader=? WHERE id=?",(members[0][0],team))
        updated=team_row(team)
        member_update(updated)
        rooms.roster_changed(session,updated)
    rsp.team_id=team
    return pb.StatusCode_OK


def leader(session):
    row=team_row(team_for(session.player_id))
    if not row:return None,pb.StatusCode_MP_NOT_IN_TEAM
    if row[2]!=session.player_id:return None,pb.StatusCode_MP_TEAM_NOT_LEADER
    return row,pb.StatusCode_OK


def kick(session,req,rsp):
    row,status=leader(session)
    if not row:return status
    target=req.target_id
    if rooms.in_game(row[0]):return pb.StatusCode_MP_TEAM_GAME_STARTED
    if target==session.player_id:return pb.StatusCode_BAD_REQ
    if team_for(target)!=row[0]:return pb.StatusCode_PLAYER_NOT_IN_MP_TEAM
    team=row[0]
    remove_member(team,target)
    seconds=int(res.index("GlobalConfig","config","key")["TeamKickRejoinCD"]["values"][0])
    db.conn.execute("INSERT OR REPLACE INTO private_mp_kicks VALUES(?,?,?)",(team,target,int(time.time())+seconds))
    notify([target],MsgId.MPTeamPlayerKickedNotice,pb.MPTeamPlayerKickedNotice(status=pb.StatusCode_OK,team_id=team))
    member_update(row)
    rooms.roster_changed(session,row)
    rsp.target_id=target
    return pb.StatusCode_OK


def dismiss(session,req,rsp):
    row,status=leader(session)
    if not row:return status
    team=row[0]
    if rooms.in_game(team):return pb.StatusCode_MP_TEAM_GAME_STARTED
    peers=[pid for pid,_ in roster(team) if pid!=session.player_id]
    rooms.remove_beacon(session,row)
    remove_team(team)
    notify(peers,MsgId.MPTeamDismissNotice,pb.MPTeamDismissNotice(status=pb.StatusCode_OK,team_id=team))
    return pb.StatusCode_OK


def rename(session,req,rsp):
    row,status=leader(session)
    if not row:return status
    name,status=checked_name(session,req.new_name)
    if status!=pb.StatusCode_OK:return status
    if name==row[1]:return pb.StatusCode_MP_TEAM_NAME_UNCHANGED
    db.conn.execute("UPDATE private_mp_teams SET name=? WHERE id=?",(name,row[0]))
    rooms.roster_changed(session,team_row(row[0]))
    rsp.team_id=row[0]
    rsp.new_name=name
    notify((pid for pid,_ in roster(row[0]) if pid!=session.player_id),MsgId.MPTeamRenameNotice,
           pb.MPTeamRenameNotice(status=pb.StatusCode_OK,team_id=row[0],team_name=name))
    return pb.StatusCode_OK


def change_mode(session,req,rsp):
    row,status=leader(session)
    if not row:return status
    if rooms.in_game(row[0]):return pb.StatusCode_MP_TEAM_GAME_STARTED
    options=mode_rules(req.mode_id,req.sub_mode_id)
    if not options:return pb.StatusCode_CFG_NOT_FOUND
    if (req.mode_id,options["sub_mode_id"])==row[3:]:return pb.StatusCode_MP_TEAM_UNCHANGED
    members=roster(row[0])
    if len(members)>options["player_max"]:return pb.StatusCode_MP_CANNOT_CHANGE_MODE
    db.conn.execute("UPDATE private_mp_teams SET mode=?, sub_mode=? WHERE id=?",(req.mode_id,options["sub_mode_id"],row[0]))
    rsp.team_id=row[0]
    rsp.game_mode.mode_id=req.mode_id
    rsp.game_mode.sub_mode_id=options["sub_mode_id"]
    rsp.game_mode.game_mode=options.get("new_play_mode",0)
    rooms.remove_beacon(session,row)
    update=pb.MPTeamGameModeChangeNotice(status=pb.StatusCode_OK,team_id=row[0],game_mode=rsp.game_mode)
    notify((pid for pid,_ in members if pid!=session.player_id),MsgId.MPTeamGameModeChangeNotice,update)
    return pb.StatusCode_OK


def on_login(session):
    row=team_row(team_for(session.player_id))
    if row:
        notice=pb.PlayerMPGameNotice(status=pb.StatusCode_OK)
        detail(session,notice.m_p_team,row,roster(row[0]))
        rooms.restore_member(session,row,notice)
        session.send(MsgId.PlayerMPGameNotice,notice,0)
