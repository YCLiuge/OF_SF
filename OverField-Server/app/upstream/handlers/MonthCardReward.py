from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_gifts import month_reward


@packet_handler(MsgId.MonthCardRewardReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        pb.MonthCardRewardReq.FromString(data)
        reply=pb.MonthCardRewardRsp()
        reply.status=month_reward(session,reply)
        session.send(MsgId.MonthCardRewardRsp,reply,packet_id)
