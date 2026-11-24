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
            return move_inside(session, req.data)

        up_recorder(
            session.scene_id,
            session.channel_id,
            session.player_id,
            req.data,
        )

        lead_id = session.scene_player.team.char1.char_id
        return next((part for part in req.data.char_recorder_data_lst if part.char_id == lead_id), None)
