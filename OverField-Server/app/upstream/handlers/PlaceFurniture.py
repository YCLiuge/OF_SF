from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_garden_actions import place


@packet_handler(MsgId.PlaceFurnitureReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.PlaceFurnitureReq.FromString(data)
        rsp = pb.PlaceFurnitureRsp()
        rsp.status = place(session, req, rsp)
        session.send(MsgId.PlaceFurnitureRsp, rsp, packet_id)
