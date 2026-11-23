from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_badge_tree import list_badges


@packet_handler(MsgId.AbilityBadgeListReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.AbilityBadgeListReq.FromString(data)
        rsp = pb.AbilityBadgeListRsp()
        rsp.status = list_badges(session, req, rsp)
        session.send(MsgId.AbilityBadgeListRsp, rsp, packet_id)
