from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_mp_teams import kick


@packet_handler(MsgId.MPTeamKickMemberReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        request = pb.MPTeamKickMemberReq.FromString(data)
        reply = pb.MPTeamKickMemberRsp()
        reply.status = kick(session, request, reply)
        session.send(MsgId.MPTeamKickMemberRsp, reply, packet_id)

