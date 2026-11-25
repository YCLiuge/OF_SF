from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_secondary_password import apply
from proto import net_pb2 as pb


@packet_handler(MsgId.SPOperateReq)
class Handler(PacketHandler):
    def handle(self, session, operation, packet_id):
        reply = pb.SPOperateRsp()
        apply(session, operation, reply)
        session.send(MsgId.SPOperateRsp, reply, packet_id)
