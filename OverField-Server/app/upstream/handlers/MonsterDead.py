from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_dungeons import monster
from private_ground_loot import scope, pending
from private_request import cache_after_commit


@packet_handler(MsgId.MonsterDeadReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.MonsterDeadReq.FromString(data)
        rsp = pb.MonsterDeadRsp()
        key = (*scope(session),session.dungeon[0],packet_id,req.monster_id,req.monster_index)
        receipts = getattr(session,"monster_gift_receipts",{})
        if packet_id and key in receipts:
            rsp.status = pb.StatusCode_OK
            rsp.monster_index = req.monster_index
            gift = pending(key[:3],receipts[key])
            if gift is not None:
                rsp.drop_item.CopyFrom(gift)
        else:
            rsp.status = monster(session, req, rsp)
            if packet_id and rsp.HasField("drop_item"):
                cache_after_commit(session,"monster_gift_receipts",key,rsp.drop_item.index)
        session.send(MsgId.MonsterDeadRsp, rsp, packet_id)
