from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_request import cached_mutation
from private_posters import star_up


@packet_handler(MsgId.PosterStarUpReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        cached_mutation(session, "PosterStarUp", data, packet_id, star_up)
