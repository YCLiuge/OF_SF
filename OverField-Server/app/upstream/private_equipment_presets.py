"""Validate character loadouts against the private player's inventory and released equipment tables."""
from network.msg_id import MsgId
from private_appearance import broadcast
from private_dungeons import _table
from private_equipment import load
from proto import net_pb2 as pb
from utils import db


def preset_at(character, index):
    return next((p for p in character.equipment_presets if p.preset_index == index), None)


def normalized(preset, *, stored=False):
    if preset.preset_index not in range(3):
        raise ValueError("preset index")
    result = pb.EquipmentPreset(preset_index=preset.preset_index, weapon=preset.weapon)
    for source, dest, slot_name, id_name, size in (
        (preset.armors, result.armors, "equip_type", "armor_id", 4),
        (preset.posters, result.posters, "poster_index", "poster_id", 3),
    ):
        parts = {}
        for entry in source:
            slot, key = getattr(entry, slot_name), getattr(entry, id_name)
            if slot not in range(size):
                raise ValueError("slot index")
            if slot in parts:
                # Some older saves have empty placeholders followed by a populated entry.
                if not stored or (parts[slot] and key):
                    raise ValueError("duplicate slot")
            parts[slot] = key or parts.get(slot, 0)
        for slot in range(size):
            dest.add(**{slot_name:slot, id_name:parts.get(slot, 0)})
    return result


def normalize_character(character):
    presets = {}
    for preset in character.equipment_presets:
        if preset.preset_index in presets:
            raise ValueError("duplicate preset")
        presets[preset.preset_index] = normalized(preset, stored=True)
    if character.in_use_equipment_preset_index not in range(3):
        raise ValueError("active preset")
    character.ClearField("equipment_presets")
    for index in range(3):
        default = pb.EquipmentPreset(preset_index=index)
        character.equipment_presets.add().CopyFrom(presets[index] if index in presets else normalized(default))


def slots(preset):
    if preset.weapon:
        yield "weapon", 0, preset.weapon
    for p in preset.armors:
        if p.armor_id:
            yield "armor", p.equip_type, p.armor_id
    for p in preset.posters:
        if p.poster_id:
            yield "poster", p.poster_index, p.poster_id


def checked_items(pid, character, preset):
    result = {}
    actor = _table("Character", "character").get(character)
    if not actor:
        raise ValueError("unavailable character")
    for kind, index, key in slots(preset):
        item = load(pid, key, kind)
        if item is None or key in result:
            raise ValueError("unowned or duplicate instance")
        detail = item.main_item
        template = getattr(getattr(detail, kind), kind + "_id")
        rule = _table(kind.title(), kind).get(template)
        if not rule or template != detail.item_id:
            raise ValueError("unavailable equipment")
        if kind != "poster" and rule.get("new_weapon_system_type", 0) != actor.get("new_weapon_system_type", 0):
            raise ValueError("incompatible equipment")
        if kind == "armor" and rule.get("new_equip_type", 0) != index:
            raise ValueError("armor slot")
        result[key] = item
    return result


def remove_instances(character, keys):
    for preset in character.equipment_presets:
        if preset.weapon in keys:
            preset.weapon = 0
        for part in preset.armors:
            if part.armor_id in keys:
                part.armor_id = 0
        for part in preset.posters:
            if part.poster_id in keys:
                part.poster_id = 0


def sync_scene_equipment(session, *, characters=None, instances=()):
    """Refresh only equipped fields on visible team members affected by a committed mutation."""
    from utils.pb_create import fill_scene_equipment
    characters = characters or {}
    instances = set(instances)
    if not characters and not instances:
        return
    team = session.scene_player.team
    changed = False
    for member in (team.char1, team.char2, team.char3):
        if not member.char_id:
            continue
        character = characters.get(member.char_id)
        if character is None:
            if not instances:
                continue
            rows = db.get_characters(session.player_id, member.char_id)
            if not rows:
                continue
            character = pb.Character.FromString(rows[0])
            active = preset_at(character, character.in_use_equipment_preset_index)
            if active is None or not any(key in instances for _, _, key in slots(active)):
                continue
        before = member.SerializeToString()
        fill_scene_equipment(session.player_id, character, member)
        changed |= member.SerializeToString() != before
    if changed:
        broadcast(session, pb.SceneActionType_UPDATE_EQUIP)


def mutate(session, req, rsp, switch=False):
    pid = session.player_id
    rows = db.get_characters(pid, req.char_id) if req.char_id else []
    if not rows or (not switch and not req.HasField("equipment_preset")):
        return pb.StatusCode_BAD_REQ
    character = pb.Character.FromString(rows[0])
    index = req.preset_index if switch else req.equipment_preset.preset_index
    if index not in range(3):
        return pb.StatusCode_BAD_REQ
    try:
        normalize_character(character)
        active = character.in_use_equipment_preset_index
        previous = {key:kind for kind, _, key in slots(preset_at(character, active))}
        dest = preset_at(character, index)
        if not switch:
            dest.CopyFrom(normalized(req.equipment_preset))
        items = checked_items(pid, req.char_id, dest)
    except ValueError:
        return pb.StatusCode_BAD_REQ

    if not switch and index != active:
        db.set_character(pid, req.char_id, character.SerializeToString())
        rsp.character.add().CopyFrom(character)
        return pb.StatusCode_OK

    character.in_use_equipment_preset_index = index
    changed_characters = {req.char_id:character}
    transfers = {}
    acquired_kinds = set()
    changed_items = {}
    for key,item in items.items():
        kind = item.main_item.WhichOneof("item")
        instance = getattr(item.main_item, kind)
        if instance.wearer_id == req.char_id:
            continue
        if instance.wearer_id:
            transfers.setdefault(instance.wearer_id, set()).add(key)
        instance.wearer_id = req.char_id
        changed_items[key] = item
        acquired_kinds.add(kind)
    # A borrow explicitly reassigns equipment on this account. Drop stale presets on the
    # previous wearer so switching their saved plan cannot silently steal the item back.
    owners = db.get_characters(pid) if items else ()
    for row in owners:
        other = pb.Character.FromString(row)
        wearer = other.character_id
        if wearer == req.char_id:
            continue
        keys = transfers.get(wearer, set())
        # Older off-team edits could write an active preset without fixing the
        # instance's wearer. Resolve both sources before committing a transfer.
        equipped = preset_at(other, other.in_use_equipment_preset_index)
        if equipped is not None:
            keys = keys | {key for _, _, key in slots(equipped) if key in items}
        if keys:
            try:
                normalize_character(other)
            except ValueError:
                return pb.StatusCode_BAD_REQ
            remove_instances(other, keys)
            changed_characters[wearer] = other
    if transfers.keys() - changed_characters.keys():
        return pb.StatusCode_BAD_REQ
    for key in previous.keys() - items.keys():
        kind = previous[key]
        item = load(pid, key, kind)
        if item is not None and getattr(item.main_item, kind).wearer_id == req.char_id:
            getattr(item.main_item, kind).wearer_id = 0
            changed_items[key] = item

    for char_id, modified in changed_characters.items():
        db.set_character(pid, char_id, modified.SerializeToString())
    for key, item in changed_items.items():
        db.set_item_detail(pid, item.SerializeToString(), instance_id=key)
    if switch:
        rsp.preset_index = index
        # The switch response has no Character field. Use the normal inventory delta
        # for the former wearer so the owner's roster can refresh without a relog.
        if len(changed_characters) > 1:
            notice = pb.PackNotice(status=pb.StatusCode_OK)
            for wearer in changed_characters.keys() - {req.char_id}:
                card = notice.items.add().main_item
                card.item_id, card.item_tag = wearer, pb.EBagItemTag_Card
                card.character.CopyFrom(changed_characters[wearer])
            session.send(MsgId.PackNotice, notice, 0)
        rsp.items.extend({**items, **changed_items}.values())
    else:
        rsp.character.extend(changed_characters.values())
        rsp.items.extend(changed_items.values())
    sync_scene_equipment(session, characters=changed_characters)
    if acquired_kinds:
        from private_posters import record
        for kind in sorted(acquired_kinds):
            record(session, {"weapon":145, "armor":122, "poster":155}[kind])
    return pb.StatusCode_OK


def switch(session, req, rsp):
    return mutate(session, req, rsp, switch=True)
