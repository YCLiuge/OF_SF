"""Original seven-day newcomer tasks, real outfit/potion actions, tier claims and date gates."""
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_shop import CN, balance
from realm_events import NEWCOMER, available, configure
from test_game_realm import login
from test_native_events import DAILY, GOAL, rpc, toggled
from utils import db
from utils.pb_create import make_item


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    slots = [identity.create_profile(name) for name in ("新人甲", "新人乙")]
    game = LocalGameService(identity, root, resources, port=0)
    when = [datetime(2026, 11, 24, 10, tzinfo=CN)]
    entry = next(row for row in available() if row["id"] == NEWCOMER)
    assert len([r for r in entry["rewards"] if "goal" in r]) == 70
    assert [r["required"] for r in entry["rewards"] if "tier" in r] == list(range(15, 106, 15))

    def connect(i):
        sock, _, pid = login(game.port, identity.issue_ticket(*slots[i]))
        stock, first = rpc(sock, "PlayerMainData", 3)
        _, rest = rpc(sock, "PlayerPing", 4)
        return sock, pid, stock, first + rest

    def view(frames):
        return next(info for n in frames if isinstance(n, pb.ActivityQuestDataNotice)
                    for info in n.info if info.activity_id == NEWCOMER)

    def claim(sock, seq, param, tier=False):
        return rpc(sock, "ActivityQuestReward", seq, activity_id=NEWCOMER, param=param, is_progress=tier)

    def count(pid, key):
        return (balance(pid, key) or pb.ItemDetail()).main_item.base_item.num

    with patch("private_shop.now", side_effect=lambda: when[0]):
        game.start()
        try:
            first, pid, stock, initial = connect(0)
            second, peer, _, _ = connect(1)
            with first, second:
                with game.lock, db.conn:
                    for player in (pid, peer):
                        db.set_players_info(player, "create_time", int(when[0].timestamp()))
                assert NEWCOMER in toggled(initial, True)
                assert view(initial).activity_id == 1001
                assert claim(first, 8, 80100107)[0].status != pb.StatusCode_OK  # No world-challenge evidence.
                assert claim(first, 9, 80100110)[0].status != pb.StatusCode_OK
                assert claim(first, 10, 1, tier=True)[0].status == pb.StatusCode_ITEM_NOT_ENOUGH
                current = next(c for c in stock.characters if c.character_id == 101001)
                preset = pb.OutfitPreset()
                preset.CopyFrom(current.outfit_presets[1])
                preset.clothes = 4011023
                assert rpc(first, "OutfitPresetUpdate", 11, char_id=101001, preset=preset)[0].status == pb.StatusCode_OK
                assert claim(first, 12, 80100110)[0].status != pb.StatusCode_OK  # Saving an inactive preset is not wearing it.
                assert rpc(first, "OutfitPresetSwitch", 13, char_id=101001, use_preset_index=1)[0].status == pb.StatusCode_OK
                reward, notices = claim(first, 14, 80100110)
                assert reward.status == pb.StatusCode_OK and 80100110 in view(notices).reward_achieve
                assert {d.main_item.item_id: d.main_item.base_item.num for d in reward.rewards} == {270: 2, 6001: 3}
                assert claim(first, 14, 80100110)[0].status == pb.StatusCode_REPEAT_GET_REWARD
                assert claim(second, 15, 80100110)[0].status != pb.StatusCode_OK
                # A fixture supplies exactly the remaining tier tokens, independently of the tested task reward.
                with game.lock, db.conn:
                    token = make_item(6001, 15, pid)
                    db.set_item_detail(pid, token.SerializeToString(), 6001)
                    limit = count(pid, 110)
                tier, notices = claim(first, 16, 1, tier=True)
                assert tier.status == pb.StatusCode_OK and tier.is_progress
                assert tier.reward_id == view(notices).progress_reward == 2 and tier.param == 1
                with game.lock:
                    assert count(pid, 110) == limit+1 and count(pid, 6001) == 15
                assert claim(first, 17, 1, tier=True)[0].status == pb.StatusCode_REPEAT_GET_REWARD
                assert claim(second, 18, 1, tier=True)[0].status == pb.StatusCode_ITEM_NOT_ENOUGH
                assert rpc(first, "ActivityRegularReward", 19, activity_id=DAILY, param=GOAL, pool_type=1)[0].status == pb.StatusCode_OK
                assert rpc(first, "ItemUse", 20, item_id=180, num=1)[0].status == pb.StatusCode_OK
                assert claim(first, 21, 80100308)[0].status != pb.StatusCode_OK  # Active goal, day 3 still locked.
                when[0] += timedelta(days=2)
                day_three, notices = claim(first, 22, 80100308)
                assert day_three.status == pb.StatusCode_OK and view(notices).open_day == 3
                configure(root, {"events": [{"id": NEWCOMER, "enabled": False, "opens_at": None, "closes_at": None}]})
                assert claim(first, 23, 80100308)[0].status == pb.StatusCode_Activity_Close
                configure(root, {"events": []})
        finally:
            game.stop()
        game.start()
        try:
            first, _, _, state = connect(0)
            second, _, _, other = connect(1)
            with first, second:
                assert view(state).progress_reward == 2 and set(view(state).reward_achieve) == {80100110, 80100308}
                assert not view(other).progress_reward and not view(other).reward_achieve
                assert view(state).open_day == 3 and view(other).open_day == 3
        finally:
            game.stop()
    print("PASS native newcomer 7 days, 70 tasks, 7 tiers, verified outfit and potion actions, age gates, unsupported goals uncredited, saved receipts and role isolation.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp), parser.parse_args().resources)
