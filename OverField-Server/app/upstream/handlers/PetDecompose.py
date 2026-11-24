from private_request import cached_mutation
from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_pets import release


@packet_handler(MsgId.PetDecomposeReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        cached_mutation(session, "PetDecompose", data, packet_id, release)
