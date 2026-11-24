"""Exercise an old pass and dated sign-ins through the live realm and the retail resource publisher."""
import argparse
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import realm_admin
from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_shop import CN
from realm_events import _catalog
from test_game_realm import login
from test_native_events import rpc, toggled

SEASONS = (260514070, 260625070)
SIGNS = (260212102, 260715100)
STAGE = {"theme": 23, "arcade": 0, "activities": [SEASONS[0], *SIGNS, 260326160, 260326170],
         "opens_at": "2032-05-01 00:00:00", "closes_at": "2032-05-14 23:59:59"}


def run(state, resources):
    identity = LocalIdentity(state / "identity.sqlite3")
    player = identity.create_profile("历史活动复开")
    def configure(data):
        return realm_admin.execute(state, "plaza.set", data, lambda: None)
    try:
        configure({**STAGE, "activities": list(SEASONS)})
    except ValueError:
        pass
    else:
        raise AssertionError("Conflicting battle pass seasons accepted")
    configure(STAGE)
    game = LocalGameService(identity, state, resources, port=0)
    when = datetime(2032, 5, 1, 12, tzinfo=CN)
    assert [len(_catalog()[season]["rewards"]) for season in SEASONS] == [70, 70]
    with patch("private_shop.now", return_value=when):
        game.start()
        try:
            sock, _, _ = login(game.port, identity.issue_ticket(*player))
            with sock:
                _, a = rpc(sock, "PlayerMainData", 3)
                _, b = rpc(sock, "PlayerPing", 4)
                opened = toggled(a + b, True)
                assert {SEASONS[0], *SIGNS, 260326160} <= opened and SEASONS[1] not in opened
                for index, aid in enumerate(SIGNS):
                    first = rpc(sock, "ActivitySignIn", 10 + index*2, activity_id=aid, day=1)[0]
                    assert first.status == pb.StatusCode_OK
                    again = rpc(sock, "ActivitySignIn", 11 + index*2, activity_id=aid, day=1)[0]
                    assert again.status == pb.StatusCode_REPEAT_GET_REWARD
                bought = rpc(sock, "ActivityBattlePassBuyExp", 20, activity_battle_pass_id=SEASONS[0])[0]
                assert bought.status == pb.StatusCode_OK and bought.battle_pass_lv >= 1
                assert rpc(sock, "GetActivityBattlePassLevelReward", 21, activity_battle_pass_id=SEASONS[0],
                           level=1, is_get_free_reward=True)[0].status == pb.StatusCode_OK
                configure({**STAGE, "activities": [SEASONS[1], *SIGNS]})
                assert rpc(sock, "ActivityBattlePassBuyExp", 22, activity_battle_pass_id=SEASONS[0])[0].status == pb.StatusCode_Activity_Close
                assert rpc(sock, "ActivityBattlePassBuyExp", 23, activity_battle_pass_id=SEASONS[1])[0].status == pb.StatusCode_OK
                configure({})
                assert rpc(sock, "ActivityBattlePassBuyExp", 24, activity_battle_pass_id=SEASONS[1])[0].status == pb.StatusCode_Activity_Close
        finally:
            game.stop()
    print("PASS historical sign-ins, duplicate claim, native S15/S16 updates, level reward and stock calendar reset.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    parser.add_argument("--client", type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as tmp:
        run(Path(tmp), args.resources)
    if args.client:
        from game_dispatch import Assets, PATCH
        assets = Assets(args.client)
        assert assets.select_plaza({**STAGE, "opens_at":"2020-01-01 00:00:00", "closes_at":"2070-01-01 00:00:00"}) != PATCH
        assert assets.select_plaza({}) == PATCH
        print("PASS stock client resource repack for sign-in, recall and pass season with original manifest restoration.")
