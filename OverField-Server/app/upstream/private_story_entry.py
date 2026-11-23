"""The original Chapter 00 opening mechanisms for 初入空间 (PC 1.1.12)."""
import math

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils.pb_create import make_Quest
from private_first_quest import STEPS as FIRST_STORY, save, saved
from private_dungeons import grant, reward_items

STORY = 1000002
STEPS = (100021, 100022, 100023, 100024, 100025, 100026)
CONDITIONS = tuple(11000211 + 10*i for i in range(len(STEPS)))
MECHANISMS = {1000211: 0, 1000221: 1}
FINISHED = pb.QuestStatus_Finish


def first_story_done(pid):
    return all((q := saved(pid, step)) is not None and q.status == FINISHED for step in FIRST_STORY)


def accept(session, packet_id):
    pid = session.player_id
    response = pb.AcceptQuestRsp(status=pb.StatusCode_BAD_REQ)
    if first_story_done(pid):
        current = next((q for step in reversed(STEPS) if (q := saved(pid, step)) is not None), None)
        if current is None:
            current = make_Quest(pid, STEPS[0])
            save(session, current)
        response.status = pb.StatusCode_OK
        response.quest.CopyFrom(current)
    session.send(MsgId.AcceptQuestRsp, response, packet_id)


def in_trigger(session, index):
    if session.dungeon[0]:
        return False
    pos = session.scene_player.team.char1.pos
    x, z = pos.x/100, pos.z/100
    if index == 1:
        # Original entry cutscene prefab has a 3000-unit box centered at zero in 樱庭世界.
        return session.scene_id == 1 and max(abs(x), abs(z), abs(pos.y/100)) <= 1500
    # Event1000211_芳汀 prefab, projected button collider 4 x 4, at its authored rotation.
    dx, dz = x - 15.03, z + 17.87
    cosine = 0.58494
    sine = math.sqrt(1 - cosine*cosine)
    return session.scene_id == 9999 and max(abs(dx*cosine - dz*sine), abs(dx*sine + dz*cosine)) <= 2.0


def mechanism(session, index, packet_id):
    pid = session.player_id
    step = STEPS[index]
    current = saved(pid, step)
    response = pb.NpcTalkRsp(status=pb.StatusCode_BAD_REQ)
    if first_story_done(pid) and current is not None and in_trigger(session, index):
        if current.status == FINISHED:
            response.status = pb.StatusCode_OK
        elif (current.status == pb.QuestStatus_InProgress and len(current.conditions) == 1
                and current.conditions[0].condition_id == CONDITIONS[index]
                and saved(pid, STEPS[index+1]) is None):
            following = make_Quest(pid, STEPS[index+1])
            current.conditions[0].progress = current.complete_count = 1
            current.conditions[0].status = current.status = FINISHED
            save(session, current)
            save(session, following)
            if index == 1:
                grant(session, reward_items(pid, 672), pb.ClaimQuestRewardRsp().items)
            notice = pb.QuestNotice(status=pb.StatusCode_OK, quests=[current,following])
            session.send(MsgId.QuestNotice, notice, 0)
            response.status = pb.StatusCode_OK
    session.send(MsgId.NpcTalkRsp, response, packet_id)

