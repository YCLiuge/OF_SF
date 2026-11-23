"""Persist local gameplay settings using the v3 SetArchiveInfo contract."""
from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto.net_pb2 import SetArchiveInfoReq, SetArchiveInfoRsp, StatusCode
from private_archive import set_value


@packet_handler(MsgId.SetArchiveInfoReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        request = SetArchiveInfoReq.FromString(data)
        response = SetArchiveInfoRsp(status=StatusCode.StatusCode_OK)
        try:
            set_value(session.player_id, request.key, request.value)
        except ValueError:
            response.status = StatusCode.StatusCode_FAIL
        else:
            response.key, response.value = request.key, request.value
        session.send(MsgId.SetArchiveInfoRsp, response, packet_id)
