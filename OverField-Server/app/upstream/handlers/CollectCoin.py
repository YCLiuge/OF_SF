from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_dungeons import coin


@packet_handler(MsgId.CollectCoinReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.CollectCoinReq.FromString(data)
        rsp = pb.CollectCoinRsp()
        rsp.status = coin(session, req, rsp)
        session.send(MsgId.CollectCoinRsp, rsp, packet_id)
