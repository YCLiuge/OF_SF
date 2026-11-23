from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_mp_teams import rename


@packet_handler(MsgId.MPTeamRenameReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        request = pb.MPTeamRenameReq.FromString(data)
        reply = pb.MPTeamRenameRsp()
        reply.status = rename(session, request, reply)
        session.send(MsgId.MPTeamRenameRsp, reply, packet_id)

