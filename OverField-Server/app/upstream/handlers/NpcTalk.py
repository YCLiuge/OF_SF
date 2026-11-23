from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId

from proto.net_pb2 import NpcTalkReq, NpcTalkRsp, StatusCode
from utils.res_loader import res
from utils.pb_create import make_QuestNotice


@packet_handler(MsgId.NpcTalkReq)
class Handler(PacketHandler):
    def handle(self, session, data: bytes, packet_id: int):
        req = NpcTalkReq()
        req.ParseFromString(data)
        if req.id == 11000311:
            from private_badge_tree import starter_talk
            return starter_talk(session, req, packet_id)

        from private_story_entry import CONDITIONS as ENTRY_CONDITIONS, MECHANISMS, mechanism
        if req.id in MECHANISMS or req.id in ENTRY_CONDITIONS:
            if req.talk_type == NpcTalkReq.NpcTalkType_Mechanism and req.id in MECHANISMS:
                return mechanism(session, MECHANISMS[req.id], packet_id)
            session.send(MsgId.NpcTalkRsp, NpcTalkRsp(status=StatusCode.StatusCode_BAD_REQ), packet_id)
            return
        from private_first_quest import CONDITIONS, talk
        if req.id in CONDITIONS:
            if req.talk_type != NpcTalkReq.NpcTalkType_Npc:
                session.send(MsgId.NpcTalkRsp, NpcTalkRsp(status=StatusCode.StatusCode_BAD_REQ), packet_id)
                return
            return talk(session, req.id, packet_id)

        rsp = NpcTalkRsp()
        rsp.status = StatusCode.StatusCode_OK
        conds = []
        if req.talk_type == NpcTalkReq.NpcTalkType_Mechanism:
            for i in res["Achieve"]["achieve"]["datas"]:
                if req.id in i.get("param", []):
                    conds.append(i["i_d"])
        else:
            conds.append(req.id)
        rsp1 = make_QuestNotice(session, [c for c in conds if c not in CONDITIONS])
        if rsp1:
            session.send(MsgId.QuestNotice, rsp1, 0)
        session.send(MsgId.NpcTalkRsp, rsp, packet_id)
