from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_collection_queries import interact_moon


@packet_handler(MsgId.EmotionMoonInteractReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        req=pb.EmotionMoonInteractReq.FromString(data)
        rsp=pb.EmotionMoonInteractRsp()
        rsp.status=interact_moon(session,req,rsp)
        session.send(MsgId.EmotionMoonInteractRsp,rsp,packet_id)
