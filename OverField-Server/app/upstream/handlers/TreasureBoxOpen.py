from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_boxes import open_box


@packet_handler(MsgId.TreasureBoxOpenReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.TreasureBoxOpenReq.FromString(data)
        rsp = pb.TreasureBoxOpenRsp()
        rsp.status = open_box(session, req, rsp)
        session.send(MsgId.TreasureBoxOpenRsp, rsp, packet_id)
        if rsp.status == pb.StatusCode_OK and rsp.items:
            from private_activity_fragments import chest_opened
            chest_opened(session, req)
