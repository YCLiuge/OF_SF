from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from config import Config

from proto.net_pb2 import (
    ChangeSceneChannelReq,
    ChangeSceneChannelRsp,
    SceneDataNotice,
    ServerSceneSyncDataNotice,
    StatusCode,
    PackNotice,
    SceneActionType,
    ChatMsgRecordInitNotice,
)

import server.scene_data as scene_data
from utils.pb_create import make_SceneDataNotice, make_Achieve


@packet_handler(MsgId.ChangeSceneChannelReq)
class Handler(PacketHandler):
    def handle(self, session, data: bytes, packet_id: int):
        req = ChangeSceneChannelReq()
        req.ParseFromString(data)

        from private_channels import change, selected
        channel,status=selected(session,req)
        if req.scene_id not in session.valid_scenes:status=StatusCode.StatusCode_BAD_REQ
        if status!=StatusCode.StatusCode_OK:
            session.send(MsgId.ChangeSceneChannelRsp,ChangeSceneChannelRsp(status=status),packet_id)
            return
        # 向其他玩家广播离开事件
        notice = ServerSceneSyncDataNotice()
        notice.status = StatusCode.StatusCode_OK
        d = notice.data.add()
        d.player_id = session.player_id
        sd = d.server_data.add()
        sd.action_type = SceneActionType.SceneActionType_LEAVE
        scene_data.up_scene_action(session.scene_id, session.channel_id, notice)

        from private_bottle_intro import SCENE, PLAZA, departed
        if session.scene_id == SCENE and req.scene_id == PLAZA:
            departed(session)
        if (session.scene_id,session.channel_id)!=(req.scene_id,channel):
            session.active_flag = None
            from private_field_activities import depart
            depart(session)
        previous_scene = session.scene_id
        change(session,req.scene_id,channel)

        rsp = ChangeSceneChannelRsp()
        rsp.status = StatusCode.StatusCode_OK

        rsp.scene_id = req.scene_id
        rsp.channel_id = session.channel_id
        rsp.channel_label = session.channel_id
        # rsp.password_allow_time = 0
        from realm_uids import local_uid
        rsp.target_player_id = local_uid(req.target_player_label) if req.target_player_label else 0

        session.send(MsgId.ChangeSceneChannelRsp, rsp, packet_id)

        # 更新场景
        rsp = SceneDataNotice()
        rsp.CopyFrom(make_SceneDataNotice(session))
        session.send(MsgId.SceneDataNotice, rsp, 0)

        from private_story_entry import scene_entered
        scene_entered(session, req.scene_id)

        from private_chat import nearby_history
        nearby_history(session)

        if req.scene_id == 9999:
            from private_inventory import store
            store(session)

        # 广播加入消息
        notice = ServerSceneSyncDataNotice()
        notice.status = StatusCode.StatusCode_OK
        d = notice.data.add()
        d.player_id = session.player_id
        sd = d.server_data.add()
        sd.action_type = SceneActionType.SceneActionType_ENTER
        sd.player.CopyFrom(session.scene_player)
        scene_data.up_scene_action(session.scene_id, session.channel_id, notice)

        if previous_scene != session.scene_id:
            make_Achieve(session, session.scene_id)
            from private_story_objectives import explored
            explored(session, session.scene_id)
