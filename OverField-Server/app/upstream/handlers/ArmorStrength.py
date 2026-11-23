from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_equipment import handle


@packet_handler(MsgId.ArmorStrengthReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        handle(session, "ArmorStrength", data, packet_id)
