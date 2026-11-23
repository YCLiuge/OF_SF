from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_boxes import gameplay_reward


@packet_handler(MsgId.GamePlayRewardReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GamePlayRewardReq.FromString(data)
        rsp = pb.GamePlayRewardRsp()
        rsp.status = gameplay_reward(session, req, rsp)
        session.send(MsgId.GamePlayRewardRsp, rsp, packet_id)
