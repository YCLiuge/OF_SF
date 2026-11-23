"""Complete the first native world-level trial and keep unlocked tiers across difficulty changes."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from test_native_gacha import call
from test_game_realm import join, login
from utils import db


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    first, second = (identity.create_profile(name) for name in ("世界试炼甲", "世界试炼乙"))
    game = LocalGameService(identity, root, resources, port=0)
    game.start()
    try:
        sock, _, pid = login(game.port, identity.issue_ticket(*first))
        peer, _, other = login(game.port, identity.issue_ticket(*second))
        with sock, peer:
            join(sock)
            join(peer)
            with game.lock, db.conn:
                db.set_players_info(pid, "level", 24)
            listed = call(sock, "WorldLevelAchieveList", 10)
            assert list(listed.unlock_world_levels) == [1]
            assert {a.achieve_id: a.count for a in listed.achieves}[16000201] == 24
            assert call(sock, "ChangeWorldLevel", 11, world_level=2).status == pb.StatusCode_WORLD_LEVEL_LOCKED
            assert call(sock, "UnlockWorldLevel", 12, world_level=0).status == pb.StatusCode_BAD_REQ
            assert call(sock, "UnlockWorldLevel", 13, world_level=3).status == pb.StatusCode_WORLD_LEVEL_LOCKED
            assert call(sock, "DungeonEnter", 14, dungeon_id=950001, char1=101001).status == pb.StatusCode_OK
            assert call(sock, "DungeonFinish", 15).dungeon_data.finish_times == 1
            call(sock, "DungeonExit", 16)
            assert call(sock, "UnlockWorldLevel", 17, world_level=2).status == pb.StatusCode_WORLD_LEVEL_ACHIEVE_NOT_FINISH
            with game.lock, db.conn:
                db.set_players_info(pid, "level", 25)
            first_unlock = call(sock, "UnlockWorldLevel", 18, world_level=2)
            assert first_unlock.status == pb.StatusCode_OK and first_unlock.world_level == 2
            assert call(sock, "UnlockWorldLevel", 19, world_level=2).status == pb.StatusCode_OK
            assert call(sock, "ChangeWorldLevel", 20, world_level=1).world_level == 1
            assert call(sock, "ChangeWorldLevel", 21, world_level=1).status == pb.StatusCode_WORLD_LEVEL_NOW_IN
            assert list(call(sock, "WorldLevelAchieveList", 22).unlock_world_levels) == [1, 2]
            assert list(call(peer, "WorldLevelAchieveList", 23).unlock_world_levels) == [1]
            # An older database can already have a high selected tier, without an unlock ledger.
            with game.lock, db.conn:
                db.set_players_info(other, "world_level", 4)
            assert call(peer, "ChangeWorldLevel", 24, world_level=1).status == pb.StatusCode_OK
            assert list(call(peer, "WorldLevelAchieveList", 25).unlock_world_levels) == [1, 2, 3, 4]
        game.stop()
        game.start()
        sock, _, _ = login(game.port, identity.issue_ticket(*first))
        with sock:
            main, _ = join(sock)
            assert main.world_level == 1
            assert list(call(sock, "WorldLevelAchieveList", 26).unlock_world_levels) == [1, 2]
            assert call(sock, "ChangeWorldLevel", 27, world_level=2).world_level == 2
    finally:
        game.stop()
    print("PASS native world-level trial, captain gates, tier switch, replay, old saves, player isolation and restart.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory() as temp:
        run(Path(temp), args.resources)
