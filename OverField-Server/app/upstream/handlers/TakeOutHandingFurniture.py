from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_garden_actions import put_away


@packet_handler(MsgId.TakeOutHandingFurnitureReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        rsp=pb.TakeOutHandingFurnitureRsp()
        rsp.status=put_away(session,pb.TakeOutHandingFurnitureReq.FromString(data),rsp)
        session.send(MsgId.TakeOutHandingFurnitureRsp,rsp,packet_id)

