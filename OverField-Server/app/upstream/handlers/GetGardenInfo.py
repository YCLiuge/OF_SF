from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId

from proto.net_pb2 import GetGardenInfoRsp, StatusCode
import utils.db as db


@packet_handler(MsgId.GetGardenInfoReq)
class Handler(PacketHandler):
    def handle(self, session, data: bytes, packet_id: int):
        rsp = GetGardenInfoRsp()
        rsp.status = StatusCode.StatusCode_OK

        (
            rsp.garden_info.likes_num,
            rsp.garden_info.access_num,
            rsp.garden_info.furniture_num,
            rsp.garden_info.furniture_limit_num,
            rsp.garden_info.is_open,
        ) = db.get_garden_info(session.player_id)

        from private_channels import HOME, count_here, room_limit
        if session.scene_id==HOME:
            rsp.garden_info.furniture_num=count_here(session)
            limit=room_limit(session)
            rsp.garden_info.furniture_limit_num=0x7fffffff if limit is None else limit
        else:
            rsp.garden_info.furniture_limit_num=session.realm_rules.home_furniture_limit or 0x7fffffff
        session.send(MsgId.GetGardenInfoRsp, rsp, packet_id)  # 1685,1686
