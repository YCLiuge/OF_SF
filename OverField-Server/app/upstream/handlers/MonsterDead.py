from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_dungeons import monster


@packet_handler(MsgId.MonsterDeadReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.MonsterDeadReq.FromString(data)
        rsp = pb.MonsterDeadRsp()
        rsp.status = monster(session, req, rsp)
        session.send(MsgId.MonsterDeadRsp, rsp, packet_id)
