from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
import private_manual as manual


@packet_handler(MsgId.ManualFlagRewardReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.ManualFlagRewardReq.FromString(data)
        rsp = pb.ManualFlagRewardRsp()
        rsp.status = manual.reward(session, req.flag_id, rsp)
        session.send(MsgId.ManualFlagRewardRsp, rsp, packet_id)
