"""Exercise the national client's blessing tree page before and after a real node purchase."""
import argparse
import json
from datetime import datetime
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_shop import CN, balance
from test_game_realm import join, login
from test_observed_initialization import call
from utils import db
from utils.pb_create import make_item


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    players = [identity.create_profile(name) for name in ("祈愿甲", "祈愿乙")]
    game = LocalGameService(identity, root, resources, port=0)
    layouts = json.loads((resources / "data/BlessingTree.json").read_bytes())
    tree_id = 10012
    tree = next(row for row in layouts["blessing_tree_info"]["datas"] if row["i_d"] == tree_id)
    node = next(row for row in tree["blessing_tree_info_group_info"] if not row.get("relate_box_i_d"))
    cost_id, cost = node["item_i_d"], node["item_count"]
    reward_id, amount = node["reward_item_i_d"], node["reward_item_count"]
    node_id = node["box_i_d"]

    def connect(index):
        sock, _, player_id = login(game.port, identity.issue_ticket(*players[index]))
        join(sock)
        return sock, player_id

    def read_nodes(sock, layout=tree_id):
        rsp = call(sock, "BlessTree", def_id=layout)
        assert rsp.status == pb.StatusCode_OK and rsp.def_id == layout and rsp.HasField("tress")
        return list(rsp.tress.tree_ids)

    def count(player_id, template):
        item = balance(player_id, template)
        return item.main_item.base_item.num if item else 0

    game.start()
    try:
        first, pid = connect(0)
        second, _ = connect(1)
        with first, second:
            assert read_nodes(first) == read_nodes(second) == []
            for bad_id in (0, 4294967295):
                assert call(first, "BlessTree", def_id=bad_id).status == pb.StatusCode_CFG_NOT_FOUND
            with game.lock, db.conn:
                token = pb.ItemDetail()
                make_item(cost_id, cost, pid, token)
                db.set_item_detail(pid, token.SerializeToString(), cost_id)
                initial_reward = count(pid, reward_id)
            unlocked = call(first, "BlessTreeUnlock", def_id=tree_id, tree_id=node_id)
            assert unlocked.status == pb.StatusCode_OK
            assert len(unlocked.rewards) == 1 and unlocked.rewards[0].main_item.base_item.num == amount
            assert read_nodes(first) == [node_id] and read_nodes(second) == []
            assert read_nodes(first, 101) == []
            assert call(first, "BlessTreeUnlock", def_id=tree_id, tree_id=node_id).status == pb.StatusCode_PLAYER_BLESS_TREE_EXIST
            with game.lock:
                assert count(pid, cost_id) == 0 and count(pid, reward_id) == initial_reward + amount
    finally:
        game.stop()
    game.start()
    try:
        first, restored_pid = connect(0)
        second, _ = connect(1)
        with first, second:
            assert pid == restored_pid and read_nodes(first) == [node_id] and read_nodes(second) == []
    finally:
        game.stop()
    print("PASS native blessing tree empty/claimed views, valid layout, purchase, cost, reward, cross-role separation and restart.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    with TemporaryDirectory() as tmp:
        with patch("private_bless_tree.now", return_value=datetime(2026,11,15,12,tzinfo=CN)):
            run(Path(tmp), parser.parse_args().resources)
