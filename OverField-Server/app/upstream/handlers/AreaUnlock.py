from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_region_devices import toggle


@packet_handler(MsgId.AreaUnlockReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.AreaUnlockReq.FromString(data)
        rsp = pb.AreaUnlockRsp()
        rsp.status = toggle(session, req.area_id, pb.AreaData.Unlock, rsp)
        session.send(MsgId.AreaUnlockRsp, rsp, packet_id)
