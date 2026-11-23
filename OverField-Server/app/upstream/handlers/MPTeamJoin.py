from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_mp_teams import join


@packet_handler(MsgId.MPTeamJoinReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        request = pb.MPTeamJoinReq.FromString(data)
        reply = pb.MPTeamJoinRsp()
        reply.status = join(session, request, reply)
        session.send(MsgId.MPTeamJoinRsp, reply, packet_id)

