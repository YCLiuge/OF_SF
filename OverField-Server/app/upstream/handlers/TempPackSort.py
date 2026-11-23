from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_inventory import sort


@packet_handler(MsgId.TempPackSortReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.TempPackSortReq.FromString(data)
        rsp = pb.TempPackSortRsp()
        rsp.status = sort(session, req, rsp)
        session.send(MsgId.TempPackSortRsp, rsp, packet_id)

