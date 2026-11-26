"""Verify native draw result extras, durable grant receipts, conversions and atomic rejection."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from realm_gacha import configure
from test_game_realm import join, login
from test_native_gacha import call
from private_dungeons import _table
from utils import db
from utils.pb_create import make_item


def amount(pid, item):
    raw = db.get_item_detail(pid, item)
    return pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0


def run(root, resources):
    owner = LocalIdentity(root / "identity.sqlite3")
    profile = owner.create_profile("招募结算核验")
    game = LocalGameService(owner, root, resources, port=0)
    configure(root, {"pools":[{"id":2000,"enabled":True,"pity":70,"weights":{"100":10000}}]})
    game.start()
    try:
        sock, _, pid = login(game.port, owner.issue_ticket(*profile))
        with sock:
            join(sock)
            with game.lock, db.conn:
                db.set_item_detail(pid, make_item(104, 10, pid).SerializeToString(), 104)
                db.conn.execute("DELETE FROM characters WHERE player_id=? AND character_id IN (102001,302002)", (pid,))
            sr, ssr, poster = 102001, 302002, 10040
            shard = _table("Character", "character")[sr]["repeat_item_i_d"]
            seq = 30
            observed = []

            def draw(template, fresh, expected):
                nonlocal seq
                seq += 1
                with game.lock:
                    before = {i:amount(pid,i) for i in expected}
                with patch("private_gacha.outcome", return_value=(template, 0)):
                    response = call(sock, "Gacha", seq, gacha_id=2000, is_single=True)
                    assert response.status == pb.StatusCode_OK
                    result, = response.items
                    assert result.main_item.item_id == template and result.main_item.is_new == fresh
                    assert {i.item_id:i.base_item.num for i in (*result.transformed_item, *result.extras)} == expected
                    # A replay is the same wire response, with no second ticket or credit.
                    assert call(sock, "Gacha", seq, gacha_id=2000, is_single=True) == response
                with game.lock:
                    row = db.conn.execute("""SELECT g.item_id,r.origin,r.is_new,r.extras FROM gacha_record g
                        JOIN private_gacha_rewards r ON g.id=r.record_id WHERE g.player_id=? ORDER BY g.id DESC LIMIT 1""",
                        (pid,)).fetchone()
                    assert row[:3] == (template,"draw",int(fresh)) and dict(json.loads(row[3])) == expected
                    assert {i:amount(pid,i)-before[i] for i in expected} == expected
                observed.append(row)

            draw(poster, True, {107:500})
            draw(poster, False, {107:500,111:20})
            draw(sr, True, {107:500})
            draw(sr, False, {107:500,111:3,shard:3})
            with game.lock, db.conn:
                char = pb.Character.FromString(db.get_characters(pid,sr)[0])
                char.star = 5
                db.set_character(pid,sr,char.SerializeToString())
            draw(sr, False, {107:500,111:12})
            draw(ssr, True, {107:1500})
            with game.lock, db.conn:
                item = pb.Character.FromString(db.get_characters(pid,ssr)[0])
                item.star = 5
                db.set_character(pid,ssr,item.SerializeToString())
                # Historical pulls can predate the ledger. Seeding them must not retroactively issue any credits.
                db.conn.executemany("INSERT INTO gacha_record(player_id,gacha_id,item_id,gacha_time) VALUES(?,2000,10030,1)",
                                    [(pid,)]*(300-len(observed)))
                old = {i:amount(pid,i) for i in (104,107,111,112,shard)}
            selection = call(sock, "GachaFullPick", 80, gacha_id=2000, item_id=ssr)
            assert selection.status == pb.StatusCode_OK
            result, = selection.item
            assert not result.main_item.is_new and {i.item_id:i.base_item.num for i in (*result.transformed_item, *result.extras)} == {111:40}
            assert call(sock, "GachaFullPick", 81, gacha_id=2000, item_id=ssr).status == pb.StatusCode_REPEAT_GET_REWARD
            with game.lock:
                assert {i:amount(pid,i)-n for i,n in old.items()} == {104:0,107:0,111:40,112:0,shard:0}
                old = {i:amount(pid,i) for i in (104,107,111,112,shard)}
            # A failure while writing the receipt must roll back both the gacha row and its inventory deltas.
            with patch("private_gacha_rewards.store", side_effect=RuntimeError("test receipt writing")), \
                 patch("private_gacha.outcome",return_value=(poster,0)):
                try:
                    call(sock, "Gacha", 82, gacha_id=2000, is_single=True)
                except (EOFError,OSError):
                    pass
                else:
                    raise AssertionError("Incomplete ledger transaction was accepted")
        game.stop()
        game.start()
        sock, _, restored = login(game.port, owner.issue_ticket(*profile))
        with sock:
            join(sock)
            assert restored == pid
            with game.lock:
                assert {i:amount(pid,i) for i in old} == old
                assert db.get_total_gacha_num(pid,2000) == 300
                rows = db.conn.execute("""SELECT g.item_id,r.origin,r.is_new,r.extras FROM gacha_record g
                    JOIN private_gacha_rewards r ON g.id=r.record_id WHERE g.player_id=? ORDER BY g.id""",(pid,)).fetchall()
                assert rows == observed
    finally:
        game.stop()
    print("PASS native SSR poster, SR character and full-star extras; guaranteed pick conversions; atomic per-draw receipts, replay and restart.")


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)
