from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_crafts import level_up


@packet_handler(MsgId.LifeLeveUpReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.LifeLeveUpReq.FromString(data)
        rsp = pb.LifeLeveUpRsp()
        rsp.status = level_up(session, req, rsp)
        session.send(MsgId.LifeLeveUpRsp, rsp, packet_id)
