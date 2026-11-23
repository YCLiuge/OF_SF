from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_friends import block


@packet_handler(MsgId.FriendBlackReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.FriendBlackReq.FromString(data)
        rsp = pb.FriendBlackRsp()
        rsp.status = block(session, req, rsp)
        session.send(MsgId.FriendBlackRsp, rsp, packet_id)
