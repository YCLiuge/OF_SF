"""Original 1.1.12 NPC commission and event dialogue panels send the quest ID with type 0."""
from proto import net_pb2 as pb
from private_dungeons import _table
from private_first_quest import save, saved, within_target
from private_story_objectives import at_fount
from utils import db
from utils.pb_create import make_Quest
from utils.res_loader import res


# Plaza Home_Day_001_Modify, each NPC's Npc触发器/监听器 projected onto its walkable floor.
# The original game uses a player collider, while its position packets give the character's feet.
_SPHERES = {
    10003: (-7.381403, 20.946506), 10004: (1.347965, -20.455846),
    10007: (-19.938742, 8.824494), 10008: (1.406232, 40.444673),
}
_npc_source = None
_delegates = {}


def delegates():
    global _npc_source, _delegates
    if _npc_source is not res["NPC"]:
        pools = _table("NPC", "delegate_quest_pool")
        result = {}
        for actor in _table("NPC", "info").values():
            for action in actor.get("funcs", ()):
                if action.get("new_n_p_c_function") != 13:
                    continue
                pool = pools[action["sub_meter_i_d"]]
                for row in pool["delegate_quest_pool_group_info"]:
                    result[row["quest_i_d"]] = (actor["i_d"], row)
        _npc_source, _delegates = res["NPC"], result
    return _delegates


def _near_actor(session, actor):
    if session.scene_id != 9999 or session.dungeon[0]:
        return False
    foot = session.scene_player.team.char1.pos
    x, z = foot.x / 100, foot.z / 100
    if actor == 10005:
        return at_fount(session)
    if actor in (10001, 10002):
        return within_target(100012 if actor == 10001 else 100011, x, z)
    if actor in _SPHERES:
        cx, cz = _SPHERES[actor]
        return (x - cx) ** 2 + (z - cz) ** 2 <= 1.700001 ** 2
    return False


def _state(pid, qid):
    record = saved(pid, qid)
    return record.status if record else pb.QuestStatus_Locked


def _can_take(session, actor, rule):
    if not _near_actor(session, actor):
        return False
    pid = session.player_id
    level, = db.get_players_info(pid, "level")
    # OnDelegateQuest uses map/quest_unlock_show to include a locked preview when the
    # level or prior quest fails. Once both pass, the row is in its actual state group.
    return (level >= rule.get("limit_level", 0) and
            all(_state(pid, qid) == pb.QuestStatus_Finish for qid in rule.get("before_i_d", ())))


def activity_dialogue(row):
    # These two authored editions share the same original 幽冥庭院 dialogue and objective.
    return (row["i_d"] in (5075, 260815130) and row.get("new_activity_type") == 4
            and row.get("quest_i_d") == 910101 and row.get("dialog_i_d") == 1011)


def accept(session, req, rsp):
    from private_events import current, settings
    import private_shop as shop

    quest_id = req.quest_id
    definition = _table("Quest", "quest").get(quest_id)
    if req.quest_type != pb.EQuestType_None or not definition:
        return pb.StatusCode_BAD_REQ
    role = delegates().get(quest_id)
    if role and definition.get("new_type") == pb.EQuestType_DelegateQuest:
        if not _can_take(session, *role):
            return pb.StatusCode_BAD_REQ
    elif quest_id == 910101:
        instant, overrides = shop.now(), settings()
        if not any(activity_dialogue(row) and current(row, instant, overrides)
                   for row in _table("Activity", "activity").values()):
            return pb.StatusCode_Activity_Close
    else:
        return pb.StatusCode_BAD_REQ

    existing = saved(session.player_id, quest_id)
    if existing and existing.status != pb.QuestStatus_Locked:
        if existing.status != pb.QuestStatus_InProgress:
            return pb.StatusCode_BAD_REQ
        rsp.quest.CopyFrom(existing)
        return pb.StatusCode_OK
    quest = make_Quest(session.player_id, quest_id)
    if not quest or not quest.conditions:
        return pb.StatusCode_CFG_NOT_FOUND
    save(session, quest)
    rsp.quest.CopyFrom(quest)
    return pb.StatusCode_OK
