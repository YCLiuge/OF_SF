from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_collection_queries import owned_ids


@packet_handler(MsgId.GetCollectItemIdsReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        req=pb.GetCollectItemIdsReq.FromString(data)
        rsp=pb.GetCollectItemIdsRsp()
        rsp.status=owned_ids(session,req,rsp)
        session.send(MsgId.GetCollectItemIdsRsp,rsp,packet_id)
