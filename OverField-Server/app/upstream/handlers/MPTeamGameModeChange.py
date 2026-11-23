from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_mp_teams import change_mode


@packet_handler(MsgId.MPTeamGameModeChangeReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        request = pb.MPTeamGameModeChangeReq.FromString(data)
        reply = pb.MPTeamGameModeChangeRsp()
        reply.status = change_mode(session, request, reply)
        session.send(MsgId.MPTeamGameModeChangeRsp, reply, packet_id)

