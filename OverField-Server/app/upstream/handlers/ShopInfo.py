from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_shop import info as shop


@packet_handler(MsgId.ShopInfoReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.ShopInfoReq.FromString(data)
        rsp = pb.ShopInfoRsp()
        rsp.status = shop(session, req, rsp)
        session.send(MsgId.ShopInfoRsp, rsp, packet_id)
