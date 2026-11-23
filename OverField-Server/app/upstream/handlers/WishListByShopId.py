from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_wishes import by_shop


@packet_handler(MsgId.WishListByShopIdReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.WishListByShopIdReq.FromString(data)
        rsp = pb.WishListByShopIdRsp()
        rsp.status = by_shop(session, req, rsp)
        session.send(MsgId.WishListByShopIdRsp, rsp, packet_id)
