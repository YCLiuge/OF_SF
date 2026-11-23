"""Real native Boss Rush sessions, battle restarts, best teams, scoped rankings and rewards."""
import argparse
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService,pb
from local_identity import LocalIdentity
from private_dungeons import _table,reward_items
from private_shop import CN,balance
from test_game_realm import join,login
from test_observed_initialization import call
from utils import db


def run(state,resources):
    identity=LocalIdentity(state/"identity.sqlite3")
    profiles=[identity.create_profile(n) for n in ("联合甲","联合乙","联合丙")]
    game=LocalGameService(identity,state,resources,port=0)
    clock=[int(datetime(2025,8,29,8,tzinfo=CN).timestamp())*1000]
    sid=1001; fields={"season_id":sid}; ok=pb.StatusCode_OK
    def connect(n):
        s,_,pid=login(game.port,identity.issue_ticket(*profiles[n]))
        join(s)
        return s,pid
    def count(pid,key):
        with game.lock:
            item=balance(pid,key)
            return item.main_item.base_item.num if item else 0
    def start(sock,index):return call(sock,"BossRushStartChallenge",stage_index=index,**fields)
    def enter(sock,*ids):return call(sock,"BossRushEnterStage",characters=ids,**fields)
    def battle(sock):return call(sock,"BossRushStartBattle",**fields)
    def leave(sock,n):return call(sock,"BossRushLeaveStage",total_damage_hp=n,**fields)
    def claim(sock):return call(sock,"BossRushQuestReward",achieve_id=18100101,**fields)
    def ranked(sock):return call(sock,"BossRushFriendRank",**fields)
    def detail(sock,pid):return call(sock,"BossRushFriendDetail",player_id=pid)
    def close_run(sock):return call(sock,"BossRushTerminateChallenge",**fields)
    with patch("private_boss_rush.now_ms",side_effect=lambda:clock[0]):
        game.start()
        try:
            a,owner=connect(0);b,friend=connect(1);c,stranger=connect(2)
            with a,b,c:
                available=count(owner,131)
                assert call(a,"BossRushInfo").info.season_id==sid
                assert start(a,1).status==pb.StatusCode_Activity_Close
                assert start(a,-1).status!=ok and start(a,100).status!=ok
                assert enter(a,101001).status!=ok and battle(a).status!=ok and leave(a,300000).status!=ok
                assert claim(a).status!=ok
                assert start(a,0).status==ok and start(a,0).status==ok and count(owner,131)==available-1
                assert enter(a).status!=ok and enter(a,101001,101001).status!=ok and enter(a,99999).status!=ok
                assert enter(a,101001).status==ok and enter(a,101001).status==ok
                begun=battle(a)
                assert begun.status==ok and begun.battle_start_time==clock[0] and battle(a)==begun
                assert call(a,"BossRushLeaveStage",battle_info=pb.BossRushStageBattleInfo(stage_index=8),**fields).status!=ok
                assert count(friend,131)==available and not ranked(b).rank_info
                assert detail(c,owner).status!=ok
        finally:game.stop()
        game.start()
        try:
            a,_=connect(0);b,_=connect(1);c,_=connect(2)
            with a,b,c:
                clock[0]+=2300
                assert battle(a).battle_start_time==begun.battle_start_time
                assert leave(a,160000).status==ok and leave(a,160000).status==ok
                assert leave(a,2).status!=ok
                live=call(a,"BossRushInfo",**fields).info
                stage=live.stage_infos[0]
                assert (stage.total_score,stage.total_damage_hp,stage.total_consume_time)==(160000,160000,2300)
                assert len(stage.teams)==1 and list(stage.teams[0].characters)==[101001]
                assert enter(a,101001).status==pb.StatusCode_CHARACTER_ENTER_BOSS_RUSH
                assert close_run(a).status==ok and close_run(a).status==ok
                settled=call(a,"BossRushInfo",**fields).info
                assert settled.best_total_score==160000 and settled.current_stage_index==-1
                assert not settled.stage_infos[0].teams and not settled.used_characters
                with game.lock:amounts=reward_items(owner,5050101)
                balances={i:count(owner,i) for i in amounts}
                assert claim(a).status==ok and {i:count(owner,i)-n for i,n in balances.items()}==amounts
                assert claim(a).status==pb.StatusCode_REPEAT_GET_REWARD and claim(b).status!=ok
                assert detail(a,owner).details[0].stage[0].teams[0].total_consume_time==2300
                assert [r.rank_info.player_id for r in ranked(a).rank_info]==[owner] and not ranked(b).rank_info
                with game.lock,db.conn:
                    for left,right in [(owner,friend),(friend,owner)]:
                        db.conn.execute("INSERT INTO friend(player_id,friend_id,friend_status) VALUES(?,?,?)",(left,right,pb.FriendStatus_FRIEND))
                assert [r.rank_info.player_id for r in ranked(b).rank_info]==[owner]
                assert detail(b,owner).details[0].score==160000 and detail(c,owner).status!=ok
                clock[0]+=24*3600_000
                assert start(b,1).status==ok and enter(b,101001).battle_info.stage_index==1 and battle(b).status==ok
                hp=_table("BossRush","boss_rush_stage")[sid]["boss_rush_stage_group_info"][1]["max_h_p"]
                assert leave(b,hp+500).best_total_score==hp and leave(b,hp+500).best_total_score==hp
                ranked_rows=ranked(b).rank_info
                assert [(r.rank_info.player_id,r.rank_info.rank_ratio) for r in ranked_rows]==[(friend,0),(owner,5000)]
                assert detail(b,friend).details[0].stage[1].is_passed and not detail(c,stranger).details
                assert start(a,1).status==ok and enter(a,101001).status==ok and battle(a).status==ok
                assert leave(a,27000).status==ok
                clock[0]+=3600_001
                expired=call(a,"BossRushInfo",**fields).info
                assert not expired.challenge_end_time and expired.best_total_score==187000
                assert enter(a,101001).status!=ok and count(owner,131)==available-2
                clock[0]+=20*24*3600_000
                assert start(a,0).status==pb.StatusCode_Activity_Close
        finally:game.stop()
    print("PASS native Boss Rush season/stage/party gates, real battle timestamps, restart, one-time costs, bounded damage, expiry, best-team records, friend-only rankings and score-gated rewards.")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:run(Path(tmp),p.parse_args().resources)
