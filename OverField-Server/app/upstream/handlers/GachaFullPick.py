from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_gacha import pick


@packet_handler(MsgId.GachaFullPickReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GachaFullPickReq.FromString(data)
        rsp = pb.GachaFullPickRsp()
        rsp.status = pick(session, req, rsp)
        session.send(MsgId.GachaFullPickRsp, rsp, packet_id)
