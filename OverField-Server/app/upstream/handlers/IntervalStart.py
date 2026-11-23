from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_intervals import start


@packet_handler(MsgId.IntervalStartReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.IntervalStartReq.FromString(data)
        rsp = pb.IntervalStartRsp()
        rsp.status = start(session, req, rsp)
        session.send(MsgId.IntervalStartRsp,rsp,packet_id)
