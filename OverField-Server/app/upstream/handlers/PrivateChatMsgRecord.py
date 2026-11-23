from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_chat import history


@packet_handler(MsgId.PrivateChatMsgRecordReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.PrivateChatMsgRecordReq.FromString(data)
        rsp = pb.PrivateChatMsgRecordRsp()
        rsp.status = history(session, req, rsp)
        session.send(MsgId.PrivateChatMsgRecordRsp, rsp, packet_id)
