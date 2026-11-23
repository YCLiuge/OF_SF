from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_explore import handle

@packet_handler(MsgId.ExploreReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        request=pb.ExploreReq.FromString(data)
        response=pb.ExploreRsp()
        response.status=handle(session,request,response)
        session.send(MsgId.ExploreRsp,response,packet_id)
