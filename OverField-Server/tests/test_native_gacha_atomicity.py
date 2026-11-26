"""Native ten-pull atomicity, absolute inventory deltas and old poster discovery records."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from realm_gacha import configure, defaults
from private_first_quest import CONDITIONS, STEPS, save, saved
from test_game_realm import join, join_after_intro, login
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def draw(sock, packet_id, single=False, notices=None):
    req = pb.GachaReq(gacha_id=2000, is_single=single)
    sock.sendall(encode(MsgId.GachaReq, req.SerializeToString(), packet_id))
    if notices is None:
        notices = []
    while True:
        header, raw = receive(sock)
        if header.msg_id == MsgId.PackNotice:
            notices.append(pb.PackNotice.FromString(raw))
        if header.msg_id == MsgId.GachaRsp and header.packet_id == packet_id:
            return pb.GachaRsp.FromString(raw), notices


def snapshot(pid):
    tables = ("items", "items_s", "characters", "instance", "quests", "claimed_rewards",
              "gacha_record", "gacha_guarantee")
    state = {table: db.conn.execute(f"SELECT * FROM {table} WHERE player_id=? ORDER BY rowid", (pid,)).fetchall()
             for table in tables}
    state["grants"] = db.conn.execute("""SELECT r.* FROM private_gacha_rewards r
        JOIN gacha_record g ON g.id=r.record_id WHERE g.player_id=? ORDER BY g.id""", (pid,)).fetchall()
    return state


def balance(pid, template):
    raw = db.get_item_detail(pid, template)
    return pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0


def set_balance(pid, template, count):
    db.set_item_detail(pid, make_item(template, count, pid).SerializeToString(), template)


def run(root, resources):
    owner = LocalIdentity(root / "identity.sqlite3")
    one, two = (owner.create_profile(name) for name in ("atomic-one", "atomic-two"))
    game = LocalGameService(owner, root, resources, port=0)
    settings = defaults()
    next(pool for pool in settings["pools"] if pool["id"] == 2000)["sr_pity"] = 0
    configure(root, settings)
    game.start()
    try:
        first, _, pid = login(game.port, owner.issue_ticket(*one))
        second, _, peer = login(game.port, owner.issue_ticket(*two))
        with first, second:
            join(first)
            join(second)
            call(first, "PlayerPing", 11)
            with game.lock, db.conn:
                set_balance(pid, 104, 20)
                set_balance(pid, 107, 0xffffffff - 2200)
                db.conn.execute("DELETE FROM characters WHERE player_id=? AND character_id=401004", (pid,))
                # One consumed draw predates the reward ledger, one held instance predates the ownership
                # ledger, and one consumed instance was acquired outside recruitment on a newer save.
                db.add_gacha_record(pid, 3000, 10180)
                old_item = make_item(10200, 1, pid)
                db.conn.execute("INSERT INTO items_s VALUES(?,?,?)", (pid, old_item.main_item.poster.instance_id,
                                                                       old_item.SerializeToString()))
                discarded = make_item(10320, 1, pid)
                instance = discarded.main_item.poster.instance_id
                db.set_item_detail(pid, discarded.SerializeToString(), instance_id=instance)
                db.del_item_detail(pid, instance)
                before, other = snapshot(pid), snapshot(peer)
            sequence = [(401004, 3)] + [(10040, 4)] * 9
            # The third result's voucher overflows after a new explorer, weapon, poster and two
            # actual history and supplemental reward inserts. The socket remains usable for retry.
            with patch("private_gacha.outcome", side_effect=sequence) as roll:
                failure, notices = draw(first, 31)
                assert roll.call_count == 3
            assert failure.status == pb.StatusCode_Item_Get_Fail and not notices
            assert not failure.items and not failure.HasField("info")
            with game.lock, db.conn:
                assert snapshot(pid) == before and snapshot(peer) == other
                set_balance(pid, 107, 0)
                star = balance(pid, 111)
            statements = []
            with game.lock:
                db.conn.set_trace_callback(statements.append)
            try:
                with patch("private_gacha.outcome", side_effect=sequence):
                    result, notices = draw(first, 31)
            finally:
                with game.lock:
                    db.conn.set_trace_callback(None)
            assert result.status == pb.StatusCode_OK and len(result.items) == 10 and len(notices) == 1
            assert result.items[0].main_item.is_new and result.items[1].main_item.is_new
            assert all(not item.main_item.is_new for item in result.items[2:])
            assert result.info.gacha_times == 10 and result.info.guarantee == 66
            packs = [item.main_item for item in notices[0].items if item.main_item.WhichOneof("item") == "base_item"]
            assert Counter(i.item_id for i in packs) == {104: 1, 107: 1, 111: 1}
            expected = {104: 10, 107: 1500 + 500 * 9, 111: star + 20 * 8}
            assert {i.item_id: i.base_item.num for i in packs} == expected
            sql = [q.lower() for q in statements]
            for key in expected:
                writes = [q for q in sql if q.startswith("insert or replace into items (") and f"values ({pid}, {key}," in q]
                assert len(writes) == 1, (key, writes)
            scan = f"select item_detail_blob from items_s where player_id={pid}"
            assert sql.count(scan) == 1
            with game.lock:
                settled = snapshot(pid)
                assert {key: balance(pid, key) for key in expected} == expected
                assert len(settled["grants"]) == 10 and db.get_gacha_guarantee(pid, 2000) == 4
                for item, receipt in zip(result.items, settled["grants"]):
                    assert receipt[1:3] == ("draw", int(item.main_item.is_new))
                    assert json.loads(receipt[3]) == [[x.item_id, x.base_item.num] for x in (*item.transformed_item, *item.extras)]
            replay, notices = draw(first, 31)
            assert replay == result and not notices
            with game.lock:
                assert snapshot(pid) == settled and snapshot(peer) == other
            for packet, template in enumerate((10180, 10200, 10320), start=40):
                statements.clear()
                with game.lock:
                    db.conn.set_trace_callback(statements.append)
                try:
                    with patch("private_gacha.outcome", return_value=(template, 0)):
                        old, _ = draw(first, packet, single=True)
                finally:
                    with game.lock:
                        db.conn.set_trace_callback(None)
                assert old.status == pb.StatusCode_OK and not old.items[0].main_item.is_new
                assert {item.item_id: item.base_item.num for item in old.items[0].extras}[111] == 20
                assert scan not in (q.lower() for q in statements)
            # Reject the bonus INSERT in SQLite, after the ticket, results and pity were written.
            # The session closes on a storage error. No partial push may reach the socket.
            with game.lock, db.conn:
                stable = snapshot(pid)
                db.conn.execute(f"""CREATE TEMP TRIGGER fail_bonus BEFORE INSERT ON items
                    WHEN NEW.player_id={pid} AND NEW.item_id=107
                    BEGIN SELECT RAISE(ABORT,'fixture late failure'); END""")
            written = []
            before_insert = db.set_item_detail

            def observe_write(player, raw, item_id=None, instance_id=None):
                if player == pid and item_id == 107:
                    written.append((balance(pid, 104), db.get_total_gacha_num(pid, 2000),
                                    db.get_gacha_guarantee(pid, 2000)))
                return before_insert(player, raw, item_id, instance_id)

            failed_pushes = []
            with patch("private_gacha.outcome", return_value=(10040, 6)), patch.object(db, "set_item_detail", observe_write):
                try:
                    draw(first, 56, single=True, notices=failed_pushes)
                except (EOFError, ConnectionError):
                    pass
                else:
                    raise AssertionError("SQLite failure returned a successful native response")
            assert written == [(6, 14, 6)] and not failed_pushes
            with game.lock, db.conn:
                db.conn.execute("DROP TRIGGER fail_bonus")
                assert snapshot(pid) == stable
        game.stop()
        game.start()
        sock, _, same = login(game.port, owner.issue_ticket(*one))
        with sock:
            join(sock)
            with game.lock:
                assert same == pid and db.get_total_gacha_num(pid, 2000) == 13
                assert balance(pid, 104) == 7 and snapshot(peer) == other
            # Native sequence numbers restart per connection. A fresh draw with a previously
            # used number must still work; balance/history are the durable record across sessions.
            with patch("private_gacha.outcome", return_value=(10040, 1)):
                fresh, _ = draw(sock, 40, single=True)
            assert fresh.status == pb.StatusCode_OK and fresh.info.gacha_times == 14
    finally:
        game.stop()
    print("PASS ten-pull rollback on third grant, same-socket retry and replay, roster, notices, ledger, "
          "pity, player isolation, restart and old poster migration. One SQL write and pack entry per stack.")


def outer_commit_rollback(root, resources):
    root.mkdir()
    owner = LocalIdentity(root / "identity.sqlite3")
    profile = owner.create_profile("atomic-outer")
    game = LocalGameService(owner, root, resources, port=0)
    game.start()
    try:
        sock, _, pid = login(game.port, owner.issue_ticket(*profile))
        with sock:
            join_after_intro(sock)
            call(sock, "PlayerPing", 70)
            with game.lock, db.conn:
                session = next(s for s in game.active.values() if s.player_id == pid)
                db.conn.execute("PRAGMA foreign_keys=ON")
                assert db.conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
                db.conn.execute("CREATE TEMP TABLE fk_parent(id INTEGER PRIMARY KEY)")
                db.conn.execute("CREATE TEMP TABLE fk_child(id INTEGER REFERENCES fk_parent(id) "
                                "DEFERRABLE INITIALLY DEFERRED)")
                quest = pb.Quest(quest_id=STEPS[-1], status=pb.QuestStatus_InProgress)
                quest.conditions.add(condition_id=CONDITIONS[-1], status=pb.QuestStatus_InProgress)
                save(session, quest)
                set_balance(pid, 104, 5)
                set_balance(pid, 101, 500)
                baseline = snapshot(pid)

            def reject_commit(name, req, packet, check_pending):
                drain = game._drain_scene_events

                def break_commit():
                    drain()
                    assert db.conn.in_transaction and session.commit_callbacks and session.responses
                    check_pending()
                    db.conn.execute("INSERT INTO fk_child VALUES(1)")  # succeeds until COMMIT

                with game.lock, patch.object(game, "_drain_scene_events", break_commit):
                    try:
                        game._handle(session, getattr(MsgId, name + "Req"), packet, req.SerializeToString())
                    except sqlite3.IntegrityError as failure:
                        assert failure.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY
                    else:
                        raise AssertionError("Expected outer COMMIT to fail")
                    assert not db.conn.in_transaction and not session.commit_callbacks and not session.responses
                    assert session not in game.pending_publish
                    assert not db.conn.execute("SELECT 1 FROM fk_child").fetchall()

            def tutorial_pending():
                assert saved(pid, quest.quest_id).status == pb.QuestStatus_Finish and balance(pid, 104) == 4
                assert session.quests[quest.quest_id].status == pb.QuestStatus_InProgress
                assert not getattr(session, "gacha_receipts", None)

            reject_commit("Gacha", pb.GachaReq(gacha_id=2000, is_single=True), 71, tutorial_pending)
            with game.lock:
                assert snapshot(pid) == baseline and quest.quest_id in session.quests
                assert not getattr(session, "gacha_receipts", None)
            first = call(sock, "Gacha", 71, gacha_id=2000, is_single=True)
            assert first.status == pb.StatusCode_OK and first.items[0].main_item.item_id == 102001
            assert call(sock, "Gacha", 71, gacha_id=2000, is_single=True) == first
            with game.lock:
                assert quest.quest_id not in session.quests and balance(pid, 104) == 4
                assert db.get_total_gacha_num(pid, 2000) == 1 and len(session.gacha_receipts) == 1

            flag = pb.HideType_SIGN_Type
            initial = db.get_players_info(pid, "hide_value")[0] or 0

            def profile_pending():
                assert db.get_players_info(pid, "hide_value")[0] == initial ^ flag
                assert not getattr(session, "mutation_receipts", None)

            reject_commit("ChangeHideType", pb.ChangeHideTypeReq(hide_type=flag), 72, profile_pending)
            with game.lock:
                assert (db.get_players_info(pid, "hide_value")[0] or 0) == initial
                assert not getattr(session, "mutation_receipts", None)
            for _ in range(2):
                result = call(sock, "ChangeHideType", 72, hide_type=flag)
                assert result.status == pb.StatusCode_OK and result.hide_value == initial ^ flag
            with game.lock:
                assert db.get_players_info(pid, "hide_value")[0] == initial ^ flag
                assert len(session.mutation_receipts) == 1

            goods = dict(shop_id=11000, grid_id=1, buy_times=1, item_id=5011001)
            stock = balance(pid, 5011001)

            def shop_pending():
                assert balance(pid, 101) == 490 and balance(pid, 5011001) == stock + 10
                assert not getattr(session, "last_shop_purchase", None)

            reject_commit("ShopBuy", pb.ShopBuyReq(**goods), 73, shop_pending)
            with game.lock:
                assert balance(pid, 101) == 500 and balance(pid, 5011001) == stock
                assert not getattr(session, "last_shop_purchase", None)
            for _ in range(2):
                assert call(sock, "ShopBuy", 73, **goods).status == pb.StatusCode_OK
            with game.lock:
                assert balance(pid, 101) == 490 and balance(pid, 5011001) == stock + 10
    finally:
        game.stop()
    print("PASS rejected outer COMMIT discards native notifications, tutorial quest cache, draws, "
          "profile and shop receipts; same-session retries commit once.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    with TemporaryDirectory() as tmp:
        resources = parser.parse_args().resources
        run(Path(tmp), resources)
        outer_commit_rollback(Path(tmp) / "outer", resources)
