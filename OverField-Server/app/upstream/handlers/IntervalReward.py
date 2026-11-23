from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_intervals import reward


@packet_handler(MsgId.IntervalRewardReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.IntervalRewardReq.FromString(data)
        rsp = pb.IntervalRewardRsp()
        rsp.status = reward(session, req, rsp)
        session.send(MsgId.IntervalRewardRsp,rsp,packet_id)
