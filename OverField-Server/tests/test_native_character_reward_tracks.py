"""Every CN character medal and defined reward tier, using two isolated native sessions."""
import argparse
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_dungeons import _table
from test_game_realm import login, join
from test_native_gacha import call
from utils import db


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profiles = [identity.create_profile(name) for name in ("成就领取甲", "成就领取乙")]
    host = LocalGameService(identity, root, resources, port=0)

    def connect(i):
        sock, _, pid = login(host.port, identity.issue_ticket(*profiles[i]))
        join(sock)
        return sock, pid

    def query(sock, cid):
        return call(sock, "GetCharacterAchievementList", 41, character_id=cid)

    def badge(sock, cid, aid):
        return call(sock, "GetCharacterAchievementBadgeAward", 42, character_id=cid, achievement_id=aid)

    def tier(sock, cid, idx):
        return call(sock, "GetCharacterAchievementAward", 43, character_id=cid, reward_index=idx)

    def unlock(sock, cid):
        return call(sock, "GetCharacterAchievementUnlockPayment", 44, character_id=cid)

    def amount(pid, item):
        raw = db.get_item_detail(pid, item)
        return pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0

    host.start()
    try:
        a, pid = connect(0)
        b, _ = connect(1)
        with a, b:
            hero = 101001
            assert badge(a, hero, 41010014).status == pb.StatusCode_Quest_Not_Finish
            assert tier(a, hero, 1).status == pb.StatusCode_Quest_Not_Finish
            assert tier(a, hero, 9).status == pb.StatusCode_MODULE_CLOSE
            assert unlock(a, 0).status == pb.StatusCode_CharNotExist
            assert badge(a, hero, 9876543).status == pb.StatusCode_CFG_NOT_FOUND
            chars = _table("Character", "character")
            goals = _table("Achieve", "achieve")
            totals = Counter()
            for cid, config in chars.items():
                badges = _table("Character","character_achieve")[config["character_achieve_i_d"]]["achieve_info"]
                tiers = _table("Character","character_achieve_reward")[config["achieve_reward_i_d"]]["achieve_reward_info"]
                with host.lock, db.conn:
                    char = pb.Character(character_id=cid, level=100, max_level=100, star=6)
                    db.set_character(pid, cid, char.SerializeToString())
                    for record in badges:
                        aid = record["achieve_i_d"]
                        db.set_achieve(pid, aid, pb.Achieve(achieve_id=aid,count=goals[aid]["count_param"]).SerializeToString())
                for record in badges:
                    aid = record["achieve_i_d"]
                    response = badge(a,cid,aid)
                    assert response.status == pb.StatusCode_OK and list(response.rewarded_achievement_id_lst).count(aid) == 1
                    assert response.update_items[0].main_item.item_id == record["item_i_d"]
                    assert response.achieves[0].count == goals[aid]["count_param"]
                    assert badge(a,cid,aid).status == pb.StatusCode_REPEAT_GET_REWARD
                    totals["medals"] += 1
                medal_id = badges[0]["item_i_d"]
                with host.lock:
                    before = amount(pid,medal_id)
                assert before >= 8
                assert unlock(a,cid).is_unlock and unlock(a,cid).is_unlock
                for row in tiers:
                    idx = row["num"]
                    response = tier(a,cid,idx)
                    if not row.get("reward_item_i_d"):
                        assert response.status == pb.StatusCode_CFG_NOT_FOUND and not response.items
                        totals["empty"] += 1
                        continue
                    assert response.status == pb.StatusCode_OK, (cid,idx,response.status)
                    assert response.items[0].main_item.item_id == row["reward_item_i_d"]
                    assert idx in response.rewarded_index_lst
                    assert tier(a,cid,idx).status == pb.StatusCode_REPEAT_GET_REWARD
                    totals["tiers"] += 1
                with host.lock:
                    assert amount(pid,medal_id) == before
            assert totals == {"medals":288,"tiers":554,"empty":22}
            assert not query(b,hero).has_rewarded_ids and not query(b,hero).is_unlocked_payment
    finally:
        host.stop()
    host.start()
    try:
        a, _ = connect(0)
        b, _ = connect(1)
        with a,b:
            assert len(query(a,hero).rewarded_id_lst) == 8 and len(query(a,hero).has_rewarded_ids) == 16
            assert tier(a,hero,1).status == pb.StatusCode_REPEAT_GET_REWARD
            assert not query(b,hero).rewarded_id_lst
    finally:
        host.stop()
    print("PASS 36 character tracks, 288 medals, 554 configured tier payouts, 22 empty rewards, duplicate protection and role-isolated restart.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    with TemporaryDirectory() as root:
        run(Path(root), parser.parse_args().resources)
