from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_gacha import history


@packet_handler(MsgId.GachaRecordReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GachaRecordReq.FromString(data)
        rsp = pb.GachaRecordRsp()
        rsp.status = history(session, req, rsp)
        session.send(MsgId.GachaRecordRsp, rsp, packet_id)
