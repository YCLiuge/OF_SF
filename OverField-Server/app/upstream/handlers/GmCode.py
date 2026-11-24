from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_gm import execute
from proto import net_pb2 as pb


@packet_handler(MsgId.GmCodeReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req=pb.GmCodeReq.FromString(data)
        session.send(MsgId.GmCodeRsp,execute(session,req.param,"native",packet_id),packet_id)
