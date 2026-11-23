from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_inventory import page


@packet_handler(MsgId.GetArmorReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GetArmorReq.FromString(data)
        rsp = pb.GetArmorRsp()
        rsp.status = page(session, req, rsp, "armor")
        session.send(MsgId.GetArmorRsp, rsp, packet_id)

