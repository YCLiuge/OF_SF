from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_recovery import handle


@packet_handler(MsgId.PlayerEnergyBuyReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        handle(session, "PlayerEnergyBuy", data, packet_id)
