from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_intervals import quick


@packet_handler(MsgId.IntervalQuickReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.IntervalQuickReq.FromString(data)
        rsp = pb.IntervalQuickRsp()
        rsp.status = quick(session, req, rsp)
        session.send(MsgId.IntervalQuickRsp,rsp,packet_id)
