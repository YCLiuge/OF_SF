from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_region_devices import toggle


@packet_handler(MsgId.AreaCloseReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.AreaCloseReq.FromString(data)
        rsp = pb.AreaCloseRsp()
        rsp.status = toggle(session, req.area_id, pb.AreaData.Close, rsp)
        session.send(MsgId.AreaCloseRsp, rsp, packet_id)
