"""Exercise saved Abyss teams, real timed visits, personal rewards and ranking privacy."""
import argparse
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService,pb
from local_identity import LocalIdentity
from test_game_realm import login,join_after_intro
from test_native_gacha import call
from utils import db


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    player=identity.create_profile("深渊测试一")
    other=identity.create_profile("深渊测试二")
    host=LocalGameService(identity,root,resources,port=0)
    seq=count(80)

    def enter(profile):
        sock,_,pid=login(host.port,identity.issue_ticket(*profile))
        join_after_intro(sock)
        return sock,pid

    def ask(sock,name,**args):
        return call(sock,name,next(seq),**args)

    sid,did,aid=9000001,99030001,17010001
    host.start()
    try:
        p,pid=enter(player)
        q,qid=enter(other)
        with p,q:
            assert ask(p,"AbyssInfo").abyss_info.player_id==pid
            assert ask(p,"DungeonOperate").status==pb.StatusCode_PlayerNotInDungeon
            assert ask(p,"PlayerLevelReward",level=1).status==pb.StatusCode_OK
            assert ask(p,"PlayerLevelReward",level=50).status==pb.StatusCode_PLAYER_LEVEL_LIMIT
            assert ask(p,"AbyssQuestReward",season_id=sid,condition_id=aid).status==pb.StatusCode_DungeonCantReward
            assert ask(p,"AbyssTeamUpdate",dungeon_id=did,abyss_team={"char1":9999}).status==pb.StatusCode_BAD_REQ
            assert ask(p,"AbyssTeamUpdate",dungeon_id=did,team_index=1,abyss_team={"char1":101001}).status==pb.StatusCode_BAD_REQ
            assert ask(p,"AbyssTeamUpdate",dungeon_id=did,abyss_team={"char1":101001}).status==pb.StatusCode_OK
            planned=ask(p,"AbyssInfo").abyss_info.season_info[sid].dungeon_info[did]
            assert not planned.finish_time and planned.abyss_teams[0].char1==101001
            assert ask(p,"DungeonEnter",dungeon_id=did,char1=101001).status==pb.StatusCode_OK
            assert ask(p,"DungeonOperate",operate_type=1).status==pb.StatusCode_BAD_REQ
            assert ask(p,"DungeonOperate").status==pb.StatusCode_OK
            timed=ask(p,"DungeonOperate",operate_type=1)
            assert timed.status==pb.StatusCode_OK and 0<timed.consume_time<100000 and timed.star==3
            assert ask(p,"DungeonOperate",operate_type=1)==timed
            assert not ask(q,"AbyssAllServerRank",season_id=sid).rank_list
            assert ask(p,"DungeonFinish").status==pb.StatusCode_OK
            reward=ask(p,"AbyssQuestReward",season_id=sid,condition_id=aid)
            assert reward.status==pb.StatusCode_OK and reward.rewards
            assert ask(p,"AbyssQuestReward",season_id=sid,condition_id=aid).status==pb.StatusCode_REPEAT_GET_REWARD
            assert ask(q,"AbyssQuestReward",season_id=sid,condition_id=aid).status==pb.StatusCode_DungeonCantReward
            assert ask(p,"AbyssQuestReward",season_id=sid,condition_id=17020001).status==pb.StatusCode_OK
            assert ask(p,"DungeonExit").status==pb.StatusCode_OK
            entry=ask(p,"AbyssInfo").abyss_info.season_info[sid].dungeon_info[did]
            assert entry.finish_time==timed.consume_time and entry.abyss_teams[0].char1==101001
            world=ask(q,"AbyssAllServerRank",season_id=sid)
            assert not world.rank_list  # National help 1004 ranks rotating areas, separately per difficulty.
            assert len(ask(q,"AbyssFriendRank",season_id=sid).rank_list)==1
            # Populate archived stage records to check completed-board qualification,
            # challenge exclusion, difficulty isolation and privacy without advancing the live clock.
            with host.lock,db.conn:
                from private_abyss import windows,stages
                stage_ids=[r["dungeon_i_d"] for r in stages(windows()[1]) if not r.get("is_challenge_dungeon")]
                sample=pb.Team(char1=101001).SerializeToString()
                db.conn.executemany("INSERT INTO pc_abyss_scores VALUES(?,?,?,?,?,?,?,NULL)",
                                    [(1,0,pid,x,100,3,sample) for x in stage_ids[:-1]])
            assert not ask(q,"AbyssAllServerRank",season_id=1).rank_list
            with host.lock,db.conn:
                db.conn.execute("INSERT INTO pc_abyss_scores VALUES(?,?,?,?,?,?,?,NULL)",(1,0,pid,stage_ids[-1],100,3,sample))
            assert ask(q,"AbyssAllServerRank",season_id=1).rank_list[0].total_time==100*len(stage_ids)
            assert not ask(q,"AbyssAllServerRank",season_id=1,bonus_type=pb.BONUS_50).rank_list
            with host.lock,db.conn:
                db.set_players_info(pid,"hide_value",pb.HideType_Abyss_Rank_Type)
            assert not ask(q,"AbyssAllServerRank",season_id=1).rank_list
            # Place a second owned character in the test roster to exercise the original switch.
            double=99030009
            with host.lock,db.conn:
                previous=pb.DungeonData(dungeon_id=99030008,finish_times=1)
                db.set_dungeon(pid,previous.dungeon_id,previous.SerializeToString())
                from private_gacha import award
                award(pid,101002,pb.ItemDetail(),pb.PackNotice())
            assert ask(p,"AbyssTeamUpdate",dungeon_id=double,abyss_team={"char1":101001}).status==pb.StatusCode_OK
            assert ask(p,"AbyssTeamUpdate",dungeon_id=double,team_index=1,abyss_team={"char1":101001}).status==pb.StatusCode_BAD_REQ
            assert ask(p,"AbyssTeamUpdate",dungeon_id=double,team_index=1,abyss_team={"char1":101002}).status==pb.StatusCode_OK
            assert ask(p,"DungeonEnter",dungeon_id=double,char1=101001).status==pb.StatusCode_OK
            assert ask(p,"DungeonOperate").status==pb.StatusCode_OK
            half=ask(p,"DungeonOperate",operate_type=1)
            assert half.status==pb.StatusCode_OK and not half.star
            assert ask(p,"DungeonOperate",operate_type=1)==half
            second=ask(p,"AbyssTeamSwitch")
            assert second.status==pb.StatusCode_OK and second.team.char1.char_id==101002
            assert ask(p,"DungeonFinish").status==pb.StatusCode_OK
            assert ask(p,"DungeonOperate",operate_type=1).star==3
        host.stop()
        host.start()
        p,same=enter(player)
        with p:
            assert same==pid
            saved=ask(p,"AbyssInfo").abyss_info.season_info[sid].dungeon_info
            assert saved[did].finish_time==timed.consume_time and len(saved[double].abyss_teams)==2
    finally:
        host.stop()
    print("PASS native Abyss solo/double, timer ordering, seasonal ranks and privacy, receipt, restart and level rewards.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)
