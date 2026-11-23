from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_mp_teams import dismiss


@packet_handler(MsgId.MPTeamDismissReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        request = pb.MPTeamDismissReq.FromString(data)
        reply = pb.MPTeamDismissRsp()
        reply.status = dismiss(session, request, reply)
        session.send(MsgId.MPTeamDismissRsp, reply, packet_id)

