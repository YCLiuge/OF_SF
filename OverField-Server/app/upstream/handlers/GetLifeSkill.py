from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_life_skills import listing
from proto import net_pb2 as pb


@packet_handler(MsgId.GetLifeSkillReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GetLifeSkillReq.FromString(data)
        rsp = pb.GetLifeSkillRsp()
        rsp.status = listing(session, req, rsp)
        session.send(MsgId.GetLifeSkillRsp, rsp, packet_id)
