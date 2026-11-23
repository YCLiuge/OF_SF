from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_gacha import draw, trace_draw


@packet_handler(MsgId.GachaReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GachaReq.FromString(data)
        rsp = pb.GachaRsp()
        rsp.status = draw(session, req, rsp, packet_id)
        trace_draw(session, req, len(data), rsp.status)
        session.send(MsgId.GachaRsp, rsp, packet_id)
