from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_crafts import craft


@packet_handler(MsgId.MakePropReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.MakePropReq.FromString(data)
        rsp = pb.MakePropRsp()
        kind = pb.LIFE_TYPE_Making
        receipt = getattr(session, "craft_receipt", None)
        request = (kind, packet_id, req.make_id)
        if packet_id and receipt and receipt[0] == request:
            rsp.ParseFromString(receipt[1])
        else:
            rsp.status = craft(session, kind, req.make_id, rsp)
            if packet_id and rsp.status == pb.StatusCode_OK:
                session.craft_receipt = (request, rsp.SerializeToString())
        session.send(MsgId.MakePropRsp, rsp, packet_id)
