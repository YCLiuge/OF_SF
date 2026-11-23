from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_gacha import draw


@packet_handler(MsgId.GachaReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GachaReq.FromString(data)
        rsp = pb.GachaRsp()
        rsp.status = draw(session, req, rsp, packet_id)
        session.send(MsgId.GachaRsp, rsp, packet_id)
