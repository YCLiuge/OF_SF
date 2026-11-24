from private_request import set_after_commit
from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_crafts import craft


@packet_handler(MsgId.HandicraftReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.HandicraftReq.FromString(data)
        rsp = pb.HandicraftRsp()
        kind = pb.LIFE_TYPE_Handicraft
        receipt = getattr(session, "craft_receipt", None)
        request = (kind, packet_id, req.handicraft_id)
        if packet_id and receipt and receipt[0] == request:
            rsp.ParseFromString(receipt[1])
        else:
            rsp.status = craft(session, kind, req.handicraft_id, rsp)
            if packet_id and rsp.status == pb.StatusCode_OK:
                set_after_commit(session, "craft_receipt", (request, rsp.SerializeToString()))
        session.send(MsgId.HandicraftRsp, rsp, packet_id)
