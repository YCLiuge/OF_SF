"""Exercise both material acquisition buttons and ensure price, progress and replay agree."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from test_native_gacha import call
from test_game_realm import login, join_after_intro
from utils import db
from utils.pb_create import make_item


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profile = identity.create_profile("材料测试")
    host = LocalGameService(identity, root, resources, port=0)

    def connect():
        sock, _, pid = login(host.port, identity.issue_ticket(*profile))
        join_after_intro(sock)
        return sock, pid

    def amount(pid, item):
        raw = db.get_item_detail(pid, item)
        return pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0

    host.start()
    try:
        sock, pid = connect()
        with sock:
            with host.lock, db.conn:
                db.set_players_info(pid, "level", 10)
                db.set_players_info(pid, "exp", 0)
                for key, count in ((10, 20), (101, 0), (102, 1), (108, 3)):
                    db.set_item_detail(pid, make_item(key, count, pid).SerializeToString(), key)
            def sweep(seq, qty, vitality):
                return call(sock, "ItemSweep", seq, item_id=101, count=qty, is_sweep=vitality)
            assert sweep(10, 1, True).status == pb.StatusCode_BAD_REQ
            assert call(sock, "ItemSweep", 11, item_id=999999, count=1).status == pb.StatusCode_CFG_NOT_FOUND
            batch = sweep(12, 864, True)
            assert batch.status == pb.StatusCode_OK
            assert {i.main_item.item_id: i.main_item.base_item.num for i in batch.rewards} == {101:864, 1:60}
            assert sweep(12, 864, True) == batch
            assert sweep(13, 864, False).status == pb.StatusCode_ITEM_NOT_ENOUGH
            with host.lock:
                assert [amount(pid, i) for i in (10, 101, 102, 108)] == [14, 864, 1, 3]
                assert db.get_players_info(pid, "exp")[0] == 60
            stars = sweep(14, 432, False)
            assert stars.status == pb.StatusCode_OK and len(stars.rewards) == 1
            assert sweep(14, 432, False) == stars
        host.stop()
        host.start()
        sock, same = connect()
        with sock, host.lock:
            assert pid == same
            assert [amount(pid, i) for i in (10, 101, 102, 108)] == [14, 1296, 0, 1]
            assert db.get_players_info(pid, "exp")[0] == 60
    finally:
        host.stop()
    print("PASS native item sweep level tier, both currencies, reward deltas, rollback, replay and restart.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp), parser.parse_args().resources)
