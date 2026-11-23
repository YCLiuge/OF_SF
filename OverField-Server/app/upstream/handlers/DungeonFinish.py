from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_dungeons import finish


@packet_handler(MsgId.DungeonFinishReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.DungeonFinishReq.FromString(data)
        rsp = pb.DungeonFinishRsp()
        rsp.status = finish(session, rsp)
        session.send(MsgId.DungeonFinishRsp, rsp, packet_id)
