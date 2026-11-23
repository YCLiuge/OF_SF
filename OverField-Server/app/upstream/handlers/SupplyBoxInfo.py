from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_initial_queries import supply


@packet_handler(MsgId.SupplyBoxInfoReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.SupplyBoxInfoReq.FromString(data)
        rsp = pb.SupplyBoxInfoRsp()
        rsp.status = supply(session, req, rsp)
        session.send(MsgId.SupplyBoxInfoRsp, rsp, packet_id)
