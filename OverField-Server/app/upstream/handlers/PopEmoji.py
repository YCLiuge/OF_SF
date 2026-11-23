from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from server.scene_data import get_session


@packet_handler(MsgId.PopEmojiReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        emoji=pb.PopEmojiReq.FromString(data).emoji_id
        if emoji not in session.entitlements.emoji:
            session.send(MsgId.PopEmojiRsp,pb.PopEmojiRsp(status=pb.StatusCode_CFG_NOT_FOUND),packet_id)
            return
        notice=pb.PopEmojiNotice(status=pb.StatusCode_OK,emoji_id=emoji,from_player_id=session.player_id)
        for peer in get_session():
            if peer.scene_id==session.scene_id and peer.channel_id==session.channel_id and peer.dungeon[0]==session.dungeon[0]:
                peer.send(MsgId.PopEmojiNotice,notice,0)
        session.send(MsgId.PopEmojiRsp,pb.PopEmojiRsp(status=pb.StatusCode_OK),packet_id)
