from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_dungeons import enter


@packet_handler(MsgId.DungeonRepeatEnterReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        selected = pb.DungeonRepeatEnterReq.FromString(data)
        rsp = pb.DungeonRepeatEnterRsp()
        if session.dungeon[0]:
            request = pb.DungeonEnterReq(dungeon_id=session.dungeon[0], char1=selected.char1,
                                         char2=selected.char2, char3=selected.char3)
            rsp.status = enter(session, request, rsp, restart=True)
        else:
            rsp.status = pb.StatusCode_PlayerNotInDungeon
        session.send(MsgId.DungeonRepeatEnterRsp, rsp, packet_id)
