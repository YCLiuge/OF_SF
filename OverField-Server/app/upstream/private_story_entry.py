"""The six retail objectives of Chapter 00, 初入空间 (PC 1.1.12)."""
import math

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_Quest
from private_first_quest import STEPS as FIRST_STORY, save, saved
from private_dungeons import grant, reward_items

STORY = 1000002
STEPS = (100021, 100022, 100023, 100024, 100025, 100026)
CONDITIONS = tuple(11000211 + 10*i for i in range(len(STEPS)))
MECHANISMS = {1000211: 0, 1000221: 1, 1000241: 3, 1000251: 4}
FINISHED = pb.QuestStatus_Finish
SAKURA = 1
PLAZA = 9999
SMALL_PLAZA_DEVICE = 1001
# One-time open-world device reward in byzp/of-ps fe890434 (AreaUnlock).
AREA_REWARDS = {1: 300, 102: 10, 101: 1000}
# Retail Event1000211, Event1000241 and Event1000251: centre, yaw quaternion, and
# the common button box projected onto the walkable plane (4 x 4 metres).
BUTTONS = {
    0: (PLAZA, 15.03, -17.87, None),
    3: (SAKURA, -39.1841164, 34.4199982, (0.7171075, -0.6969626)),
    4: (SAKURA, -35.1500015, 14.8299999, (0.9998114, -0.01942035)),
}


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
        # Event1000221's entry cutscene box is 3000 units wide and centred at zero.
        return session.scene_id == SAKURA and max(abs(x), abs(z), abs(pos.y/100)) <= 1500
    scene, origin_x, origin_z, yaw = BUTTONS[index]
    if session.scene_id != scene:
        return False
    if yaw is None:
        cosine = 0.58494
        sine = math.sqrt(1 - cosine*cosine)
    else:
        y, w = yaw
        cosine, sine = 1 - 2*y*y, 2*w*y
    dx, dz = x - origin_x, z - origin_z
    return max(abs(dx*cosine - dz*sine), abs(dx*sine + dz*cosine)) <= 2.0


def advance(session, index):
    """Check this player's exact pending condition before updating its immediate successor."""
    pid = session.player_id
    current = saved(pid, STEPS[index])
    if current is None:
        return False
    if current.status == FINISHED:
        return True
    if (current.status != pb.QuestStatus_InProgress or len(current.conditions) != 1
            or current.conditions[0].condition_id != CONDITIONS[index]
            or (index > 0 and (not (previous := saved(pid, STEPS[index-1])) or previous.status != FINISHED))):
        return False
    following = None
    if index + 1 < len(STEPS):
        if saved(pid, STEPS[index+1]) is not None:
            return False
        following = make_Quest(pid, STEPS[index+1])
    current.conditions[0].progress = current.complete_count = 1
    current.conditions[0].status = current.status = FINISHED
    save(session, current)
    notice = pb.QuestNotice(status=pb.StatusCode_OK, quests=[current])
    if following is not None:
        save(session, following)
        notice.quests.add().CopyFrom(following)
    if index == 1:
        grant(session, reward_items(pid, 672), pb.ClaimQuestRewardRsp().items)
    session.send(MsgId.QuestNotice, notice, 0)
    return True


def mechanism(session, index, packet_id):
    ok = in_trigger(session, index) and first_story_done(session.player_id) and advance(session, index)
    # Older worlds can already have registered the small plaza device prior to accepting this step.
    if ok and index == 1:
        area_activated(session, SMALL_PLAZA_DEVICE)
    status = pb.StatusCode_OK if ok else pb.StatusCode_BAD_REQ
    session.send(MsgId.NpcTalkRsp, pb.NpcTalkRsp(status=status), packet_id)


def area_activated(session, area_id):
    if area_id != SMALL_PLAZA_DEVICE or session.scene_id != SAKURA or session.dungeon[0]:
        return
    raw = db.get_area(session.player_id, SAKURA, area_id)
    was_activated = raw and pb.AreaData.FromString(raw).area_state == pb.AreaData.Unlock
    if not was_activated:
        was_activated = db.conn.execute(
            "SELECT 1 FROM claimed_rewards WHERE player_id=? AND system='area_first_unlock' AND key=?",
            (session.player_id, str(area_id))).fetchone()
    if was_activated:
        advance(session, 2)


def set_device(session, area_id, state, rsp):
    if area_id != SMALL_PLAZA_DEVICE or session.scene_id != SAKURA or session.dungeon[0]:
        return pb.StatusCode_BAD_REQ
    from private_map_progress import rebuild, device_opened
    rebuild(session.player_id)
    pid = session.player_id
    raw = db.get_area(pid, SAKURA, area_id)
    device = pb.AreaData.FromString(raw) if raw else pb.AreaData(area_id=area_id)
    already_open = bool(raw and device.area_state == pb.AreaData.Unlock)
    # Close and reopen are genuine device actions; its first-activation bonus is permanent.
    first_unlock = False
    if already_open or state == pb.AreaData.Unlock:
        inserted = db.conn.execute(
            "INSERT OR IGNORE INTO claimed_rewards(player_id, system, key) VALUES (?, 'area_first_unlock', ?)",
            (pid, str(area_id))).rowcount
        first_unlock = bool(inserted and not already_open and state == pb.AreaData.Unlock)
    if not raw or device.area_state != state:
        device.area_state = state
        device.level = device.level or 1
        device.ClearField("items")
        db.set_area(pid, SAKURA, area_id, device.SerializeToString())
    rsp.area.CopyFrom(device)
    rsp.area.ClearField("items")
    if first_unlock:
        grant(session, AREA_REWARDS, pb.ClaimQuestRewardRsp().items)
        for item_id, quantity in AREA_REWARDS.items():
            rsp.area.items.add(item_id=item_id, num=quantity)
    if state == pb.AreaData.Unlock:
        device_opened(session, area_id)
        area_activated(session, area_id)
    return pb.StatusCode_OK


def scene_entered(session, scene_id):
    if scene_id == PLAZA and session.scene_id == PLAZA and not session.dungeon[0]:
        advance(session, 5)
