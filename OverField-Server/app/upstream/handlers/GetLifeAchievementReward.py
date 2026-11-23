from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_crafts import claim


@packet_handler(MsgId.GetLifeAchievementRewardReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GetLifeAchievementRewardReq.FromString(data)
        rsp = pb.GetLifeAchievementRewardRsp()
        rsp.status = claim(session, req, rsp)
        session.send(MsgId.GetLifeAchievementRewardRsp, rsp, packet_id)
