from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_friends import add


@packet_handler(MsgId.FriendAddReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.FriendAddReq.FromString(data)
        rsp = pb.FriendAddRsp()
        rsp.status = add(session, req, rsp)
        session.send(MsgId.FriendAddRsp, rsp, packet_id)
