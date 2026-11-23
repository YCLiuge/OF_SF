from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_tutorial_recruit import list_pools


@packet_handler(MsgId.GachaListReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GachaListReq.FromString(data)
        rsp = pb.GachaListRsp()
        rsp.status = list_pools(session, req, rsp)
        session.send(MsgId.GachaListRsp, rsp, packet_id)
