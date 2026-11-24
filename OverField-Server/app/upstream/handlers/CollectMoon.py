from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_collection_queries import collect_moon


@packet_handler(MsgId.CollectMoonReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        req=pb.CollectMoonReq.FromString(data)
        rsp=pb.CollectMoonRsp()
        rsp.status=collect_moon(session,req,rsp)
        session.send(MsgId.CollectMoonRsp,rsp,packet_id)
