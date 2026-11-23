"""Return local gameplay settings using the v3 GetArchiveInfo contract."""
from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto.net_pb2 import GetArchiveInfoReq, GetArchiveInfoRsp, StatusCode
from private_archive import get_value


@packet_handler(MsgId.GetArchiveInfoReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        request = GetArchiveInfoReq.FromString(data)
        response = GetArchiveInfoRsp(status=StatusCode.StatusCode_OK)
        try:
            response.value = get_value(session.player_id, request.key)
        except ValueError:
            response.status = StatusCode.StatusCode_FAIL
        session.send(MsgId.GetArchiveInfoRsp, response, packet_id)
