from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_friends import search


@packet_handler(MsgId.FriendSearchReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.FriendSearchReq.FromString(data)
        rsp = pb.FriendSearchRsp()
        rsp.status = search(session, req, rsp)
        session.send(MsgId.FriendSearchRsp, rsp, packet_id)
