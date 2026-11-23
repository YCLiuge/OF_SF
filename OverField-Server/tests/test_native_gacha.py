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
from realm_gacha import available, configure, defaults
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
        assert sorted(results.values()) == [200, 700, 1200, 7900]
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
            assert balance(110) == 20 and balance(34104) == shard_before + 9
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as temp:
        run(Path(temp), args.resources)
