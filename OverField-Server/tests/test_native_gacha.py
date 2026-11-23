"""Exercise 1.1.12 recruit requests through a disposable live private realm."""
import argparse
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from test_game_realm import join, login
from realm_gacha import available, configure, defaults, validate
from private_gacha import outcome
from utils import db
from utils.pb_create import make_item


def call(sock, name, packet_id, **fields):
    req = getattr(pb, name + "Req")(**fields)
    sock.sendall(encode(getattr(MsgId, name + "Req"), req.SerializeToString(), packet_id))
    for _ in range(256):
        head, raw = receive(sock)
        if head.msg_id == getattr(MsgId, name + "Rsp") and head.packet_id == packet_id:
            return getattr(pb, name + "Rsp").FromString(raw)
    raise AssertionError(name)


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profile = identity.create_profile("原生招募测试")
    service = LocalGameService(identity, root, resources, port=0)
    settings = defaults()
    assert {r["id"] for r in available()} >= {2000, 3000, 3001}
    bad = {"pools": [dict(settings["pools"][0], weights={"100": 9999})]}
    try:
        configure(root, bad)
        raise AssertionError("Accepted invalid odds")
    except ValueError:
        pass
    service.start()
    try:
        # Enumerate every integer threshold (the second RNG call selects group member zero).
        results = Counter()
        for n in range(10000):
            values = iter((n, 0))
            item, _ = outcome(settings["pools"][0], 0, 400, lambda size: next(values))
            results[item] += 1
        assert sorted(results.values()) == [80, 100, 600, 9220]
        sock, _, pid = login(service.port, identity.issue_ticket(*profile))
        with sock:
            join(sock)
            assert [r.gacha_id for r in call(sock, "GachaList", 4).gachas] == [2000, 3000, 3001, 3023, 203014, 203015, 9000]
            def balance(item):
                raw = db.get_item_detail(pid, item)
                return pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0
            with service.lock, db.conn:
                for ticket in (104, 110):
                    db.set_item_detail(pid, make_item(ticket, 30, pid).SerializeToString(), ticket)
                # Keep this fixture valid both with a full initial collection and on older saves.
                db.conn.execute("DELETE FROM characters WHERE player_id=? AND character_id=401004", (pid,))
            for pool in settings["pools"]:
                if pool["id"] == 3000:
                    pool.update(pity=1)
                if pool["id"] == 3001:
                    pool.update(enabled=False)
            configure(root, settings)
            assert [r.gacha_id for r in call(sock, "GachaList", 5).gachas] == [2000, 3000, 3023, 203014, 203015, 9000]
            assert call(sock, "Gacha", 6, gacha_id=3001, is_single=True).status == pb.StatusCode_BAD_REQ
            assert call(sock, "GachaFullPick", 7, gacha_id=2000, item_id=302002).status == pb.StatusCode_BAD_REQ
            assert call(sock, "ChooseGacha", 70, gacha_id=9000, choose_gacha_pool=3001).status == pb.StatusCode_BAD_REQ
            assert call(sock, "ChooseGacha", 71, gacha_id=9000, choose_gacha_pool=3000).status == pb.StatusCode_OK
            shard_before = balance(34104)
            ten = call(sock, "Gacha", 8, gacha_id=3000)
            assert ten.status == pb.StatusCode_OK and len(ten.items) == 10 and ten.info.gacha_times == 10
            assert all(i.main_item.character.character_id == 401004 for i in ten.items)
            assert ten.items[0].main_item.is_new and all(i.extras[0].item_id == 34104 for i in ten.items[1:])
            assert balance(110) == 20 and balance(34104) == shard_before + 27
            assert call(sock, "Gacha", 8, gacha_id=3000) == ten and balance(110) == 20
            hist = call(sock, "GachaRecord", 9, gacha_id=3000, page=2)
            assert hist.total_page == 2 and len(hist.records) == 5 and hist.gacha_id == 3000 and hist.page == 2
            archive_pull = call(sock, "Gacha", 99, gacha_id=9000, is_single=True)
            assert archive_pull.status == pb.StatusCode_OK and archive_pull.info.choose_pool_id == 3000
            assert archive_pull.items[0].main_item.character.character_id == 401004 and balance(110) == 19
            settings["pools"][0]["weights"] = {"100": 10000}
            configure(root, settings)
            single = call(sock, "Gacha", 10, gacha_id=2000, is_single=True)
            assert single.status == pb.StatusCode_OK and single.items[0].main_item.poster.instance_id
            assert balance(104) == 29 and db.get_item_detail(pid, instance_id=single.items[0].main_item.poster.instance_id)
            with service.lock, db.conn:
                db.set_item_detail(pid, make_item(104, 3, pid).SerializeToString(), 104)
            assert call(sock, "Gacha", 11, gacha_id=2000).status == pb.StatusCode_ITEM_NOT_ENOUGH
            assert balance(104) == 3 and db.get_total_gacha_num(pid, 2000) == 1
            # Inject a database write-path failure after the fee has been deducted.
            # The normal host transaction must undo both the fee and all history.
            with patch("private_gacha.award", side_effect=ValueError("test rollback")):
                try:
                    call(sock, "Gacha", 12, gacha_id=2000, is_single=True)
                    raise AssertionError("Failed draw was accepted")
                except (EOFError, OSError):
                    pass
        service.stop()
        service.start()
        sock, _, _ = login(service.port, identity.issue_ticket(*profile))
        with sock:
            join(sock)
            assert balance(104) == 3 and balance(110) == 19 and db.get_total_gacha_num(pid, 2000) == 1
            restored = next(p for p in call(sock, "GachaList", 19).gachas if p.gacha_id == 9000)
            assert restored.choose_pool_id == 3000 and restored.gacha_times == 1
            settings["pools"][1]["enabled"] = False
            configure(root, settings)
            assert [r.gacha_id for r in call(sock, "GachaList", 20).gachas] == [2000, 3023, 203014, 203015]
            assert len(call(sock, "GachaRecord", 21, gacha_id=3000, page=1).records) == 5
    finally:
        service.stop()
    print("PASS native recruit catalog, live editing, exact odds, pity, ten pulls, debit, inventory, replay, rollback and restart.")


def permanent_guarantees(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profile = identity.create_profile("常驻保底")
    game = LocalGameService(identity, root, resources, port=0)
    settings = defaults()
    pool = next(p for p in settings["pools"] if p["id"] == 2000)
    assert pool["weights"] == {"100": 9220, "200": 80, "300": 600, "400": 100}
    up = next(p for p in settings["pools"] if p["id"] == 3000)
    for option in (dict(pool, sr_pity=-1), dict(pool, sr_pity=1001), dict(pool, sr_pity=True), dict(up, sr_pity=10)):
        try:
            validate({"pools": [option]})
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid SR guarantee setting was accepted")
    assert pool.pop("sr_pity") == 10 and pool["pity"] == 70
    pool["weights"] = {"200": 10000}  # Always an SSR poster on a non-guaranteed pull.
    assert "sr_pity" not in configure(root, settings)["pools"][0]
    from private_dungeons import _table

    def connect():
        sock, _, pid = login(game.port, identity.issue_ticket(*profile))
        join(sock)
        return sock, pid

    def draw(sock, packet, single=True):
        reply = call(sock, "Gacha", packet, gacha_id=2000, is_single=single)
        assert reply.status == pb.StatusCode_OK
        return [(_table("Item", "item")[i.main_item.item_id]["quality"], i.main_item.WhichOneof("item"))
                for i in reply.items], reply.info.guarantee

    game.start()
    try:
        sock, pid = connect()
        with sock:
            with game.lock, db.conn:
                db.set_item_detail(pid, make_item(104, 150, pid).SerializeToString(), 104)
            for i in range(1, 10):
                assert draw(sock, 100+i) == ([(4, "poster")], 70-i)
        game.stop()
        game.start()
        sock, same_pid = connect()
        with sock:
            assert same_pid == pid
            # Nine actual singles before restarting carry over into the next ten-pull.
            for i in range(10, 70, 10):
                outcomes, remaining = draw(sock, 100+i, False)
                assert outcomes == [(3, "character")] + [(4, "poster")] * 9
                assert remaining == 70-(i+9)
            # A legacy save may have full draw history and no dedicated counter row.
            with game.lock, db.conn:
                db.conn.execute("DELETE FROM gacha_guarantee WHERE player_id=? AND gacha_id=2000", (pid,))
            assert next(p for p in call(sock, "GachaList", 169).gachas if p.gacha_id == 2000).guarantee == 1
            assert draw(sock, 170) == ([(4, "character")], 70)
            assert draw(sock, 170) == ([(4, "character")], 70)
            with game.lock:
                assert db.get_total_gacha_num(pid, 2000) == 70
            pool.update(sr_pity=3, pity=4)
            configure(root, settings)
            assert draw(sock, 171) == ([(4, "poster")], 3)
            assert draw(sock, 172) == ([(4, "poster")], 2)
            assert draw(sock, 173) == ([(3, "character")], 1)
            assert draw(sock, 174) == ([(4, "character")], 4)
            pool.update(sr_pity=0, pity=1000)
            configure(root, settings)
            assert draw(sock, 175, False) == ([(4, "poster")] * 10, 990)
            pool["weights"] = {"400": 10000}
            configure(root, settings)
            assert draw(sock, 176) == ([(4, "character")], 1000)
            assert db.get_total_gacha_num(pid, 2000) == 85
    finally:
        game.stop()
    print("PASS permanent banner 10/70 explorer guarantees, historical single/ten carry-over, poster exclusion, simultaneous guarantees, owner overrides and retry.")


def post_draw_interactions(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profiles = [identity.create_profile(n) for n in ("结果甲", "结果乙", "结果丙")]
    game = LocalGameService(identity, root, resources, port=0)

    def connect(index):
        sock, _, pid = login(game.port, identity.issue_ticket(*profiles[index]))
        join(sock)
        return sock, pid

    def playback(sock, sequence, kind, action_id, **data):
        return call(sock, "SceneInterActionPlayStatus", sequence, push_type=kind,
                    action_status=pb.ScenePlayerActionStatus(id=action_id, **data))

    def notices_before_ping(sock, sequence):
        sock.sendall(encode(MsgId.PlayerPingReq, pb.PlayerPingReq().SerializeToString(), sequence))
        found = []
        for _ in range(200):
            h, raw = receive(sock)
            if h.msg_id == MsgId.SceneInterActionPlayStatusNotice:
                found.append(pb.SceneInterActionPlayStatusNotice.FromString(raw))
            if h.msg_id == MsgId.PlayerPingRsp and h.packet_id == sequence:
                return found
        raise AssertionError("Missing heartbeat reply")

    game.start()
    try:
        a, pid = connect(0)
        peer, _ = connect(1)
        elsewhere, _ = connect(2)
        with a, peer, elsewhere:
            assert call(elsewhere, "ChangeSceneChannel", 9, scene_id=9999, channel_label=2).status == pb.StatusCode_OK
            with game.lock, db.conn:
                db.set_item_detail(pid, make_item(104, 5, pid).SerializeToString(), 104)
            with patch("private_gacha.outcome", return_value=(11240, 0)):
                # The private client's observed four-request sequence (1445 → 1423 → 1919 → 1449).
                assert call(a, "Gacha", 10, gacha_id=2000, is_single=True).status == pb.StatusCode_OK
                assert call(a, "PosterIllustrationList", 11).status == pb.StatusCode_OK
                assert playback(a, 12, pb.Immediately, 7001, value1=23).status == pb.StatusCode_OK
                assert call(a, "GachaRecord", 13, gacha_id=2000, page=1).records[0].item_id == 11240
            echoed = notices_before_ping(peer, 14)
            assert len(echoed) == 1 and echoed[0].push_type == pb.Immediately
            assert echoed[0].action_status.value1 == 23 and echoed[0].player_id == pid
            assert not notices_before_ping(elsewhere, 15)
            assert playback(a, 16, pb.AddKeepingStatus, 7001, value1=41).status == pb.StatusCode_OK
            kept = notices_before_ping(a, 17)
            assert len(kept) == 1 and kept[0].action_status.value1 == 41
            with game.lock:
                current = next(s for s in game.active.values() if s.player_id == pid).scene_player
                assert current.status.id == 7001 and current.status.value1 == 41
            assert playback(a, 18, pb.RemovingStatus, 7001).status == pb.StatusCode_OK
            with game.lock:
                assert not current.HasField("status")
            assert playback(a, 19, pb.AddKeepingStatus, 0).status == pb.StatusCode_BAD_REQ
            assert call(a, "SceneInterActionPlayStatus", 20).status == pb.StatusCode_OK
            assert playback(a, 21, pb.AddKeepingStatus, 7001, value1=41).status == pb.StatusCode_OK
            assert call(a, "ChangeSceneChannel", 22, scene_id=9999, channel_label=2).status == pb.StatusCode_OK
            with game.lock:
                assert not current.HasField("status") and db.get_total_gacha_num(pid, 2000) == 1
                assert not db.conn.execute("SELECT 1 FROM furnitures WHERE player_id=?", (pid,)).fetchone()
    finally:
        game.stop()
    print("PASS native post-draw 1445/1423/1919/1449 sequence, room-scoped 1922 playback, kept-state release and channel cleanup.")


def poster_bonuses(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profile = identity.create_profile("招募附奖")
    game = LocalGameService(identity, root, resources, port=0)

    def total(player, item):
        raw = db.get_item_detail(player, item)
        return pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0

    def draw(sock, sequence):
        request = pb.GachaReq(gacha_id=2000, is_single=True)
        sock.sendall(encode(MsgId.GachaReq, request.SerializeToString(), sequence))
        updated = {}
        for _ in range(200):
            h, data = receive(sock)
            if h.msg_id == MsgId.PackNotice:
                for item in pb.PackNotice.FromString(data).items:
                    updated[item.main_item.item_id] = item.main_item.base_item.num
            if h.msg_id == MsgId.GachaRsp and h.packet_id == sequence:
                result = pb.GachaRsp.FromString(data)
                assert result.status == pb.StatusCode_OK
                extras = {item.item_id: item.base_item.num for item in result.items[0].extras}
                return result, extras, updated
        raise AssertionError("Missing recruit response")

    game.start()
    try:
        sock, _, pid = login(game.port, identity.issue_ticket(*profile))
        with sock:
            join(sock)
            with game.lock, db.conn:
                db.set_item_detail(pid, make_item(104, 10, pid).SerializeToString(), 104)
                baseline = {i: total(pid, i) for i in (107, 111, 112)}
            with patch("private_gacha.outcome", return_value=(11240, 0)):
                first, extras, updated = draw(sock, 51)
                assert first.items[0].main_item.is_new and extras == {107: 50}
                assert updated[107] == baseline[107] + 50
                second, extras, updated = draw(sock, 52)
                assert not second.items[0].main_item.is_new and extras == {112: 15, 107: 50}
                assert updated[112] == baseline[112] + 15 and updated[107] == baseline[107] + 100
                assert draw(sock, 52)[0] == second
                with game.lock:
                    assert total(pid, 112) == baseline[112] + 15 and total(pid, 107) == baseline[107] + 100
            with patch("private_gacha.outcome", return_value=(401004, 0)):
                first, extras, _ = draw(sock, 53)
                assert first.items[0].main_item.is_new and extras == {107: 1500}
                second, extras, _ = draw(sock, 54)
                assert not second.items[0].main_item.is_new and extras == {34104: 3, 111: 16, 107: 1500}
                with game.lock, db.conn:
                    character = pb.Character.FromString(db.get_characters(pid, 401004)[0])
                    character.star = 5
                    db.set_character(pid, 401004, character.SerializeToString())
                _, extras, _ = draw(sock, 55)
                assert extras == {111: 40, 107: 1500}
        game.stop()
        game.start()
        sock, _, restored = login(game.port, identity.issue_ticket(*profile))
        with sock:
            join(sock)
            assert restored == pid
            with game.lock:
                assert total(pid, 107) == baseline[107] + 4600 and total(pid, 111) == baseline[111] + 56
                assert total(pid, 112) == baseline[112] + 15 and total(pid, 104) == 5
    finally:
        game.stop()
    print("PASS SR poster duplicate bonus, per-pull vouchers, SSR character duplicate and full-star grants, pack balances, replay and restart.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as temp:
        run(Path(temp), args.resources)
    with TemporaryDirectory() as temp:
        poster_bonuses(Path(temp), args.resources)
    with TemporaryDirectory() as temp:
        permanent_guarantees(Path(temp), args.resources)
    with TemporaryDirectory() as temp:
        post_draw_interactions(Path(temp), args.resources)
