from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_equipment_presets import mutate
from private_request import cached_mutation


@packet_handler(MsgId.CharacterEquipUpdateReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        cached_mutation(session, "CharacterEquipUpdate", data, packet_id, mutate)
