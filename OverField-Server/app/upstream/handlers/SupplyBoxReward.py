from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_garden_actions import claim_supply


@packet_handler(MsgId.SupplyBoxRewardReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.SupplyBoxRewardReq.FromString(data)
        rsp = pb.SupplyBoxRewardRsp()
        rsp.status = claim_supply(session, req, rsp)
        session.send(MsgId.SupplyBoxRewardRsp, rsp, packet_id)
