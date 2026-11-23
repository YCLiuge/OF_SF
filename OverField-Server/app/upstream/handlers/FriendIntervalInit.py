from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_intervals import friend_info


@packet_handler(MsgId.FriendIntervalInitReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.FriendIntervalInitReq.FromString(data)
        rsp = pb.FriendIntervalInitRsp()
        rsp.status = friend_info(session, req, rsp)
        session.send(MsgId.FriendIntervalInitRsp,rsp,packet_id)
