from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_plaza import forecast


@packet_handler(MsgId.GenericSceneBReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.GenericSceneBReq.FromString(data)
        rsp = pb.GenericSceneBRsp()
        rsp.status = forecast(session, req, rsp)
        session.send(MsgId.GenericSceneBRsp, rsp, packet_id)
