"""Publish the original client's scene interaction playback state to its current room."""
from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from server.scene_data import channel_members


@packet_handler(MsgId.SceneInterActionPlayStatusReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.SceneInterActionPlayStatusReq.FromString(data)
        rsp = pb.SceneInterActionPlayStatusRsp(status=pb.StatusCode_OK)
        if req.push_type not in pb.InterActionPushType.values():
            rsp.status = pb.StatusCode_BAD_REQ
        elif req.push_type == pb.AddKeepingStatus:
            if not req.action_status.id:
                rsp.status = pb.StatusCode_BAD_REQ
            else:
                session.scene_player.status.CopyFrom(req.action_status)
        elif req.push_type == pb.RemovingStatus:
            if not req.action_status.id or req.action_status.id == session.scene_player.status.id:
                session.scene_player.ClearField("status")

        session.send(MsgId.SceneInterActionPlayStatusRsp, rsp, packet_id)
        if rsp.status != pb.StatusCode_OK:
            return
        notice = pb.SceneInterActionPlayStatusNotice(status=pb.StatusCode_OK, player_id=session.player_id,
                                                    push_type=req.push_type, action_status=req.action_status)
        # Realm furniture is persisted by its ownership-checked placement/return handlers.
        # This message only describes the actor's animation, including its normal end/reset.
        for peer in channel_members(session.channel_id):
            same_room = (not session.dungeon[0] and not peer.dungeon[0] and peer.logged_in
                         and peer.scene_id == session.scene_id and peer.channel_id == session.channel_id)
            if peer is session or same_room:
                peer.send(MsgId.SceneInterActionPlayStatusNotice, notice, 0)
