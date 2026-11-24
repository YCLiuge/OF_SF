from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_character_training import handle


@packet_handler(MsgId.CharacterLevelBreakReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        handle(session, "CharacterLevelBreak", data, packet_id)
