from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_plaza import play_note


@packet_handler(MsgId.PlayMusicNoteReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.PlayMusicNoteReq.FromString(data)
        rsp = pb.PlayMusicNoteRsp()
        rsp.status = play_note(session, req, rsp)
        session.send(MsgId.PlayMusicNoteRsp, rsp, packet_id)
