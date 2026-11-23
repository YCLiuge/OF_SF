from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_boxes import pickup


@packet_handler(MsgId.TreasureBoxPickupReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.TreasureBoxPickupReq.FromString(data)
        rsp = pb.TreasureBoxPickupRsp()
        rsp.status = pickup(session, req, rsp)
        session.send(MsgId.TreasureBoxPickupRsp, rsp, packet_id)
