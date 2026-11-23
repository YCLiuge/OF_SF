"""Verify the national-client seasonal and archaeology queries using private-realm frames."""
from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from test_game_realm import login, join
from test_observed_initialization import call
from utils import db
from handlers.BossRushInfo import read_season


def run(root, resources):
    ids = LocalIdentity(root / "identity.sqlite3")
    one = ids.create_profile("考古一")
    two = ids.create_profile("考古二")
    game = LocalGameService(ids, root, resources, port=0)
    game.start()
    try:
        sock, _, pid = login(game.port, ids.issue_ticket(*one))
        with sock:
            join(sock)
            rsp = call(sock, "BossRushInfo", season_id=1001)
            assert rsp.status == pb.StatusCode_OK and rsp.info.season_id == 1001
            info = rsp.info
            assert [s.stage_index for s in info.stage_infos] == [0,1,2]
            assert info.current_stage_index == -1 and info.best_total_score == 0
            start = int(datetime(2025,8,29,tzinfo=timezone.utc).timestamp())
            assert info.start_time == start * 1000 and info.show_rank_time == start * 1000
            assert call(sock,"BossRushInfo",season_id=9999).status == pb.StatusCode_CFG_NOT_FOUND
            with game.lock:
                assert not read_season(pid, 0, start - 1).HasField("info")
                assert read_season(pid, 0, start).info.season_id == 1001
            initial = call(sock, "TreasureIllustrationInfo")
            assert initial.status == pb.StatusCode_OK and len(initial.achieves) == 5
            groups = {g.achieve_type:g for g in initial.achieves}
            assert set(groups) == set(range(5)) and all(len(g.treasure_achieve_)==3 for g in groups.values())
            assert [a.achieve_id for a in groups[pb.EArchaeologyType_TreasureMap].treasure_achieve_] == [150000101,150000102,150000103]
            assert all(not a.count for g in groups.values() for a in g.treasure_achieve_)
            with game.lock, db.conn:
                info.current_stage_index = 0
                info.stage_infos[0].total_score = 23
                info.challenge_end_time = 1
                db.set_boss_rush_info(pid,1001,info.SerializeToString())
                db.set_achieve(pid,150000101,pb.Achieve(achieve_id=150000101,count=2).SerializeToString())
                db.conn.executescript("CREATE TABLE IF NOT EXISTS claimed_rewards(player_id,system,key);"
                                      "CREATE TABLE IF NOT EXISTS player_kv(player_id,ns,k,v);")
                db.conn.execute("INSERT INTO claimed_rewards(player_id,system,key) VALUES(?,?,?)",(pid,"treasure_illustration","0:11"))
                db.conn.execute("INSERT INTO claimed_rewards(player_id,system,key) VALUES(?,?,?)",(pid,"treasure_achieve","150000101"))
                db.conn.execute("INSERT INTO player_kv(player_id,ns,k,v) VALUES(?,?,?,?)",(pid,"archaeology","7000270",3))
            updated = call(sock, "TreasureIllustrationInfo")
            maps = next(g for g in updated.achieves if g.achieve_type == pb.EArchaeologyType_TreasureMap)
            assert list(updated.book_reward) == [11] and not updated.utensil_reward
            assert list(maps.rewarded_treasure_achieve_id_lst)==[150000101]
            assert [a.count for a in maps.treasure_achieve_] == [2,3,0]
            settled = call(sock, "BossRushInfo", season_id=1001).info
            assert settled.best_total_score == 23 and not settled.challenge_end_time
            assert settled.current_stage_index == -1 and not settled.stage_infos[0].total_score
        sock, _, other = login(game.port, ids.issue_ticket(*two))
        with sock:
            join(sock)
            assert other != pid and not call(sock,"BossRushInfo",season_id=1001).info.best_total_score
            fresh = call(sock,"TreasureIllustrationInfo")
            assert not fresh.book_reward and all(not a.count for g in fresh.achieves for a in g.treasure_achieve_)
    finally:
        game.stop()
    game.start()
    try:
        sock, _, _ = login(game.port, ids.issue_ticket(*one))
        with sock:
            join(sock)
            assert call(sock,"BossRushInfo",season_id=1001).info.best_total_score==23
            assert list(call(sock,"TreasureIllustrationInfo").book_reward)==[11]
    finally:
        game.stop()
    print("PASS season 2701 and archaeology 4523, inactive/active seasons, national UTC+8 calendar, claims, restart and player isolation.")


if __name__=="__main__":
    parser=ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)

