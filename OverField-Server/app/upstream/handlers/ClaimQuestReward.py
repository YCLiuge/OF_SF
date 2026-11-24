from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_tutorial_recruit import claim_story
from private_story_catalog import claim
from private_extra_story_rewards import claim as claim_extra


@packet_handler(MsgId.ClaimQuestRewardReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.ClaimQuestRewardReq.FromString(data)
        rsp = pb.ClaimQuestRewardRsp()
        operation = claim_story if req.quest_id == 1000001 else claim
        if req.quest_type == pb.EQuestType_RandomEventQuest:
            from private_bless_tasks import claim as operation
        elif req.quest_type == pb.EQuestType_ExtraQuest:
            operation = claim_extra
        rsp.status = operation(session,req,rsp)
        session.send(MsgId.ClaimQuestRewardRsp,rsp,packet_id)
