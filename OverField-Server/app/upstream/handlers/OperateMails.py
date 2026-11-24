from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_mail import operate


@packet_handler(MsgId.OperateMailsReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        operate(session, pb.OperateMailsReq.FromString(data), packet_id)
