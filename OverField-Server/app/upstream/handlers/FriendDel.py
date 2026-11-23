from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_friends import delete


@packet_handler(MsgId.FriendDelReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.FriendDelReq.FromString(data)
        rsp = pb.FriendDelRsp()
        rsp.status = delete(session, req, rsp)
        session.send(MsgId.FriendDelRsp, rsp, packet_id)
