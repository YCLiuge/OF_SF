from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_friends import handle


@packet_handler(MsgId.FriendHandleReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.FriendHandleReq.FromString(data)
        rsp = pb.FriendHandleRsp()
        rsp.status = handle(session, req, rsp)
        session.send(MsgId.FriendHandleRsp, rsp, packet_id)
