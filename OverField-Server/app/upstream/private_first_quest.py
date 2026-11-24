"""Private-realm objectives for the first two confirmed conversations in 新的伙伴."""
import math
from proto import net_pb2 as pb
from network.msg_id import MsgId
from utils import db
from utils.pb_create import make_Quest
from private_dungeons import grant, reward_items

STORY = 1000001
STEPS = (100011, 100012, 100013)
CONDITIONS = (11000111, 11000121, 11000131)
# Authored Home_Day_001_Modify Npc触发器/监听器, projected onto the walkable plane.
TARGETS = {
    100011: dict(npc="梓", npcId=10002, dialog=1000112, task="去咖啡厅问问情况",
                 x=19.922000885, z=-26.661937714, halfWidth=1.991453409, halfDepth=2.670019150),
    100012: dict(npc="绫", npcId=10001, dialog=1000122, task="找绫了解一下招募",
                 x=25.699059554, z=-21.873896962, radius=1.700000048),
    100013: dict(task='使用“招募”获得1名队员')
}


def within_target(qid, x, z):
    target = TARGETS[qid]
    dx, dz = x-target["x"], z-target["z"]
    if "radius" in target:
        return math.hypot(dx, dz) <= target["radius"]
    return abs(dx) <= target["halfWidth"] and abs(dz) <= target["halfDepth"]


def saved(pid, qid):
    raw = db.get_quest(pid, qid)
    return pb.Quest.FromString(raw) if raw else None


def save(session, quest):
    db.set_quest(session.player_id, quest.quest_id, quest.SerializeToString())
    if quest.quest_id==101011 and quest.status in (pb.QuestStatus_Completed,pb.QuestStatus_Finish) and getattr(session,"logged_in",False):
        from private_bless_tasks import ensure
        ensure(session,publish=True)
    if quest.status == pb.QuestStatus_InProgress:
        session.quests[quest.quest_id] = quest
    else:
        session.quests.pop(quest.quest_id, None)


def accept(session, pid):
    current = next((q for k in reversed(STEPS) if (q := saved(session.player_id, k)) is not None), None)
    if current is None:
        current = make_Quest(session.player_id, STEPS[0])
        save(session, current)
    session.send(MsgId.AcceptQuestRsp, pb.AcceptQuestRsp(status=pb.StatusCode_OK, quest=current), pid)


def talk(session, condition, packet_id):
    index = CONDITIONS.index(condition)
    quest_id = STEPS[index]
    current = saved(session.player_id, quest_id)
    response = pb.NpcTalkRsp(status=pb.StatusCode_BAD_REQ)
    # The third original objective requires recruitment. A dialogue never completes it.
    if index == 2 or current is None or session.scene_id != 9999 or session.dungeon[0]:
        session.send(MsgId.NpcTalkRsp, response, packet_id)
        return
    if current.status == pb.QuestStatus_Finish:
        response.status = pb.StatusCode_OK
    else:
        foot = session.scene_player.team.char1.pos
        if current.status == pb.QuestStatus_InProgress and within_target(quest_id, foot.x/100, foot.z/100):
            next_id = STEPS[index+1]
            if (len(current.conditions) != 1 or current.conditions[0].condition_id != condition or
                    saved(session.player_id, next_id) is not None):
                session.send(MsgId.NpcTalkRsp, response, packet_id)
                return
            nxt = make_Quest(session.player_id, next_id)
            current.conditions[0].progress = 1
            current.conditions[0].status = current.status = pb.QuestStatus_Finish
            save(session, current)
            save(session, nxt)
            if quest_id == 100012:
                # Quest[100012].reward=650; both components are deterministic at level 1.
                grant(session, reward_items(session.player_id, 650), pb.ClaimQuestRewardRsp().items)
            notice = pb.QuestNotice(status=pb.StatusCode_OK)
            notice.quests.extend((current, nxt))
            session.send(MsgId.QuestNotice, notice, 0)
            response.status = pb.StatusCode_OK
    session.send(MsgId.NpcTalkRsp, response, packet_id)
