from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_initial_queries import scene_progress


@packet_handler(MsgId.SceneProcessListReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.SceneProcessListReq.FromString(data)
        rsp = pb.SceneProcessListRsp()
        rsp.status = scene_progress(session, req, rsp)
        session.send(MsgId.SceneProcessListRsp, rsp, packet_id)
