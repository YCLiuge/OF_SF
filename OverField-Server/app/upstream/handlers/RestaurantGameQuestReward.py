from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_restaurant import handle, reward


@packet_handler(MsgId.RestaurantGameQuestRewardReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        handle(session,data,packet_id,"RestaurantGameQuestReward",reward)
