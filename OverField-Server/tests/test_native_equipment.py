"""Exercise owned equipment instances through native private-realm TCP packets."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from test_game_realm import join, login
from test_native_gacha import call
from private_equipment import load, property_group
from private_dungeons import _table
from utils import db
from utils.pb_create import make_item
from utils.res_loader import res


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    one, two = (identity.create_profile(name) for name in ("装备主甲", "装备主乙"))
    host = LocalGameService(identity, root, resources, port=0)
    host.start()
    try:
        sock, _, pid = login(host.port, identity.issue_ticket(*one))
        peer, _, _ = login(host.port, identity.issue_ticket(*two))
        with sock, peer:
            join(sock)
            join(peer)
            def equipment(kind):
                cat = _table("Item", "item")
                tag = getattr(pb, "EBagItemTag_" + kind.title())
                for i in res["ReleasedEquipment"]["equipment_ids"]:
                    if cat[i]["new_bag_item_tag"] != tag:
                        continue
                    item = make_item(i, 1, pid)
                    if not item or item.main_item.WhichOneof("item") != kind:
                        continue
                    obj = getattr(item.main_item, kind)
                    cfg = property_group(kind, obj)
                    if cfg.get("strengthen_i_d") and cfg.get("disassemble_i_d"):
                        db.set_item_detail(pid, item.SerializeToString(), instance_id=obj.instance_id)
                        return obj.instance_id
                raise AssertionError("No suitable equipment")
            def stock(i, n):
                db.set_item_detail(pid, make_item(i, n, pid).SerializeToString(), item_id=i)
            def balance(i):
                return pb.ItemDetail.FromString(db.get_item_detail(pid, i)).main_item.base_item.num
            with host.lock, db.conn:
                weapon, armor, other = equipment("weapon"), equipment("armor"), equipment("weapon")
                stone = make_item(20001, 1, pid)
                db.set_item_detail(pid, stone.SerializeToString(), item_id=20001)
                for i in (101, 205, 250, 270):
                    stock(i, 10000)
            assert call(peer, "WeaponStrength", 28, weapon_instance_id=weapon).status == pb.StatusCode_BAD_REQ
            for kind, key in (("weapon", weapon), ("armor", armor)):
                name = kind.title() + "Strength"
                item = load(pid, key, kind)
                group = property_group(kind, getattr(item.main_item, kind))
                rule = _table(kind.title(), kind + "_strengthen")[group["strengthen_i_d"]]
                costs = next(r for r in rule[kind + "_strengthen_group_info"] if r["level"] == 1)
                needs = costs[kind + "_strength_need_item"]
                with host.lock, db.conn:
                    for part in needs:
                        stock(part["need_item_i_d"], 10000)
                first = needs[0]["need_item_i_d"]
                before = balance(first)
                request = {kind + "_instance_id": key}
                result = call(sock, name, 31, **request)
                assert result.status == pb.StatusCode_OK and result.strength_level == 1, result
                assert balance(first) == before - sum(x["need_item_count"] for x in needs if x["need_item_i_d"] == first)
                assert call(sock, name, 31, **request) == result
                assert getattr(load(pid, key, kind).main_item, kind).strength_level == 1
            assert call(sock, "LockEquip", 32, instance_id=armor, equip_type=1).status == pb.StatusCode_OK
            assert call(sock, "LockEquip", 32, instance_id=armor, equip_type=1).status == pb.StatusCode_OK
            assert load(pid, armor, "armor").main_item.armor.is_lock
            assert call(sock, "EquipDisassemble", 33, armor_ids=[armor]).status == pb.StatusCode_BAD_REQ
            before = balance(101)
            inlay = call(sock, "WeaponInscriptionInlaid", 34, weapon_instance_id=weapon, inscription_id=20001)
            assert inlay.status == pb.StatusCode_OK and inlay.inscriptions[0].weapon_instance_id == weapon
            assert balance(101) == before - 1000 and load(pid, weapon, "weapon").main_item.weapon.inscription1 == 20001
            assert call(sock, "EquipDisassemble", 35, weapon_ids=[weapon]).status == pb.StatusCode_BAD_REQ
            with host.lock, db.conn:
                stock(205, 0)
            failed = call(sock, "WeaponInscriptionInlaid", 36, weapon_instance_id=other, inscription_id=20001)
            assert failed.status == pb.StatusCode_ITEM_NOT_ENOUGH and balance(101) == before - 1000
            assert load(pid, weapon, "weapon").main_item.weapon.inscription1 == 20001
            with host.lock, db.conn:
                stock(205, 2)
            assert call(sock, "WeaponInscriptionInlaid", 37, weapon_instance_id=other, inscription_id=20001).status == pb.StatusCode_OK
            assert not load(pid, weapon, "weapon").main_item.weapon.inscription1
            assert call(sock, "WeaponInscriptionRemove", 38, weapon_instance_id=other).status == pb.StatusCode_OK
            assert not load(pid, 20001, "inscription").main_item.inscription.weapon_instance_id
            worn = pb.Character.FromString(db.get_characters(pid)[0]).equipment_presets[0].weapon
            assert call(sock, "EquipDisassemble", 39, weapon_ids=[weapon, worn]).status == pb.StatusCode_BAD_REQ
            assert load(pid, weapon, "weapon")
            done = call(sock, "EquipDisassemble", 40, weapon_ids=[weapon])
            assert done.status == pb.StatusCode_OK and list(done.weapon_ids) == [weapon] and done.items
            assert not load(pid, weapon, "weapon")
        host.stop()
        host.start()
        assert load(pid, armor, "armor").main_item.armor.is_lock
        assert load(pid, armor, "armor").main_item.armor.strength_level == 1
        assert not load(pid, weapon, "weapon")
    finally:
        host.stop()
    print("PASS native lock, weapon/armor strength, material debit, inscription move/remove, protected disassembly, player isolation, retries and restart.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as temp:
        run(Path(temp), args.resources)
