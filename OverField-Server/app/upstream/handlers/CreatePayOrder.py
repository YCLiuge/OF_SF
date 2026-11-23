"""A private shop click awards local inventory. Never open an SDK checkout or issue a merchant order."""
from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_gifts import claim, request_key


@packet_handler(MsgId.CreatePayOrderReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.CreatePayOrderReq.FromString(data)
        if req.to_player_id and req.to_player_id!=session.player_id:
            session.send(MsgId.CreatePayOrderRsp,pb.CreatePayOrderRsp(status=pb.StatusCode_BAD_REQ),packet_id)
            return
        status, delivery, pack, extra = claim(session.player_id,req.shop_id,req.grid_id,
                                             request_key(session,packet_id),pool=req.pool_id)
        # The retail SDK starts a real checkout after a successful order. This private benefit has no order.
        session.send(MsgId.CreatePayOrderRsp,pb.CreatePayOrderRsp(
            status=pb.StatusCode_Not_Payment if status==pb.StatusCode_OK else status),packet_id)
        if status==pb.StatusCode_OK:
            if pack.items:
                session.send(MsgId.PackNotice,pack,0)
            for msg, notice in extra:
                session.send(msg,notice,0)
            session.send(MsgId.PaySendGoodsNotice,delivery,0)
