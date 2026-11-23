from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_mp_teams import info


@packet_handler(MsgId.MPTeamInfoReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        request = pb.MPTeamInfoReq.FromString(data)
        reply = pb.MPTeamInfoRsp()
        reply.status = info(session, request, reply)
        session.send(MsgId.MPTeamInfoRsp, reply, packet_id)

