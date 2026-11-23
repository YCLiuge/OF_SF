from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_equipment import handle


@packet_handler(MsgId.WeaponInscriptionInlaidReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        handle(session, "WeaponInscriptionInlaid", data, packet_id)
