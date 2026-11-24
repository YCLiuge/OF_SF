"""Native party deaths, peer visibility, capped dungeon revivals and both Abyss rosters."""
import argparse
from contextlib import ExitStack
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService,MsgId,pb
from local_identity import LocalIdentity
from private_dungeons import progress
from test_game_realm import login,join_after_intro
from test_native_events import NOTICES,rpc
from utils import db


def run(root,resources):
    roles=LocalIdentity(root/"identity.sqlite3")
    slots=[roles.create_profile(n) for n in ("倒地队长","同频道队长","另一频道队长")]
    server=LocalGameService(roles,root,resources,port=0)
    seq=count(200)
    hero,bench=101001,101002
    NOTICES[MsgId.ServerSceneSyncDataNotice]=pb.ServerSceneSyncDataNotice

    def ask(sock,kind,**fields):
        return rpc(sock,kind,next(seq),**fields)[0]

    def actions(frames):
        return [action.player.team for frame in frames if isinstance(frame,pb.ServerSceneSyncDataNotice)
                for row in frame.data for action in row.server_data if action.action_type==pb.SceneActionType_UPDATE_CHARACTER_DEAD]

    def fence(sock):
        return actions(rpc(sock,"GetWeapon",next(seq))[1])

    server.start()
    try:
        with ExitStack() as opened:
            players=[]
            for ident in slots:
                sock,_,pid=login(server.port,roles.issue_ticket(*ident))
                opened.enter_context(sock)
                join_after_intro(sock)
                players.append((sock,pid))
            (a,pid),(peer,other),(third,_)=players
            assert ask(third,"ChangeSceneChannel",scene_id=9999,channel_label=2).status==pb.StatusCode_OK
            with server.lock,db.conn:
                db.add_character(pid,bench)
            assert ask(a,"CharacterDead",character_id=bench).status==pb.StatusCode_BAD_REQ
            assert ask(a,"CharacterDead",character_id=0).status==pb.StatusCode_BAD_REQ
            key=next(seq)
            died,frames=rpc(a,"CharacterDead",key,character_id=hero)
            assert died.status==pb.StatusCode_OK and actions(frames)[-1].char1.is_dead
            assert rpc(a,"CharacterDead",key,character_id=hero)[0]==died
            assert ask(a,"CharacterDead",character_id=hero)==died
            assert fence(peer)[-1].char1.is_dead and not fence(third)
            with server.lock: assert progress(pid,60010031)==1 and progress(pid,60020031)==1 and progress(other,60010031)==0
            assert ask(a,"GetOneAchieveAward",achieve_id=60010031).status==pb.StatusCode_OK
            assert ask(a,"GetOneAchieveAward",achieve_id=60010031).status != pb.StatusCode_OK
            assert ask(peer,"GetOneAchieveAward",achieve_id=60010031).status==pb.StatusCode_FAIL
            assert ask(a,"UpdateTeam",char1=bench,char2=hero).status==pb.StatusCode_OK
            with server.lock:
                s=next(s for s in server.scene_members[(9999,1)] if s.player_id==pid)
                assert s.scene_player.team.char2.is_dead and not s.scene_player.team.char1.is_dead
            assert ask(a,"CharacterRevive",character_id=hero).status==pb.StatusCode_OK
            assert not fence(peer)[-1].char2.is_dead

            did=11101
            assert ask(a,"DungeonEnter",dungeon_id=did,char1=hero,char2=bench).status==pb.StatusCode_OK
            assert ask(peer,"DungeonEnter",dungeon_id=did,char1=hero).status==pb.StatusCode_OK
            for i in range(3):
                actor=(hero,bench)[i%2]
                assert ask(a,"CharacterDead",character_id=actor).status==pb.StatusCode_OK
                assert ask(a,"CharacterRevive",character_id=actor).status==pb.StatusCode_OK
            assert ask(a,"CharacterDead",character_id=hero).status==pb.StatusCode_OK
            assert ask(a,"CharacterRevive",character_id=hero).status==pb.StatusCode_FAIL
            again=ask(a,"DungeonEnter",dungeon_id=did,char1=hero,char2=bench)
            assert again.team.char1.is_dead and not again.team.char2.is_dead
            assert not fence(peer) and not fence(third)
            assert ask(peer,"CharacterDead",character_id=hero).status==pb.StatusCode_OK
            assert ask(peer,"CharacterRevive",character_id=hero).status==pb.StatusCode_OK
            again=ask(a,"DungeonRepeatEnter",char1=hero,char2=bench)
            assert again.status==pb.StatusCode_OK and not again.team.char1.is_dead
            assert ask(a,"CharacterDead",character_id=hero).status==pb.StatusCode_OK
            assert ask(a,"CharacterRevive",character_id=hero).status==pb.StatusCode_OK
            assert ask(a,"DungeonExit").status==pb.StatusCode_OK
            double=99030009
            with server.lock,db.conn:
                previous=pb.DungeonData(dungeon_id=99030008,finish_times=1)
                db.set_dungeon(pid,previous.dungeon_id,previous.SerializeToString())
                db.set_players_info(pid,"level",100)
            assert ask(a,"AbyssTeamUpdate",dungeon_id=double,abyss_team={"char1":hero}).status==pb.StatusCode_OK
            assert ask(a,"AbyssTeamUpdate",dungeon_id=double,team_index=1,abyss_team={"char1":bench}).status==pb.StatusCode_OK
            assert ask(a,"DungeonEnter",dungeon_id=double,char1=hero).status==pb.StatusCode_OK
            assert ask(a,"DungeonOperate").status==pb.StatusCode_OK
            assert ask(a,"CharacterDead",character_id=bench).status==pb.StatusCode_BAD_REQ
            assert ask(a,"DungeonOperate",operate_type=1).status==pb.StatusCode_OK
            assert ask(a,"AbyssTeamSwitch").team.char1.char_id==bench
            assert ask(a,"CharacterDead",character_id=hero).status==pb.StatusCode_BAD_REQ
            assert ask(a,"CharacterDead",character_id=bench).status==pb.StatusCode_OK
            second=ask(a,"AbyssTeamSwitch")
            assert second.team.char1.char_id==bench and second.team.char1.is_dead
            restored=ask(a,"DungeonEnter",dungeon_id=double,char1=hero)
            assert restored.team==second.team or (restored.team.char1.char_id==bench and restored.team.char1.is_dead)
            assert ask(a,"DungeonExit").status==pb.StatusCode_OK
            with server.lock,db.conn:
                db.set_achieve(pid,60020031,pb.Achieve(achieve_id=60020031,count=99).SerializeToString())
            assert ask(a,"CharacterDead",character_id=hero).status==pb.StatusCode_OK
            assert ask(a,"GetOneAchieveAward",achieve_id=60020031).status==pb.StatusCode_OK
    finally:
        server.stop()
    server.start()
    try:
        a,_,pid=login(server.port,roles.issue_ticket(*slots[0]))
        with a:
            _,world=join_after_intro(a)
            own=next(x for x in world.data.players if x.team.char2.char_id==hero)
            assert own.team.char2.is_dead
            assert ask(a,"CharacterRevive",character_id=hero).status==pb.StatusCode_OK
            with server.lock: assert progress(pid,60020031)==100
    finally:
        server.stop()
    print("PASS public downed snapshots, peer isolation, two claimable achievements, retries, three revivals per visit, Abyss party switch and restart.")


if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),cli.parse_args().resources)
