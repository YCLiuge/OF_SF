from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
import private_manual as manual


@packet_handler(MsgId.ManualListReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        pb.ManualListReq.FromString(data)
        rsp = pb.ManualListRsp()
        rsp.status = manual.list_flags(session, rsp)
        session.send(MsgId.ManualListRsp, rsp, packet_id)
