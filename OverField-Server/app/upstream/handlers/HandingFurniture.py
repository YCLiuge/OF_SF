from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_garden_actions import hand


@packet_handler(MsgId.HandingFurnitureReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        rsp=pb.HandingFurnitureRsp()
        rsp.status=hand(session,pb.HandingFurnitureReq.FromString(data),rsp)
        session.send(MsgId.HandingFurnitureRsp,rsp,packet_id)

