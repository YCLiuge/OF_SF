from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_initial_queries import generic


@packet_handler(MsgId.GenericGameBReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GenericGameBReq.FromString(data)
        rsp = pb.GenericGameBRsp()
        rsp.status = generic(session, req, rsp)
        session.send(MsgId.GenericGameBRsp, rsp, packet_id)
