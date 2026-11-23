from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_garden_actions import remove


@packet_handler(MsgId.TakeOutFurnitureReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.TakeOutFurnitureReq.FromString(data)
        rsp = pb.TakeOutFurnitureRsp()
        rsp.status = remove(session, req, rsp)
        session.send(MsgId.TakeOutFurnitureRsp, rsp, packet_id)
