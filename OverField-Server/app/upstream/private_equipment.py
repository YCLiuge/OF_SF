"""Equipment operations on the authenticated player's persistent inventory."""
from collections import Counter
from private_request import cache_after_commit
import random

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils import db
from private_crafts import payments
from private_dungeons import _table, grant


def load(pid, key, kind):
    if not key:
        return None
    raw = db.get_item_detail(pid, key) if kind == "inscription" else db.get_item_detail(pid, instance_id=key)
    if raw:
        item = pb.ItemDetail.FromString(raw)
        if item.main_item.WhichOneof("item") == kind:
            value = getattr(item.main_item, kind)
            if (value.inscription_id if kind == "inscription" else value.instance_id) == key:
                return item
    return None


def save(session, changed, costs=None):
    notice = pb.PackNotice(status=pb.StatusCode_OK)
    for key, item in (costs or {}).items():
        db.set_item_detail(session.player_id, item.SerializeToString(), item_id=key)
        notice.items.add().CopyFrom(item)
    for item in changed:
        kind = item.main_item.WhichOneof("item")
        if kind == "inscription":
            db.set_item_detail(session.player_id, item.SerializeToString(), item_id=item.main_item.item_id)
        else:
            instance = getattr(item.main_item, kind)
            db.set_item_detail(session.player_id, item.SerializeToString(), instance_id=instance.instance_id)
        notice.items.add().CopyFrom(item)
    if notice.items:
        session.send(MsgId.PackNotice, notice, 0)


def property_group(kind, equipment):
    root = kind.title()
    config = _table(root, kind).get(getattr(equipment, kind + "_id"), {})
    prop = _table(root, kind + "_property").get(config.get(kind + "_property_i_d"), {})
    groups = prop.get(kind + "_property_group_info", [])
    if 1 <= equipment.property_index <= len(groups):
        return groups[equipment.property_index - 1]
    return next((g for g in groups if g.get("min_level", 0) <= equipment.level <= g.get("max_level", 0)), {})


def lock(session, req, rsp):
    if req.equip_type not in (0, 1):
        return pb.StatusCode_BAD_REQ
    kind = ("weapon", "armor")[req.equip_type]
    item = load(session.player_id, req.instance_id, kind)
    if not item:
        return pb.StatusCode_BAD_REQ
    obj = getattr(item.main_item, kind)
    obj.is_lock = not obj.is_lock
    save(session, [item])
    return pb.StatusCode_OK


def strengthen(session, req, rsp, kind):
    key = getattr(req, kind + "_instance_id")
    setattr(rsp, kind + "_instance_id", key)
    item = load(session.player_id, key, kind)
    if not item:
        return pb.StatusCode_BAD_REQ
    obj = getattr(item.main_item, kind)
    config = _table(kind.title(), kind + "_strengthen").get(property_group(kind, obj).get("strengthen_i_d"))
    if not config:
        return pb.StatusCode_CFG_NOT_FOUND
    level = obj.strength_level + 1
    entry = next((g for g in config[kind + "_strengthen_group_info"] if g.get("level") == level), None)
    if entry is None:
        rsp.strength_level = obj.strength_level
        return getattr(pb, "StatusCode_" + kind.upper() + "_MAX_STRENGTH_LEVEL")
    needs = Counter()
    for part in entry.get(kind + "_strength_need_item", []):
        needs[part["need_item_i_d"]] += part["need_item_count"]
    costs, status = payments(session.player_id, needs)
    if costs is None:
        return status
    obj.strength_level, obj.strength_exp = level, 0
    rsp.strength_level = level
    save(session, [item], costs)
    from private_equipment_presets import sync_scene_equipment
    sync_scene_equipment(session, instances=(key,))
    from private_newcomer import record
    record(session, 123)
    return pb.StatusCode_OK


def inscription(session, req, rsp, remove=False):
    pid = session.player_id
    key = req.weapon_instance_id
    weapon = load(pid, key, "weapon")
    if not weapon:
        return pb.StatusCode_BAD_REQ
    slot = weapon.main_item.weapon
    if remove:
        rsp.weapon_instance_id = key
    new_id = 0 if remove else req.inscription_id
    if not remove and not new_id:
        return pb.StatusCode_BAD_REQ
    if new_id == slot.inscription1:
        if not remove:
            rsp.weapon_instances.add().CopyFrom(slot)
        return pb.StatusCode_OK
    changed = [weapon]
    costs = {}
    if new_id:
        new = load(pid, new_id, "inscription")
        if not new:
            return pb.StatusCode_ITEM_NOT_ENOUGH
        groups = _table("Inscription", "inscription").get(new_id, {}).get("inscription_group_info", [])
        rule = next((g for g in groups if g.get("level") == 1), None)
        if not rule:
            return pb.StatusCode_CFG_NOT_FOUND
        needs = Counter()
        if rule.get("need_gold_count"):
            needs[101] += rule["need_gold_count"]
        for part in rule.get("inscription_need_item", []):
            needs[part["need_item_i_d"]] += part["item_count"]
        costs, status = payments(pid, needs)
        if costs is None:
            return status
        old_key = new.main_item.inscription.weapon_instance_id
        if old_key and old_key != key:
            other = load(pid, old_key, "weapon")
            if not other or other.main_item.weapon.inscription1 != new_id:
                return pb.StatusCode_BAD_REQ
            other.main_item.weapon.inscription1 = 0
            changed.append(other)
        new.main_item.inscription.weapon_instance_id = key
        changed.append(new)
    if slot.inscription1:
        old = load(pid, slot.inscription1, "inscription")
        if not old or old.main_item.inscription.weapon_instance_id != key:
            return pb.StatusCode_BAD_REQ
        old.main_item.inscription.weapon_instance_id = 0
        changed.append(old)
    slot.inscription1 = new_id
    save(session, changed, costs)
    from private_equipment_presets import sync_scene_equipment
    sync_scene_equipment(session, instances=(item.main_item.weapon.instance_id for item in changed
                                            if item.main_item.WhichOneof("item") == "weapon"))
    if not remove:
        for item in changed:
            rsp.items.add().CopyFrom(item)
            if item.main_item.WhichOneof("item") == "weapon":
                rsp.weapon_instances.add().CopyFrom(item.main_item.weapon)
            else:
                rsp.inscriptions.add().CopyFrom(item.main_item.inscription)
    return pb.StatusCode_OK


def disassemble(session, req, rsp):
    pid = session.player_id
    selected = list(req.weapon_ids) + list(req.armor_ids)
    if not selected or len(selected) > 256 or len(set(selected)) != len(selected):
        return pb.StatusCode_BAD_REQ
    in_presets = set()
    for raw in db.get_characters(pid):
        for preset in pb.Character.FromString(raw).equipment_presets:
            in_presets.add(preset.weapon)
            in_presets.update(a.armor_id for a in preset.armors)
    rewards = Counter()
    for kind, ids in (("weapon", req.weapon_ids), ("armor", req.armor_ids)):
        for key in ids:
            item = load(pid, key, kind)
            if not item or key in in_presets:
                return pb.StatusCode_BAD_REQ
            obj = getattr(item.main_item, kind)
            if obj.wearer_id or obj.is_lock or (kind == "weapon" and obj.inscription1):
                return pb.StatusCode_BAD_REQ
            config = _table(kind.title(), kind + "_disassemble").get(property_group(kind, obj).get("disassemble_i_d"))
            if not config:
                return pb.StatusCode_CFG_NOT_FOUND
            for r in config[kind + "_disassemble_item"]:
                if random.random() < r.get("item_rate", 1):
                    rewards[r["item_i_d"]] += random.randint(r.get("item_min_count", 1), r.get("item_max_count", 1))
    for key in selected:
        db.del_item_detail(pid, key)
    if rewards:
        grant(session, rewards, rsp.items)
    rsp.weapon_ids.extend(req.weapon_ids)
    rsp.armor_ids.extend(req.armor_ids)
    from private_newcomer import record
    if req.weapon_ids:
        record(session, 150)
    if req.armor_ids:
        record(session, 148)
    return pb.StatusCode_OK


OPERATIONS = {"LockEquip": lock, "WeaponStrength": lambda s, q, r: strengthen(s, q, r, "weapon"),
              "ArmorStrength": lambda s, q, r: strengthen(s, q, r, "armor"),
              "WeaponInscriptionInlaid": inscription,
              "WeaponInscriptionRemove": lambda s, q, r: inscription(s, q, r, remove=True),
              "EquipDisassemble": disassemble}


def handle(session, name, data, packet_id):
    rsp = getattr(pb, name + "Rsp")()
    receipts = getattr(session, "equip_receipts", None)
    key = (packet_id, name, data)
    if packet_id and receipts is not None and key in receipts:
        rsp.ParseFromString(receipts[key])
    else:
        req = getattr(pb, name + "Req").FromString(data)
        rsp.status = OPERATIONS[name](session, req, rsp)
        if packet_id and rsp.status == pb.StatusCode_OK:
            cache_after_commit(session, "equip_receipts", key, rsp.SerializeToString())
    session.send(getattr(MsgId, name + "Rsp"), rsp, packet_id)
