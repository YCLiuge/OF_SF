from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_inventory import page


@packet_handler(MsgId.GetWeaponReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GetWeaponReq.FromString(data)
        rsp = pb.GetWeaponRsp()
        rsp.status = page(session, req, rsp, "weapon")
        session.send(MsgId.GetWeaponRsp, rsp, packet_id)

