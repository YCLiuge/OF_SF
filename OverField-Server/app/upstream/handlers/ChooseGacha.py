from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_gacha import choose


@packet_handler(MsgId.ChooseGachaReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.ChooseGachaReq.FromString(data)
        rsp = pb.ChooseGachaRsp()
        rsp.status = choose(session, req, rsp)
        session.send(MsgId.ChooseGachaRsp, rsp, packet_id)
