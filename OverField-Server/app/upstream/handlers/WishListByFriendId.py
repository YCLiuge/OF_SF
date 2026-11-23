from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_wishes import by_friend


@packet_handler(MsgId.WishListByFriendIdReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.WishListByFriendIdReq.FromString(data)
        rsp = pb.WishListByFriendIdRsp()
        rsp.status = by_friend(session, req, rsp)
        session.send(MsgId.WishListByFriendIdRsp, rsp, packet_id)
