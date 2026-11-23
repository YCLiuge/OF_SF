from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_tutorial_recruit import claim_story


@packet_handler(MsgId.ClaimQuestRewardReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.ClaimQuestRewardReq.FromString(data)
        rsp = pb.ClaimQuestRewardRsp()
        rsp.status = claim_story(session, req, rsp)
        session.send(MsgId.ClaimQuestRewardRsp, rsp, packet_id)
