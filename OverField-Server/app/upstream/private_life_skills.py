"""Paid, timed life-skill training using the six PC discipline tables."""
from collections import Counter
from private_request import cache_after_commit
import pickle
import time

from network.msg_id import MsgId
from private_crafts import base, payments, save_pack
from private_dungeons import _table
from proto import net_pb2 as pb
from utils import db


# Resource, skill table prefix, profession-level field.
DISCIPLINES = {
    pb.LIFE_TYPE_Cooking: ("Cooking", "cook", "need_cook_level"),
    pb.LIFE_TYPE_Fishing: ("Fishing", "fishing", "need_fish_level"),
    pb.LIFE_TYPE_Making: ("Make", "make", "need_make_level"),
    pb.LIFE_TYPE_Handicraft: ("Handicraft", "handicraft", "need_handicraft_level"),
    pb.LIFE_TYPE_Sewing: ("Spin", "spin", "need_spin_level"),
    pb.LIFE_TYPE_Gathering: ("LifeGather", "life_gather", "need_gather_level"),
}


def clock():
    return int(time.time())


def tiers(kind, skill):
    resource, prefix, _ = DISCIPLINES[kind]
    group = prefix + "_spell"
    return _table(resource, group).get(skill, {}).get(group + "_items", ())


def import_legacy(player):
    # Older builds immediately granted lv, even when start_study_time was nonzero.
    rows = db.conn.execute("SELECT k,v FROM player_kv WHERE player_id=? AND ns='life_skill'", (player,))
    for key, raw in rows.fetchall():
        if not str(key).isdecimal() or int(key) not in DISCIPLINES:
            continue
        kind = int(key)
        if not isinstance(raw, bytes) or len(raw) > 65536:
            continue
        try:
            saved = db._load_saved_data(raw)
        except (EOFError, ValueError, TypeError, pickle.UnpicklingError, OverflowError):
            continue
        if not isinstance(saved, dict):
            continue
        for skill, state in saved.items():
            if type(skill) is not int or not isinstance(state, (tuple, list)) or len(state) != 2:
                continue
            if type(state[0]) is not int or not 1 <= state[0] <= len(tiers(kind, skill)):
                continue
            db.conn.execute("INSERT OR IGNORE INTO realm_life_study(player,kind,skill,level) VALUES(?,?,?,?)",
                            (player, kind, skill, state[0]))


def write_entries(player, kind, output):
    for skill, level, start in db.conn.execute(
            "SELECT skill,level,start FROM realm_life_study WHERE player=? AND kind=? ORDER BY skill", (player, kind)):
        output.add(skill_id=skill, lv=level, start_study_time=start)


def refresh(session):
    now = clock()
    check = getattr(session, "life_study_due", None)
    if check is not None and (not check or now < check):
        return
    player = session.player_id
    if check is None:
        import_legacy(player)
    due = db.conn.execute("SELECT kind,skill,level FROM realm_life_study WHERE player=? AND due>0 AND due<=? "
                          "ORDER BY kind,skill", (player, now)).fetchall()
    if due:
        notice = pb.LifeSkillLevelUpNotice(status=pb.StatusCode_OK)
        updates = {}
        for kind, skill, level in due:
            if kind not in updates:
                updates[kind] = notice.life_skills.add(life_type=kind)
            updates[kind].life_skill_info.add(skill_id=skill, lv=level+1)
        db.conn.execute("UPDATE realm_life_study SET level=level+1,start=0,due=0 WHERE player=? AND due>0 AND due<=?",
                        (player, now))
        session.send(MsgId.LifeSkillLevelUpNotice, notice, 0)
    session.life_study_due = db.conn.execute(
        "SELECT MIN(due) FROM realm_life_study WHERE player=? AND due>0", (player,)).fetchone()[0] or 0


def garden_like_bonus(session):
    refresh(session)
    row = db.conn.execute("SELECT level FROM realm_life_study WHERE player=? AND kind=? AND skill=34300",
                          (session.player_id, pb.LIFE_TYPE_Making)).fetchone()
    if not row or not row[0]:
        return 0
    config = tiers(pb.LIFE_TYPE_Making, 34300)[row[0]-1]
    return int(config["spell_param1"])


def listing(session, req, rsp):
    if req.life_type not in DISCIPLINES:
        return pb.StatusCode_BAD_REQ
    refresh(session)
    rsp.life_type = req.life_type
    write_entries(session.player_id, req.life_type, rsp.skill_info)
    return pb.StatusCode_OK


def start(session, req, rsp):
    kind, skill, player = req.life_type, req.skill_id, session.player_id
    rsp.is_force_study = req.is_force_study
    if kind not in DISCIPLINES:
        return pb.StatusCode_BAD_REQ
    levels = tiers(kind, skill)
    if not levels:
        return pb.StatusCode_PlayerLifeSkillNotExist
    refresh(session)
    previous = db.conn.execute("SELECT level,due FROM realm_life_study WHERE player=? AND kind=? AND skill=?",
                               (player, kind, skill)).fetchone()
    level, due = previous or (0, 0)
    if req.is_force_study:
        if not due:
            return pb.StatusCode_SkillAlreadyStudy if level else pb.StatusCode_BAD_REQ
        # The native confirmation shows floor(remaining_seconds * per_minute / 60 + 0.5).
        pair = _table("GlobalConfig", "config", "key")["ClearCDByItemConfig"]["values"][0]
        currency, rate = map(int, pair.split(":"))
        cost = (max(0, due - clock()) * rate + 30) // 60
        charges, status = payments(player, {currency: cost}) if cost else ({}, pb.StatusCode_OK)
        if charges is None:
            return status
        db.conn.execute("UPDATE realm_life_study SET due=?,start=0 WHERE player=? AND kind=? AND skill=?",
                        (clock(), player, kind, skill))
        session.life_study_due = clock()
        refresh(session)
        rsp.life_skills.life_type = kind
        rsp.life_skills.life_skill_info.add(skill_id=skill, lv=level + 1)
        if charges:
            for item in charges.values():
                rsp.items.add().CopyFrom(item)
            save_pack(session, charges)
        return pb.StatusCode_OK
    if due:
        return pb.StatusCode_IsLearning
    if db.conn.execute("SELECT 1 FROM realm_life_study WHERE player=? AND kind=? AND due>0", (player,kind)).fetchone():
        return pb.StatusCode_CantLearningTwoSkill
    if level >= len(levels):
        return pb.StatusCode_LifeSkillMaxLv
    cfg = levels[level]
    if cfg["level"] != level+1:
        return pb.StatusCode_CFG_NOT_FOUND
    _, prefix, requirement = DISCIPLINES[kind]
    career = base(player, kind)
    if not career or career.level < cfg[requirement]:
        return pb.StatusCode_LifeLvNotEnough
    costs = Counter({cfg["need_currency_i_d"]: cfg["need_currency_count"]})
    for entry in cfg.get(prefix + "_need_item", ()):
        costs[entry["need_item_i_d"]] += entry["need_item_count"]
    charges, status = payments(player, costs)
    if charges is None:
        return status
    now = clock()
    end = now + cfg["need_time"]
    db.conn.execute("""INSERT INTO realm_life_study(player,kind,skill,level,start,due) VALUES(?,?,?,?,?,?)
                       ON CONFLICT(player,kind,skill) DO UPDATE SET start=excluded.start,due=excluded.due""",
                    (player, kind, skill, level, now, end))
    session.life_study_due = min(session.life_study_due or end, end)
    rsp.life_skills.life_type = kind
    rsp.life_skills.life_skill_info.add(skill_id=skill, lv=level, start_study_time=now)
    for detail in charges.values():
        rsp.items.add().CopyFrom(detail)
    save_pack(session, charges)
    return pb.StatusCode_OK


def learn(session, req, packet):
    receipts = getattr(session, "life_study_receipts", None)
    key = packet, req.life_type, req.skill_id, req.is_force_study
    if packet and receipts is not None and key in receipts:
        return pb.LifeSkillLevelUpRsp.FromString(receipts[key])
    rsp = pb.LifeSkillLevelUpRsp()
    rsp.status = start(session, req, rsp)
    if packet and rsp.status == pb.StatusCode_OK:
        cache_after_commit(session, "life_study_receipts", key, rsp.SerializeToString())
    return rsp
