from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_plaza import send_action


@packet_handler(MsgId.SendActionReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.SendActionReq.FromString(data)
        rsp = pb.SendActionRsp()
        rsp.status = send_action(session, req, rsp)
        session.send(MsgId.SendActionRsp, rsp, packet_id)
