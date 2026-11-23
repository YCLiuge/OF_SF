from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_explore import handle

@packet_handler(MsgId.ExploreInitReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        request=pb.ExploreInitReq.FromString(data)
        response=pb.ExploreInitRsp()
        response.status=handle(session,request,response)
        session.send(MsgId.ExploreInitRsp,response,packet_id)
