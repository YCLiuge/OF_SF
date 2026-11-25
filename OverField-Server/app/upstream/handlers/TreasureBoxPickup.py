from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from private_boxes import pickup
from private_encounter_gifts import recorded_chest
from private_request import cached_mutation
from proto import net_pb2 as pb


@packet_handler(MsgId.TreasureBoxPickupReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        req=pb.TreasureBoxPickupReq.FromString(data)
        if (req.box_type==pb.TREASURE_BOX_TYPE_DYNAMIC and not session.dungeon[0] and
                recorded_chest(session,req.box_index)):
            scope=(session.scene_id,session.channel_id)
            return cached_mutation(session,"TreasureBoxPickup",data,packet_id,pickup,context=scope)
        rsp=pb.TreasureBoxPickupRsp()
        rsp.status=pickup(session,req,rsp)
        session.send(MsgId.TreasureBoxPickupRsp,rsp,packet_id)
