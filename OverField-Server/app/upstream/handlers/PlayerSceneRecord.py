from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId


from proto.net_pb2 import PlayerSceneRecordReq

from server.scene_data import up_recorder


@packet_handler(MsgId.PlayerSceneRecordReq)
class Handler(PacketHandler):
    def handle(self, session, data: bytes, packet_id: int):
        req = PlayerSceneRecordReq()
        req.ParseFromString(data)

        if session.dungeon[0]:
            from private_dungeons import move_inside
            move_inside(session, req.data)
            return

        up_recorder(
            session.scene_id,
            session.channel_id,
            session.player_id,
            req.data,
        )

        # The private host applies the active character's explicitly present fields and commits them.
