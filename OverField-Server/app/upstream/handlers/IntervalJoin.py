from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_intervals import join


@packet_handler(MsgId.IntervalJoinReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.IntervalJoinReq.FromString(data)
        rsp = pb.IntervalJoinRsp()
        rsp.status = join(session, req, rsp)
        session.send(MsgId.IntervalJoinRsp,rsp,packet_id)
