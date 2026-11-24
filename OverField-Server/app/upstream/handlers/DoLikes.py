from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_garden_likes import like
from proto import net_pb2 as pb


@packet_handler(MsgId.DoLikesReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        pb.DoLikesReq.FromString(data)
        session.send(MsgId.DoLikesRsp, like(session, packet_id), packet_id)
