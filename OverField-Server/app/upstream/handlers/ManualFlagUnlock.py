from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
import private_manual as manual


@packet_handler(MsgId.ManualFlagUnlockReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.ManualFlagUnlockReq.FromString(data)
        rsp = pb.ManualFlagUnlockRsp()
        rsp.status = manual.unlock(session, req.flag_id, rsp)
        session.send(MsgId.ManualFlagUnlockRsp, rsp, packet_id)
