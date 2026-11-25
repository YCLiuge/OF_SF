"""Exercise outdoor moon fragments independently from ordinary chest and gathering loot."""
import argparse
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from realm_rules import configure
from test_game_realm import join_after_intro, login, next_msg
from test_native_events import NOTICES, rpc
from test_native_gacha import call
from test_native_gacha_atomicity import balance, set_balance
from test_native_ground_loot import encounter, travel
from utils import db


def run(root, resources):
    identities = LocalIdentity(root / "identity.sqlite3")
    profiles = [identities.create_profile(n) for n in ("碎片甲", "碎片乙")]
    configure(root, {"activity_fragment_percent": 100})
    host = LocalGameService(identities, root, resources, port=0)
    seq = count(100)
    OK = pb.StatusCode_OK
    NOTICES[MsgId.MoonQuestGamePlayRewardNotice] = pb.MoonQuestGamePlayRewardNotice

    def connect(i):
        sock, _, pid = login(host.port, identities.issue_ticket(*profiles[i]))
        main, _ = join_after_intro(sock)
        lead = next(c for c in main.characters if c.character_id == main.team.char1)
        travel(sock, next(seq))
        return sock, pid, lead

    def open_box(sock, index):
        return call(sock, "TreasureBoxOpen", next(seq), box_type=0, treasure_box_index=index, loc_type=0)

    def wallet(pid):
        with host.lock:
            return balance(pid, 124)

    def check_notice(notice, source, amount):
        assert notice.status == OK and notice.id == source, notice
        assert [(i.main_item.item_id, i.main_item.base_item.num) for i in notice.rewards] == [(124, amount)]

    def chest_award(sock, idx, pid, delta):
        before = wallet(pid)
        assert open_box(sock, idx).items
        notice = next_msg(sock, MsgId.MoonQuestGamePlayRewardNotice, pb.MoonQuestGamePlayRewardNotice)
        check_notice(notice, 1120, delta)
        pack = next_msg(sock, MsgId.PackNotice, pb.PackNotice)
        assert any(i.main_item.item_id == 124 and i.main_item.base_item.num == before + delta for i in pack.items)
        assert wallet(pid) == before + delta

    host.start()
    try:
        a, pid, lead = connect(0)
        b, other, _ = connect(1)
        with a, b:
            before, before_other = wallet(pid), wallet(other)
            assert open_box(a, 999999).status != OK
            chest_award(a, 17, pid, 5)
            assert open_box(a, 17).items and wallet(pid) == before + 5
            assert encounter(a, next(seq)).status == OK
            key = next(seq)
            fields = {"monster_id": 1205, "monster_index": 0}
            result, notes = rpc(a, "MonsterDead", key, **fields)
            assert result.status == OK
            moon, = (n for n in notes if isinstance(n, pb.MoonQuestGamePlayRewardNotice))
            check_notice(moon, 1101, 5)
            assert call(a, "MonsterDead", key, **fields).status == OK
            assert call(a, "MonsterDead", next(seq), **fields).status == OK
            assert wallet(pid) == before + 10
            before += 5
            assert wallet(other) == before_other
            chest_award(b, 17, other, 5)
            fields = {"index": 311166090, "reward": 200000101}
            assert call(a, "Gather", next(seq), gather_item=fields).status != OK
            assert wallet(pid) == before + 5
            assert call(a, "CharacterGatherWeaponUpdate", next(seq), character_id=lead.character_id,
                        weapon_id=lead.character_appearance.logging_axe_instance_id).status == OK
            packet = next(seq)
            gathered, notices = rpc(a, "Gather", packet, gather_item=fields)
            assert gathered.status == OK and gathered.items
            moon, = (n for n in notices if isinstance(n, pb.MoonQuestGamePlayRewardNotice))
            check_notice(moon, 1106, 5)
            assert call(a, "Gather", packet, gather_item=fields) == gathered and wallet(pid) == before + 10
            assert call(a, "TreasureBoxPickup", next(seq), box_type=0, box_index=17, loc_type=0, pick_index=-1).items
            assert not open_box(a, 17).items and wallet(pid) == before + 10
        host.stop()
        configure(root, {"activity_fragment_quantity": 3})
        host = LocalGameService(identities, root, resources, port=0)
        host.start()
        a, same, _ = connect(0)
        with a:
            assert pid == same and not open_box(a, 17).items and wallet(pid) == before + 10
            with host.lock, db.conn:
                db.conn.execute("UPDATE private_boxes SET refresh=1 WHERE player_id=? AND zone=1 AND dynamic=0 AND idx=17", (pid,))
            chest_award(a, 17, pid, 3)
            with host.lock, db.conn:
                set_balance(pid, 124, 0xffffffff)
            assert open_box(a, 28).items and wallet(pid) == 0xffffffff
        host.stop()
        configure(root, {"activity_fragment_percent": 0})
        host = LocalGameService(identities, root, resources, port=0)
        host.start()
        b, other, _ = connect(1)
        with b:
            saved = wallet(other)
            assert open_box(b, 28).items and wallet(other) == saved
        print("PASS outdoor fragments: native chest and harvest notices, independent loot, no duplicate claims, "
              "refresh, restart, two roles, full stack, disabled and custom amount.")
    finally:
        host.stop()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--resources", type=Path, required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp), p.parse_args().resources.resolve())
