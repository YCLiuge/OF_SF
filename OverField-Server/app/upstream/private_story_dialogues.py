"""Three authored plaza stories whose sole objectives are normal conversations with 荧荧."""
from math import hypot

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils.res_loader import res
from private_first_quest import save, saved
from private_story_catalog import catalog
from utils.pb_create import make_Quest

STORIES = (1000606, 1000608, 1000705)
# Home_Day_001_Modify/NPC_荧荧_引导助理_10006/Npc触发器/监听器:
# original trigger sphere transformed to the horizontal player movement plane.
NPC = 10006
SCENE = 9999
X = -28.01443
Z = -22.91091
CENTER_Y = 2.75422
RADIUS = 1.700000048
_cached_catalog = None
_by_condition = {}
_reserved_aliases = set()


def dialogue_index():
    global _cached_catalog, _by_condition, _reserved_aliases
    stories = catalog()
    if _cached_catalog is stories:
        return _by_condition, _reserved_aliases
    quests = res.index("Quest", "quest")
    groups = res.index("Quest", "condition_set_group")
    sources = res.index("Quest", "quest_resource")
    achieves = res.index("Achieve", "achieve")
    verified = {}
    aliases = {NPC}
    for sid in STORIES:
        _, _, steps = stories[sid]
        for index, qid in enumerate(steps):
            q = quests[qid]
            phases = groups[q["condition_set_group_i_d"]]["quest_condition_set"]
            if len(phases) != 1 or len(phases[0]["achieve_condition_i_d"]) != 1:
                raise ValueError("Unexpected dialogue goal count")
            phase = phases[0]
            cond, = phase["achieve_condition_i_d"]
            definition = achieves[cond]
            actor, dialog = definition["param"]
            if (q.get("new_type") != 1 or q.get("reward") or q.get("first_reward")
                    or definition.get("new_achieve_condition") != 37 or definition.get("count_param") != 1
                    or actor != NPC or not dialog or cond in verified):
                raise ValueError("Dialogue actor, objective or reward differs from the supported story")
            actors = { (row.get("scene_i_d"), row.get("quest_resource_i_d"))
                      for key in phase["resource_i_d"] for row in sources[key]["quest_resource_items"] }
            if actors != {(SCENE, NPC)}:
                raise ValueError("Unexpected dialogue scene or tracked NPC")
            verified[cond] = (steps, index)
            aliases.add(dialog)
    _cached_catalog, _by_condition, _reserved_aliases = stories, verified, aliases
    return verified, aliases


def talk(session, req, response):
    """Return whether this NPC request belongs to the six conversations. The handler sends the reply."""
    goals, aliases = dialogue_index()
    if req.id not in goals:
        if req.id in aliases:
            response.status = pb.StatusCode_BAD_REQ
            return True
        return False
    response.status = pb.StatusCode_BAD_REQ
    if req.talk_type != pb.NpcTalkReq.NpcTalkType_Npc or session.scene_id != SCENE or session.dungeon[0]:
        return True
    pos = session.scene_player.team.char1.pos
    # The avatar's feet cannot touch the collider when already above its highest point.
    if pos.y/100 > CENTER_Y + RADIUS or hypot(pos.x/100 - X, pos.z/100 - Z) > RADIUS:
        return True
    steps, index = goals[req.id]
    pid = session.player_id
    current = saved(pid, steps[index])
    if current is None or len(current.conditions) != 1 or current.conditions[0].condition_id != req.id:
        return True
    if any((before := saved(pid, qid)) is None or before.status != pb.QuestStatus_Finish for qid in steps[:index]):
        return True
    if current.status == pb.QuestStatus_Finish:
        response.status = pb.StatusCode_OK
        return True
    if current.status != pb.QuestStatus_InProgress or current.conditions[0].status != pb.QuestStatus_InProgress:
        return True
    if any(saved(pid, qid) is not None for qid in steps[index+1:]):
        return True
    current.status = current.conditions[0].status = pb.QuestStatus_Finish
    current.complete_count = current.conditions[0].progress = 1
    save(session,current)
    notice = pb.QuestNotice(status=pb.StatusCode_OK, quests=[current])
    if index+1 < len(steps):
        following = make_Quest(pid, steps[index+1])
        save(session, following)
        notice.quests.add().CopyFrom(following)
    session.send(MsgId.QuestNotice, notice, 0)
    response.status = pb.StatusCode_OK
    return True
