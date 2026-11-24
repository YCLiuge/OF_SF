from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_equipment_presets import switch
from private_request import cached_mutation


@packet_handler(MsgId.CharacterEquipPresetSwitchReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        cached_mutation(session, "CharacterEquipPresetSwitch", data, packet_id, switch)
