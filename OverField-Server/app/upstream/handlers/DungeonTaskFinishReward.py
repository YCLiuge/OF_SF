from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_dungeons import claim


@packet_handler(MsgId.DungeonTaskFinishRewardReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.DungeonTaskFinishRewardReq.FromString(data)
        rsp = pb.DungeonTaskFinishRewardRsp()
        rsp.status = claim(session, req, rsp)
        session.send(MsgId.DungeonTaskFinishRewardRsp, rsp, packet_id)
