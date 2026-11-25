from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_moon_spots import native


@packet_handler(MsgId.PlaceMoonSpotReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        native(session, data, packet_id)
