from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_collection_queries import moon_info


@packet_handler(MsgId.GetCollectMoonInfoReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        req=pb.GetCollectMoonInfoReq.FromString(data)
        rsp=pb.GetCollectMoonInfoRsp()
        rsp.status=moon_info(session,req,rsp)
        session.send(MsgId.GetCollectMoonInfoRsp,rsp,packet_id)
