from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_inventory import drop


@packet_handler(MsgId.TempPackItemDropReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.TempPackItemDropReq.FromString(data)
        rsp = pb.TempPackItemDropRsp()
        rsp.status = drop(session, req, rsp)
        session.send(MsgId.TempPackItemDropRsp, rsp, packet_id)

