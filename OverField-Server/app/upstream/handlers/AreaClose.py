from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_bottle_intro import set_device
from private_story_entry import SAKURA, set_device as set_sakura_device


@packet_handler(MsgId.AreaCloseReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.AreaCloseReq.FromString(data)
        rsp = pb.AreaCloseRsp()
        if session.scene_id == SAKURA:
            rsp.status = set_sakura_device(session, req.area_id, pb.AreaData.Close, rsp)
        else:
            rsp.status = set_device(session, req.area_id, pb.AreaData.Close, rsp)
        session.send(MsgId.AreaCloseRsp, rsp, packet_id)
