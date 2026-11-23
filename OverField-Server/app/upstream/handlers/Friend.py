from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_initial_queries import friends


@packet_handler(MsgId.FriendReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.FriendReq.FromString(data)
        rsp = pb.FriendRsp()
        rsp.status = friends(session, req, rsp)
        session.send(MsgId.FriendRsp, rsp, packet_id)
