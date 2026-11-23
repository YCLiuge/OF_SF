"""Check the national pass and anniversary wish on two private players over TCP."""
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from private_battle_pass import WISH
from private_dungeons import _table
from private_shop import CN, balance
from test_game_realm import login
from test_observed_initialization import call
from utils import db


PASS, TEMPLATE, DAILY, DUNGEON, SEASON, LOGIN_60 = 260815070, 10190, 80200100, 80200101, 80200500, 80509201


def run(state, resources):
    identity = LocalIdentity(state / "identity.sqlite3")
    slots = [identity.create_profile(label) for label in ("通行甲", "通行乙")]
    game = LocalGameService(identity, state, resources, port=0)
    clock = [datetime(2026, 11, 24, 3, 55, tzinfo=CN)]

    def connect(index):
        sock, _, player = login(game.port, identity.issue_ticket(*slots[index]))
        sock.sendall(encode(MsgId.PlayerMainDataReq, pb.PlayerMainDataReq().SerializeToString(), 3))
        active, passes, wish = set(), None, None
        for _ in range(180):
            h, buf = receive(sock)
            if h.msg_id == MsgId.ActivityChangeNotice:
                change = pb.ActivityChangeNotice.FromString(buf)
                if change.is_open:
                    active.update(change.activity_id)
            if h.msg_id == MsgId.ActivityBattlePassInfoNotice:
                passes = pb.ActivityBattlePassInfoNotice.FromString(buf)
            if h.msg_id == MsgId.ActivityRegularDataNotice:
                wish = next((i for i in pb.ActivityRegularDataNotice.FromString(buf).info if i.activity_id == WISH), wish)
            if passes and wish and {PASS, WISH} <= active:
                return sock, player, next(i for i in passes.info if i.activity_id == PASS), wish
        sock.close()
        raise AssertionError("Expected both native activity IDs and their progress notifications")

    def count(player, item):
        with game.lock:
            return balance(player, item).main_item.base_item.num

    def goal(sock, key, aid=PASS):
        return call(sock, "GetActivityBattlePassQuestReward", activity_battle_pass_id=aid, achieve_id=key)

    def tier(sock, level, free=True, aid=PASS):
        return call(sock, "GetActivityBattlePassLevelReward", activity_battle_pass_id=aid, level=level, is_get_free_reward=free)

    def wish_reward(sock):
        return call(sock, "ActivityRegularReward", activity_id=WISH, param=LOGIN_60, pool_type=3)

    with patch("private_shop.now", side_effect=lambda: clock[0]):
        game.start()
        try:
            a, pid, info, wish = connect(0)
            b, bid, other, _ = connect(1)
            with a, b:
                assert info.level == 0 and not info.has_buy and not other.total_exp and not wish.param1
                assert tier(a, 1).status == pb.StatusCode_BattlePass_LevelLimit
                assert tier(a, 1, False).status == pb.StatusCode_BattlePass_Not_Buy
                assert goal(a, DUNGEON).status != pb.StatusCode_OK
                assert goal(a, SEASON).status != pb.StatusCode_OK
                assert goal(a, DAILY, TEMPLATE).add_exp == 200
                assert goal(a, DAILY).status == pb.StatusCode_BattlePass_QuestRewarded
                assert goal(b, DAILY).current_total_exp == 200
                with game.lock, db.conn:
                    for _ in range(10):
                        db.conn.execute("INSERT INTO gacha_record(player_id,gacha_id,item_id,gacha_time) VALUES(?,?,?,?)",
                                        (pid, 1, 1, int(clock[0].timestamp())))
                assert goal(a, SEASON).status == pb.StatusCode_OK
                assert goal(b, SEASON).status != pb.StatusCode_OK
                stars = count(pid, 102)
                paid = call(a, "ActivityBattlePassBuyExp", activity_battle_pass_id=PASS)
                assert paid.status == pb.StatusCode_OK and paid.battle_pass_lv >= 1 and count(pid, 102) == stars-50
                item = _table("BattlePass", "battle_pass")[TEMPLATE]["battle_pass_level_ups"][0]["battle_pass_reward"][0]
                coin = count(pid, item["item_i_d"])
                assert tier(a, 1).status == pb.StatusCode_OK and count(pid,item["item_i_d"]) == coin+item["item_num"]
                assert tier(a, 1, aid=TEMPLATE).status == pb.StatusCode_BattlePass_LvRewarded
                assert wish_reward(a).status != pb.StatusCode_OK
                assert call(a, "ActivityUploadWishTxt", activity_id=WISH, txt=" ").status != pb.StatusCode_OK
                text = call(a, "ActivityUploadWishTxt", activity_id=WISH, txt="希望重逢")
                assert text.status == pb.StatusCode_OK and text.txt == "希望重逢"
                with game.lock, db.conn:
                    db.conn.execute("INSERT INTO player_kv VALUES(?,'private_pass',?,1)", (pid,str(TEMPLATE)))
                    db.conn.execute("UPDATE private_login_days SET days=59 WHERE player=? AND activity=?", (pid,WISH))
                clock[0] += timedelta(minutes=5)
                assert goal(a, DAILY).status == pb.StatusCode_OK
                assert wish_reward(a).status == pb.StatusCode_OK and wish_reward(a).status == pb.StatusCode_REPEAT_GET_REWARD
                assert wish_reward(b).status != pb.StatusCode_OK
        finally:
            game.stop()
        game.start()
        try:
            a, _, info, wish = connect(0)
            b, _, other, wish_b = connect(1)
            with a,b:
                assert info.has_buy and list(info.free_lv_reward_lst) == [1]
                assert len(info.payment_lv_reward_lst) == 70 and tier(a,1,False).status == pb.StatusCode_BattlePass_LvRewarded
                assert set(info.season_achieve_reward_lst) == {SEASON} and list(info.daily_achieve_reward_lst) == [DAILY]
                assert wish.param1 and wish.pool_achieve[0].is_reward and not wish_b.param1 and not wish_b.pool_achieve[0].is_reward
                assert not other.has_buy and not other.free_lv_reward_lst and other.total_exp == 200
                clock[0] = datetime(2027,1,1,0,0,tzinfo=CN)
                assert goal(a,DAILY).status == pb.StatusCode_Activity_Close
                assert tier(a,1).status == pb.StatusCode_Activity_Close
                assert wish_reward(a).status == pb.StatusCode_Activity_Close
                assert call(a,"ActivityUploadWishTxt",activity_id=WISH,txt="过期").status == pb.StatusCode_Activity_Close
        finally:
            game.stop()
    print("PASS native pass and anniversary wish activation, progress, rewards, purchases, duplicate prevention, separate roles, restart and expiry.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    with TemporaryDirectory() as temp:
        run(Path(temp), parser.parse_args().resources)
