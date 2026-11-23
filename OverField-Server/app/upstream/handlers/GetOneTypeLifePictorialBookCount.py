from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_crafts import book


@packet_handler(MsgId.GetOneTypeLifePictorialBookCountReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GetOneTypeLifePictorialBookCountReq.FromString(data)
        rsp = pb.GetOneTypeLifePictorialBookCountRsp()
        rsp.status = book(session, req, rsp)
        session.send(MsgId.GetOneTypeLifePictorialBookCountRsp, rsp, packet_id)
