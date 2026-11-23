from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_dungeons import objectives


@packet_handler(MsgId.DungeonViewReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.DungeonViewReq.FromString(data)
        rsp = pb.DungeonViewRsp()
        rsp.status = objectives(session, req, rsp)
        session.send(MsgId.DungeonViewRsp, rsp, packet_id)
