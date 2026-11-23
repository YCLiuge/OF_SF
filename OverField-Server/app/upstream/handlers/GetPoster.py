from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_inventory import page


@packet_handler(MsgId.GetPosterReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GetPosterReq.FromString(data)
        rsp = pb.GetPosterRsp()
        rsp.status = page(session, req, rsp, "poster")
        session.send(MsgId.GetPosterRsp, rsp, packet_id)

