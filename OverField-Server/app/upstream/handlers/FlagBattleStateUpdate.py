from network.packet_handler import packet_handler, PacketHandler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_flag_battle import update


@packet_handler(MsgId.FlagBattleStateUpdateReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.FlagBattleStateUpdateReq.FromString(data)
        rsp = pb.FlagBattleStateUpdateRsp()
        rsp.status = update(session,req,rsp,packet_id)
        session.send(MsgId.FlagBattleStateUpdateRsp,rsp,packet_id)
