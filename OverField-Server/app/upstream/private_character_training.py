"""PC role-bound skills, experience and level breaks. Called inside a realm transaction."""
from private_request import cache_after_commit
from fractions import Fraction

from network.msg_id import MsgId
from private_crafts import payments, save_pack
from private_dungeons import _table
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res


def active_spells(config):
    return (*config.get("spell_i_ds", ()), *config.get("ex_spell_i_ds", ()))


def import_skills(player):
    # Community builds stored paid progress separately; prefer the highest valid level.
    for key, value in db.conn.execute("SELECT k,v FROM player_kv WHERE player_id=? AND ns='skills'", (player,)):
        parts = key.split(":")
        if len(parts) != 2 or not all(s.isascii() and s.isdigit() and 0 < len(s) <= 10 for s in parts):
            continue
        char_id, skill_id = map(int, parts)
        cfg = _table("Character","character").get(char_id, {})
        levels = res["CharacterSkills"]["steps"].get(str(skill_id), ())
        raw = db.get_characters(player, char_id) if char_id else ()
        if not raw or skill_id not in active_spells(cfg) or not any(levels):
            continue
        try:
            old = int(value)
        except (TypeError, ValueError):
            continue
        if not 1 < old <= len(levels):
            continue
        char = pb.Character.FromString(raw[0])
        slot = next((s for s in char.character_skill_list if s.skill_id == skill_id), None)
        if slot is None:
            slot = char.character_skill_list.add(skill_id=skill_id)
        if slot.skill_level < old:
            slot.skill_level = old
            db.set_character(player, char_id, char.SerializeToString())


def skill(session, char, cfg, req, rsp):
    if req.skill_id not in active_spells(cfg):
        return pb.StatusCode_BAD_REQ, {}
    spells = res["CharacterSkills"]
    levels = spells["steps"].get(str(req.skill_id), ())
    slot = next((s for s in char.character_skill_list if s.skill_id == req.skill_id), None)
    level = (slot.skill_level or 1) if slot is not None else 1
    if not levels:
        return pb.StatusCode_CFG_NOT_FOUND, {}
    if level >= len(levels):
        return pb.StatusCode_CharacterSkillLvIsMax, {}
    cost = spells["costs"].get(str(levels[level - 1]))
    if not cost:
        return pb.StatusCode_BAD_REQ, {}
    details, status = payments(session.player_id, {int(k): v for k, v in cost.items()})
    if status != pb.StatusCode_OK:
        return status, {}
    if slot is None:
        slot = char.character_skill_list.add(skill_id=req.skill_id)
    slot.skill_level = level + 1
    rsp.skill.CopyFrom(slot)
    rsp.items.extend(details.values())
    return pb.StatusCode_OK, details


def level_up(session, char, cfg, req, rsp):
    if not 1 <= len(req.nums) <= 3 or min(req.nums) < 0 or not any(req.nums) or char.level >= char.max_level:
        return pb.StatusCode_BAD_REQ, {}
    costs = {template: count for template, count in zip((190,191,192), req.nums) if count}
    curve = {r["level"]: r for r in _table("Character","level")[cfg["growth_level_i_d"]]["level_info"]}
    if char.level not in curve:
        return pb.StatusCode_CFG_NOT_FOUND, {}
    items = _table("Item","item")
    xp = sum(items[template]["relate_i_d"] * n for template, n in costs.items())
    coins = int(Fraction(str(curve[char.level]["exp_to_coin"])) * xp)
    if coins:
        costs[101] = coins
    details, status = payments(session.player_id, costs)
    if status != pb.StatusCode_OK:
        return status, {}
    level, remaining = char.level, char.exp + xp
    while level < char.max_level:
        tier = curve.get(level)
        if not tier or not tier.get("need_exp"):
            return pb.StatusCode_CFG_NOT_FOUND, {}
        if remaining < tier["need_exp"]:
            break
        remaining -= tier["need_exp"]
        level += 1
    char.level, char.exp = level, remaining if level < char.max_level else 0
    rsp.level, rsp.exp = char.level, char.exp
    return pb.StatusCode_OK, details


def level_break(session, char, cfg, req, rsp):
    rule = _table("Character","level_rule").get(cfg.get("rule_i_d"), {})
    tier = next((r for r in rule.get("level_rule_info", ()) if r["top_level"] == char.max_level), None)
    if not tier or char.level != char.max_level:
        return pb.StatusCode_BAD_REQ, {}
    costs = {i["need_item_i_d"]: i["need_item_count"] for i in tier["rule_need_item"]}
    details, status = payments(session.player_id, costs)
    if status != pb.StatusCode_OK:
        return status, {}
    char.max_level = tier["top_max_level"]
    rsp.level, rsp.exp, rsp.max_level = char.level, char.exp, char.max_level
    return pb.StatusCode_OK, details


OPERATIONS = {"CharacterSkillLevelUp": skill, "CharacterLevelUp": level_up, "CharacterLevelBreak": level_break}


def handle(session, name, data, packet_id):
    req = getattr(pb, name + "Req").FromString(data)
    rsp = getattr(pb, name + "Rsp")(char_id=req.char_id)
    response_id = getattr(MsgId, name + "Rsp")
    receipts = getattr(session, "training_receipts", None)
    key = (packet_id, name, data)
    if packet_id and receipts is not None and key in receipts:
        rsp.ParseFromString(receipts[key])
        session.send(response_id, rsp, packet_id)
        return
    raw = db.get_characters(session.player_id, req.char_id) if req.char_id else ()
    if not raw:
        rsp.status = pb.StatusCode_CharNotExist
        session.send(response_id, rsp, packet_id)
        return
    cfg = _table("Character","character").get(req.char_id)
    if not cfg:
        rsp.status = pb.StatusCode_CFG_NOT_FOUND
        session.send(response_id, rsp, packet_id)
        return
    char = pb.Character.FromString(raw[0])
    rsp.status, costs = OPERATIONS[name](session, char, cfg, req, rsp)
    if rsp.status != pb.StatusCode_OK:
        session.send(response_id, rsp, packet_id)
        return
    db.set_character(session.player_id, req.char_id, char.SerializeToString())
    session.send(response_id, rsp, packet_id)
    save_pack(session, costs)
    if name != "CharacterSkillLevelUp":
        team = session.scene_player.team
        for member in (team.char1, team.char2, team.char3):
            if member.char_id == req.char_id:
                member.char_lv, member.char_break_lv = char.level, char.max_level
    if name == "CharacterLevelBreak":
        from private_newcomer import record
        record(session, 134)
    if packet_id:
        cache_after_commit(session, "training_receipts", key, rsp.SerializeToString())
