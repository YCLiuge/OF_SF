from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res


@packet_handler(MsgId.WorldLevelAchieveListReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        pb.WorldLevelAchieveListReq.FromString(data)
        rsp = pb.WorldLevelAchieveListRsp(status=pb.StatusCode_OK)
        for level in res["Level"]["world_level"]["datas"]:
            for cfg in level.get("world_level_info", ()):
                aid = cfg.get("achieve_i_d")
                if aid:
                    entry = rsp.achieves.add(achieve_id=aid)
                    raw = db.get_achieve(session.player_id, aid)
                    if raw:
                        entry.ParseFromString(raw)
        session.send(MsgId.WorldLevelAchieveListRsp, rsp, packet_id)
