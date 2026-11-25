"""Restore authored single-run quests and retain resettable or unclassified records as history."""
from private_dungeons import _table
from private_panel_quests import delegates
from proto import net_pb2 as pb

HISTORY = "import_quest_history"
PERSISTENT = {pb.EQuestType_SideQuest, pb.EQuestType_WorldQuest, pb.EQuestType_ExtraQuest,
              pb.EQuestType_DelegateQuest}


def activity_quests():
    return {row["quest_i_d"] for row in _table("Activity","activity").values() if row.get("quest_i_d")}


def classify(definition, event_quests):
    key = definition["i_d"]
    kind = definition.get("new_type", 0)
    if kind not in PERSISTENT or definition.get("limit_num", 0) != 1 or key in event_quests:
        return "history"
    if kind == pb.EQuestType_DelegateQuest and key not in delegates():
        return "history"
    return "persistent"


def encoded(entry):
    key, state, count, conditions = entry
    quest = pb.Quest(quest_id=key, status=state, complete_count=count, bonus_times=1)
    for cid, status, progress in conditions:
        quest.conditions.add(condition_id=cid, status=status, progress=progress)
    return quest.SerializeToString()


def add_missing(conn, pid, plan, *, undo_slot=None):
    """The calling transaction owns the role. Never overwrite newer local play or trigger a reward."""
    totals = {"additional_quests": 0, HISTORY: 0}
    for label, storage in (("additional_quests", "quests"), (HISTORY, HISTORY)):
        for entry in plan.get(label, ()):
            raw = encoded(entry)
            key = entry[0]
            if storage == "quests":
                cursor = conn.execute("INSERT OR IGNORE INTO quests(player_id,quest_id,quest_blob) VALUES(?,?,?)",
                                      (pid, key, raw))
            else:
                cursor = conn.execute("INSERT OR IGNORE INTO player_kv(player_id,ns,k,v) VALUES(?,?,?,?)",
                                      (pid, HISTORY, str(key), raw))
            if cursor.rowcount:
                totals[label] += 1
                if undo_slot:
                    conn.execute("INSERT OR IGNORE INTO private_import_delta(slot,kind,k,prior,applied) VALUES(?,?,?,NULL,?)",
                                 (undo_slot, storage, key, raw))
    return totals
