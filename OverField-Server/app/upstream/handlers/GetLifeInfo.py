from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_crafts import info


@packet_handler(MsgId.GetLifeInfoReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GetLifeInfoReq.FromString(data)
        rsp = pb.GetLifeInfoRsp()
        rsp.status = info(session, req, rsp)
        session.send(MsgId.GetLifeInfoRsp, rsp, packet_id)
