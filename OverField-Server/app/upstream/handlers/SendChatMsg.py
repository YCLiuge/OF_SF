from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_chat import send


@packet_handler(MsgId.SendChatMsgReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.SendChatMsgReq.FromString(data)
        rsp = pb.SendChatMsgRsp()
        rsp.status = send(session, req, rsp)
        session.send(MsgId.SendChatMsgRsp, rsp, packet_id)
