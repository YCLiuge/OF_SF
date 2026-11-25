from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_ground_loot import pickup, scope
from private_request import cache_after_commit
from proto import net_pb2 as pb


@packet_handler(MsgId.PickupReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        req = pb.PickupReq.FromString(data)
        key = (*scope(session),session.dungeon[0],packet_id,req.drop_item_index,req.pick_index)
        receipts = getattr(session,"ground_pickup_receipts",{})
        rsp = pb.PickupRsp()
        if packet_id and key in receipts:
            rsp.ParseFromString(receipts[key])
        else:
            rsp.status = pickup(session,req,rsp)
            if packet_id and rsp.status == pb.StatusCode_OK:
                cache_after_commit(session,"ground_pickup_receipts",key,rsp.SerializeToString())
        session.send(MsgId.PickupRsp,rsp,packet_id)
