from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_explore import handle

@packet_handler(MsgId.ExploreRewardReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        request=pb.ExploreRewardReq.FromString(data)
        response=pb.ExploreRewardRsp()
        response.status=handle(session,request,response)
        session.send(MsgId.ExploreRewardRsp,response,packet_id)
