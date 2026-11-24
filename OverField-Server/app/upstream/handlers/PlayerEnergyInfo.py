from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_recovery import handle


@packet_handler(MsgId.PlayerEnergyInfoReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        handle(session, "PlayerEnergyInfo", data, packet_id)
