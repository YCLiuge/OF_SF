from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_mp_teams import quit


@packet_handler(MsgId.MPTeamQuitReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        request = pb.MPTeamQuitReq.FromString(data)
        reply = pb.MPTeamQuitRsp()
        reply.status = quit(session, request, reply)
        session.send(MsgId.MPTeamQuitRsp, reply, packet_id)

