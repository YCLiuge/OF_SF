from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_inventory import store


@packet_handler(MsgId.TempPackItemStoreReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.TempPackItemStoreReq.FromString(data)
        rsp = pb.TempPackItemStoreRsp()
        rsp.status = store(session, req, rsp)
        session.send(MsgId.TempPackItemStoreRsp, rsp, packet_id)

