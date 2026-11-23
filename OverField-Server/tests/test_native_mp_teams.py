"""Exercise multiplayer teams with three simultaneous native TCP players and a restored realm."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
import time

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from migrate import backup, restore
from realm_rules import configure
from test_game_dungeon import call
from test_game_realm import join as enter, login, next_msg
from utils import db


def run(base,resources):
    root=base/"realm"
    configure(root,{"mp_team_members":2,"mp_team_name_length":6,"mp_team_limit":1})
    identity=LocalIdentity(root/"identity.sqlite3")
    profiles=[identity.create_profile(n) for n in ("队伍甲","队伍乙","队伍丙")]
    service=LocalGameService(identity,root,resources,port=0)
    def connect(stack,p):
        sock,_,pid=login(service.port,identity.issue_ticket(*profiles[p]))
        stack.enter_context(sock)
        enter(sock)
        return sock,pid
    def create(sock,name,**kwargs):
        fields={"team_name":name,"mode_id":1001,"sub_mode_id":100101,"character_id":101001}
        return call(sock,"MPTeamCreate",**(fields|kwargs))
    def accept(sock,team):
        return call(sock,"MPTeamJoin",team_id=team,character_id=101001)
    service.start()
    try:
        with ExitStack() as live:
            a,uid=connect(live,0);b,buid=connect(live,1);c,cuid=connect(live,2)
            for name,status in (("123",pb.StatusCode_MP_TEAM_NAME_PURE_DIGIT),("长名字超过六个字",pb.StatusCode_MP_TEAM_NAME_TOO_LONG),
                                ("<标签>",pb.StatusCode_MP_TEAM_NAME_INVALID)):
                assert create(a,name).status==status
            assert create(a,"同行",mode_id=9999).status==pb.StatusCode_CFG_NOT_FOUND
            assert create(a,"同行",character_id=2).status==pb.StatusCode_MP_CHARACTER_INVALID
            created=create(a,"同行")
            team=created.team_detail.base.team_id
            assert created.status==pb.StatusCode_OK and team and created.team_detail.base.max_members==2
            assert created.team_detail.members.leader==uid and set(created.team_detail.members.members)=={uid}
            assert create(c,"第二队").status==pb.StatusCode_MP_TEAM_CANNOT_JOIN
            assert accept(c,team).status==pb.StatusCode_MP_TEAM_CANNOT_JOIN
            assert call(c,"MPTeamInfo",team_id=team).status==pb.StatusCode_MP_NOT_IN_TEAM
            assert call(a,"MPTeamInvite",target_id=buid).status==pb.StatusCode_OK
            invite=next_msg(b,MsgId.MPTeamInviteNotice,pb.MPTeamInviteNotice)
            assert invite.inviter==uid and invite.team_base.team_id==team
            with service.lock,db.conn:
                db.conn.execute("UPDATE private_mp_invites SET expires=0 WHERE team=?",(team,))
            assert accept(b,team).status==pb.StatusCode_MP_TEAM_CANNOT_JOIN
            # Two valid invitations race for the same final position.
            for target in (buid,cuid):
                assert call(a,"MPTeamInvite",target_id=target).status==pb.StatusCode_OK
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda sock:accept(sock,team),(b,c)))
            assert sorted([r.status for r in results])==sorted([pb.StatusCode_OK,pb.StatusCode_MP_TEAM_FULL])
            winner=1 if results[0].status==pb.StatusCode_OK else 2
            member,mid,visitor,vid=(b,buid,c,cuid) if winner==1 else (c,cuid,b,buid)
            before=call(member,"MPTeamInfo",team_id=team).team_detail
            assert set(before.members.members)=={uid,mid}
            assert call(member,"MPTeamDismiss").status==pb.StatusCode_MP_TEAM_NOT_LEADER
            assert call(member,"MPTeamKickMember",target_id=uid).status==pb.StatusCode_MP_TEAM_NOT_LEADER
            assert call(member,"MPTeamRename",new_name="越权").status==pb.StatusCode_MP_TEAM_NOT_LEADER
            assert call(a,"MPTeamRename",new_name="花园同行").status==pb.StatusCode_OK
            assert next_msg(member,MsgId.MPTeamRenameNotice,pb.MPTeamRenameNotice).team_name=="花园同行"
            assert call(a,"MPTeamGameModeChange",mode_id=1002,sub_mode_id=100201).status==pb.StatusCode_OK
            assert call(member,"MPTeamInfo",team_id=0).team_detail.base.mode_info.game_mode==2
            assert call(a,"MPTeamKickMember",target_id=uid).status==pb.StatusCode_BAD_REQ
            assert call(a,"MPTeamKickMember",target_id=mid).target_id==mid
            assert next_msg(member,MsgId.MPTeamPlayerKickedNotice,pb.MPTeamPlayerKickedNotice).team_id==team
            assert call(a,"MPTeamInvite",target_id=mid).status==pb.StatusCode_MP_IN_TEAM_KICK_CD
            assert accept(visitor,team).status==pb.StatusCode_OK
            assert call(a,"MPTeamQuit").team_id==team
            assert call(visitor,"MPTeamInfo").team_detail.members.leader==vid
    finally:
        service.stop()
    archive=backup(root,base/"teams.zip")
    restored=restore(archive,base/"restored")
    identity=LocalIdentity(restored/"identity.sqlite3")
    service=LocalGameService(identity,restored,resources,port=0)
    service.start()
    try:
        with ExitStack() as live:
            captain,vid=connect(live,3-winner)
            restored_notice=next_msg(captain,MsgId.PlayerMPGameNotice,pb.PlayerMPGameNotice)
            assert restored_notice.m_p_team.base.team_id==team and restored_notice.m_p_team.members.leader==vid
            assert restored_notice.m_p_team.base.team_name=="花园同行"
            a,uid=connect(live,0)
            assert call(captain,"MPTeamInvite",target_id=uid).status==pb.StatusCode_OK
            assert accept(a,team).status==pb.StatusCode_OK
            assert call(captain,"MPTeamDismiss").status==pb.StatusCode_OK
            assert next_msg(a,MsgId.MPTeamDismissNotice,pb.MPTeamDismissNotice).team_id==team
            assert call(a,"MPTeamInfo").status==pb.StatusCode_MP_NOT_IN_TEAM
            fresh=create(a,"新队")
            assert fresh.team_detail.base.team_id>team
            assert call(a,"MPTeamQuit").status==pb.StatusCode_OK
            for table in ("private_mp_teams","private_mp_members","private_mp_invites","private_mp_kicks"):
                assert db.conn.execute("SELECT count(*) FROM "+table).fetchone()==(0,)
    finally:
        service.stop()
    print("PASS 3 native TCP players, invitations, capacity race, member and leader permissions, migration and login notices.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as temp:
        run(Path(temp),parser.parse_args().resources)

