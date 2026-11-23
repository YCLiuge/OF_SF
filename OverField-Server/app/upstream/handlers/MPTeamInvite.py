from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_mp_teams import invite


@packet_handler(MsgId.MPTeamInviteReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        request = pb.MPTeamInviteReq.FromString(data)
        reply = pb.MPTeamInviteRsp()
        reply.status = invite(session, request, reply)
        session.send(MsgId.MPTeamInviteRsp, reply, packet_id)

