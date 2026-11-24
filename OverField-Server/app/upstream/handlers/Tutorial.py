"""Record the 24 national-client prologue tutorial reports for the current character."""
from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_first_quest import saved
from proto import net_pb2 as pb
from utils import db
import private_bottle_intro as intro

# PC 1.1.12 StatisticSystem.InitP2TutorialKeyToName.
EVENTS = {
    1:"操作指引", 2:"区域装置激活", 3:"跳跃引导", 4:"收集水晶1", 5:"打开宝箱1",
    6:"攻击引导", 7:"收集水晶2", 8:"技能引导", 9:"交互风车离开空间", 10:"出现开放空间字幕",
    11:"菲对话开始", 12:"菲对话结束", 13:"梓询问招募情况开始", 14:"梓询问招募情况结束",
    15:"提交招募申请", 16:"提交招募申请对话结束", 17:"引导招募icon", 18:"引导招募单抽",
    19:"诗对话开始", 20:"诗对话结束", 21:"点击引导剧情", 22:"点击引导剧情序章",
    23:"引导任务奖励领取", 24:"引导接受任务",
}


def record(session, req):
    phase = req.tutorial_id
    if phase not in EVENTS or (req.tutorial_name and req.tutorial_name != EVENTS[phase]) or session.dungeon[0]:
        return pb.StatusCode_BAD_REQ
    pid = session.player_id
    seen = {int(key) for (key,) in db.conn.execute(
        "SELECT k FROM player_kv WHERE player_id=? AND ns='tutorial' AND k!='ids' AND CAST(v AS INTEGER)>0",
        (pid,)) if key.isascii() and key.isdecimal()}
    if phase >= 13:
        # Dialogue, recruit, chapter and reward UI reports can arrive after the next quest starts.
        # The authoritative quest and inventory are changed by their own gameplay requests.
        required_step = 100011 if phase <= 14 else 100012 if phase <= 16 else 100013
        quest = saved(pid, required_step)
        if quest is None or quest.status == pb.QuestStatus_Locked:
            return pb.StatusCode_BAD_REQ
    elif phase in (11,12):
        if session.scene_id != intro.PLAZA:
            return pb.StatusCode_BAD_REQ
        if phase not in seen:
            quest = saved(pid,100011)
            # The dialog callback can finish after the native NPC interaction advances this quest.
            if quest is None or quest.status == pb.QuestStatus_Locked:
                return pb.StatusCode_BAD_REQ
            if phase == 12 and 11 not in seen and quest.status == pb.QuestStatus_InProgress:
                return pb.StatusCode_BAD_REQ
    else:
        enrolled = intro.is_enrolled(pid)
        late = session.scene_id == intro.PLAZA and phase in (9,10) and enrolled=="departed" and bool(seen & set(range(1,10)))
        if not enrolled or not (session.scene_id == intro.SCENE or late):
            return pb.StatusCode_BAD_REQ
    if phase in seen:
        return pb.StatusCode_OK
    if phase == 9:
        from private_story_starter import grant_starter
        status = grant_starter(session, pb.GamePlayRewardRsp().items)
        if status != pb.StatusCode_OK:
            return status
    db.conn.execute("INSERT OR REPLACE INTO player_kv(player_id,ns,k,v) VALUES(?,'tutorial',?,1)",(pid,str(phase)))
    row = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='tutorial' AND k='ids'",(pid,)).fetchone()
    aggregate = row[0] if row else b""
    text = aggregate.decode("ascii") if isinstance(aggregate,bytes) else str(aggregate)
    ids = {int(k) for k in text.split(",") if k.isascii() and k.isdecimal()}
    encoded = ",".join(map(str,sorted(ids | seen | {phase}))).encode("ascii")
    db.conn.execute("INSERT OR REPLACE INTO player_kv(player_id,ns,k,v) VALUES(?,'tutorial','ids',?)",(pid,encoded))
    return pb.StatusCode_OK


@packet_handler(MsgId.TutorialReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.TutorialReq.FromString(data)
        session.send(MsgId.TutorialRsp,pb.TutorialRsp(status=record(session,req)),packet_id)
