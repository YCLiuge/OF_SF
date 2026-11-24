from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_life_skills import learn
from proto import net_pb2 as pb


@packet_handler(MsgId.LifeSkillLevelUpReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.LifeSkillLevelUpReq.FromString(data)
        session.send(MsgId.LifeSkillLevelUpRsp, learn(session, req, packet_id), packet_id)
