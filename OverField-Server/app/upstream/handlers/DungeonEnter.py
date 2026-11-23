from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_dungeons import enter
from server import scene_data


@packet_handler(MsgId.DungeonEnterReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.DungeonEnterReq.FromString(data)
        rsp = pb.DungeonEnterRsp()
        was_inside = bool(session.dungeon[0])
        rsp.status = enter(session, req, rsp)
        session.send(MsgId.DungeonEnterRsp, rsp, packet_id)
        if not was_inside and rsp.status == pb.StatusCode_OK:
            session.home_seat = None
            notice = pb.ServerSceneSyncDataNotice(status=pb.StatusCode_OK)
            event = notice.data.add(player_id=session.player_id)
            event.server_data.add(action_type=pb.SceneActionType_LEAVE)
            scene_data.up_scene_action(session.scene_id,session.channel_id,notice)
