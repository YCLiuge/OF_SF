from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_mp_teams import create


@packet_handler(MsgId.MPTeamCreateReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        request = pb.MPTeamCreateReq.FromString(data)
        reply = pb.MPTeamCreateRsp()
        reply.status = create(session, request, reply)
        session.send(MsgId.MPTeamCreateRsp, reply, packet_id)

