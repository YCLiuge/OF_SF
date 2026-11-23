from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_intervals import self_info


@packet_handler(MsgId.SelfIntervalInitReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.SelfIntervalInitReq.FromString(data)
        rsp = pb.SelfIntervalInitRsp()
        rsp.status = self_info(session, req, rsp)
        session.send(MsgId.SelfIntervalInitRsp,rsp,packet_id)
