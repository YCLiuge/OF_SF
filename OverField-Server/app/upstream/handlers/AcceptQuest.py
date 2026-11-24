from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_first_quest import STEPS as FIRST, accept as accept_first, saved
from private_story_entry import accept as accept_second
from private_story_catalog import accept


@packet_handler(MsgId.AcceptQuestReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.AcceptQuestReq.FromString(data)
        rsp = pb.AcceptQuestRsp()
        if req.quest_type == pb.EQuestType_MainQuest and req.quest_id == 1000002:
            return accept_second(session, packet_id)
        if req.quest_type == pb.EQuestType_MainQuest and req.quest_id == 1000001:
            if session.scene_id != 100 or saved(session.player_id,FIRST[0]):
                return accept_first(session,packet_id)
            rsp.status = pb.StatusCode_BAD_REQ
        else:
            rsp.status = accept(session,req,rsp)
        session.send(MsgId.AcceptQuestRsp,rsp,packet_id)
