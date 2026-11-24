"""Manual extra-story rewards from the PC quest directory, keyed by the actual terminal quest."""
from collections import defaultdict
import time

from private_dungeons import grant, reward_items
from private_first_quest import save, saved
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res


_sources = ()
_terminals = {}


def terminals():
    global _sources, _terminals
    sources = (res["Story"], res["Quest"])
    if _sources and all(a is b for a, b in zip(_sources, sources)):
        return _terminals
    groups = defaultdict(list)
    for row in sources[1]["quest"]["datas"]:
        if row.get("new_type") == pb.EQuestType_ExtraQuest:
            groups[row["quest_group"]].append(row)
    configured = {}
    for chapter in sources[0]["extra_chapter"]["datas"]:
        for entry in chapter["extra_quest"]:
            head = entry["quest_i_d"]
            rows = groups[head]
            if not rows or rows[0]["i_d"] != head:
                raise ValueError("Invalid extra-story quest chain")
            tail = rows[-1]
            if tail.get("reward") and not tail.get("is_immed_reward") and tail.get("limit_num") == 1:
                configured[tail["i_d"]] = (tuple(step["i_d"] for step in rows), tail["reward"])
    _sources, _terminals = sources, configured
    return configured


def manual_end(row):
    return row.get("new_type") == pb.EQuestType_ExtraQuest and row["i_d"] in terminals()


def claim(session, req, rsp):
    entry = terminals().get(req.quest_id)
    if entry is None or req.quest_type != pb.EQuestType_ExtraQuest:
        return pb.StatusCode_BAD_REQ
    player = session.player_id
    current = saved(player, req.quest_id)
    if current is None:
        return pb.StatusCode_Quest_Not_Finish
    rsp.quest.CopyFrom(current)
    paid = db.conn.execute("SELECT 1 FROM claimed_rewards WHERE player_id=? AND system='extra_story' AND key=?",
                           (player, str(req.quest_id))).fetchone()
    if paid or current.status == pb.QuestStatus_Finish or current.complete_count:
        return pb.StatusCode_REPEAT_GET_REWARD
    # ChaimReward enables the button precisely when the saved last step is Completed (2).
    if current.status != pb.QuestStatus_Completed:
        return pb.StatusCode_Quest_Not_Finish
    amounts = reward_items(player, entry[1])
    if not amounts:
        return pb.StatusCode_CFG_NOT_FOUND
    db.conn.execute("INSERT INTO claimed_rewards(player_id,system,key,claim_time) VALUES(?,'extra_story',?,?)",
                    (player, str(req.quest_id), int(time.time())))
    grant(session, amounts, rsp.items)
    current.status = pb.QuestStatus_Finish
    current.complete_count = 1
    save(session, current)
    rsp.quest.CopyFrom(current)
    return pb.StatusCode_OK
