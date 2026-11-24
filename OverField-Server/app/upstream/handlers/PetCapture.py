from private_request import cached_mutation
from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_pets import capture


@packet_handler(MsgId.PetCaptureReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        cached_mutation(session, "PetCapture", data, packet_id, capture)
