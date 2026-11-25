"""Restore a minimum fishing level from native catch records without granting quest rewards."""
from contextlib import closing
from functools import lru_cache
from itertools import chain
import pickle
import sqlite3

from private_dungeons import _table
from proto import net_pb2 as pb
from utils import db


@lru_cache(maxsize=1)
def catch_requirements():
    fish = _table("Fishing", "fishing_info")
    levels = {}
    for cid, rule in _table("Achieve", "achieve").items():
        species = rule.get("param", ())
        if rule.get("new_achieve_condition") == 28 and len(species) == 1 and species[0] in fish:
            levels[cid] = fish[species[0]].get("need_level", 1)
    groups = _table("Quest", "condition_set_group")
    authored = {}
    for qid, quest in _table("Quest", "quest").items():
        phases = groups.get(quest.get("condition_set_group_i_d"), {}).get("quest_condition_set", ())
        ids = frozenset(cid for phase in phases for cid in phase.get("achieve_condition_i_d", ()))
        if not ids.isdisjoint(levels):
            authored[qid] = ids
    daily = {entry["achieve_condition_i_d"] for tier in _table("Fishing", "fishing_level").values()
             for entry in tier.get("fishing_level_quest", ())}
    return levels, authored, daily


def quest_floor(qid, status, conditions):
    levels, authored, daily = catch_requirements()
    ids = frozenset(c[0] for c in conditions)
    if status not in (1, 2, 3) or len(ids) != len(conditions):
        return 1
    if qid:
        if authored.get(qid) != ids:
            return 1
    elif len(ids) != 1 or not ids.issubset(daily):
        return 1
    return max((levels.get(cid, 1) for cid, phase, progress in conditions
                if phase in (1, 2, 3) and progress > 0), default=1)


def apply_floor(conn, player, level):
    """Retain the role's existing proficiency, skill studies, catch log and higher level."""
    if level <= 1:
        return
    row = conn.execute("SELECT life_blob FROM life_info WHERE player_id=?", (player,)).fetchone()
    if row is None:
        raise ValueError("Missing life information for restored role")
    life = db._load_saved_data(row[0])
    info = pb.LifeBaseInfo.FromString(life.get(pb.LIFE_TYPE_Fishing, b""))
    if info.level < level:
        info.life_type = pb.LIFE_TYPE_Fishing
        info.level = level
        life[pb.LIFE_TYPE_Fishing] = info.SerializeToString()
        conn.execute("UPDATE life_info SET life_blob=? WHERE player_id=?", (pickle.dumps(life),player))


def sealed_floor(conn, player, slot):
    # Older importers archived numbered quests but discarded anonymous daily conditions.
    # Only sealed imports need migration, and the caller owns the realm transaction.
    receipt = conn.execute("SELECT 1 FROM private_import_receipt WHERE slot=? AND player=? "
                           "AND state='applied' AND sealed=1", (slot, player)).fetchone()
    if not receipt:
        return 1
    cached = conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='import_fishing' AND k='floor_v1'",
                          (player,)).fetchone()
    if cached:
        return int(cached[0])
    from save_import_quests import HISTORY
    _, authored, _ = catch_requirements()
    keys = tuple(authored)
    slots = ",".join("?" for _ in keys)
    native = conn.execute(f"SELECT quest_blob FROM quests WHERE player_id=? AND quest_id IN ({slots})", (player,*keys))
    history = conn.execute(f"SELECT v FROM player_kv WHERE player_id=? AND ns=? AND k IN ({slots})",
                           (player,HISTORY,*(str(key) for key in keys)))
    level = 1
    for (raw,) in chain(native, history):
        q = pb.Quest.FromString(raw)
        level = max(level, quest_floor(q.quest_id, q.status,
                    [(c.condition_id, c.status, c.progress) for c in q.conditions]))
    conn.execute("INSERT INTO player_kv VALUES(?,'import_fishing','floor_v1',?)", (player,str(level)))
    return level


def recover_offline(state, resources, uid, *, apply=False):
    from state_lock import exclusive_state
    from utils.res_loader import res
    if not 0 < uid <= 0xffffffff:
        raise ValueError("Expected the public player UID")
    res.load(resources)
    with exclusive_state(state) as root, closing(sqlite3.connect((root/"game.sqlite").as_uri()+"?mode=rw",uri=True)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        matches = conn.execute("SELECT r.player,r.slot FROM private_import_receipt r LEFT JOIN realm_public_uids u "
                               "ON u.player_id=r.player WHERE COALESCE(u.uid,r.player)=? AND r.sealed=1 AND r.state='applied'", (uid,)).fetchall()
        if len(matches) != 1:
            raise ValueError("Expected a single sealed import for this UID")
        player, slot = matches[0]
        level = sealed_floor(conn,player,slot)
        raw = conn.execute("SELECT life_blob FROM life_info WHERE player_id=?", (player,)).fetchone()[0]
        previous = pb.LifeBaseInfo.FromString(db._load_saved_data(raw).get(pb.LIFE_TYPE_Fishing,b"")).level
        if apply:
            apply_floor(conn,player,level)
            conn.commit()
        else:
            conn.rollback()
        return {"previous":previous,"confirmed_minimum":level,"result":max(level,previous) if apply else previous}
