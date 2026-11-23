from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_plaza import change_instrument


@packet_handler(MsgId.ChangeMusicalItemReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.ChangeMusicalItemReq.FromString(data)
        rsp = pb.ChangeMusicalItemRsp()
        rsp.status = change_instrument(session, req, rsp)
        session.send(MsgId.ChangeMusicalItemRsp, rsp, packet_id)
