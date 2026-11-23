from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_inventory import wear


@packet_handler(MsgId.TempPackWearEquipReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.TempPackWearEquipReq.FromString(data)
        rsp = pb.TempPackWearEquipRsp()
        rsp.status = wear(session, req, rsp)
        session.send(MsgId.TempPackWearEquipRsp, rsp, packet_id)

