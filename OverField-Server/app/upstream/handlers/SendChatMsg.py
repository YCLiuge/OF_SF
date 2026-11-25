from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_chat import send, NEAR, WORLD, DM
from private_gm import chat_command, execute


@packet_handler(MsgId.SendChatMsgReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.SendChatMsgReq.FromString(data)
        rsp = pb.SendChatMsgRsp()
        if chat_command(req.text):
            if req.type in (NEAR, WORLD, DM) and not req.expression:
                result = execute(session, req.text, "chat", packet_id)
                rsp.status, rsp.text = result.status, result.result
            else:
                rsp.status = pb.StatusCode_BAD_REQ
        elif req.type == NEAR and not req.expression and req.text in ("/collapse", "/collapse cancel"):
            from private_mp_rooms import select_collapse
            rsp.status = select_collapse(session, req.text.endswith(" cancel"), rsp)
        else:
            rsp.status = send(session, req, rsp)
        session.send(MsgId.SendChatMsgRsp, rsp, packet_id)
