from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_crafts import achievements


@packet_handler(MsgId.GetLifeAchieveReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GetLifeAchieveReq.FromString(data)
        rsp = pb.GetLifeAchieveRsp()
        rsp.status = achievements(session, req, rsp)
        session.send(MsgId.GetLifeAchieveRsp, rsp, packet_id)
