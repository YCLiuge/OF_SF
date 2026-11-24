from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_request import set_after_commit


@packet_handler(MsgId.PlayerOfflineReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        pb.PlayerOfflineReq.FromString(data)
        reply = pb.PlayerOfflineRsp(status=pb.StatusCode_OK,reason=pb.PlayerOfflineReason_NONE)
        session.send(MsgId.PlayerOfflineRsp,reply,packet_id)
        set_after_commit(session,"logout_requested",True)
