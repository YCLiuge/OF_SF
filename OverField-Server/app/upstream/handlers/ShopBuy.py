from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_shop import buy


@packet_handler(MsgId.ShopBuyReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        req=pb.ShopBuyReq.FromString(data)
        fields=(req.shop_id,req.grid_id,req.buy_times,req.item_id)
        latest=getattr(session,"last_shop_purchase",None)
        if packet_id and latest and latest[:2]==(packet_id,fields):
            reply=pb.ShopBuyRsp.FromString(latest[2])
        else:
            reply=pb.ShopBuyRsp()
            reply.status=buy(session,req,reply)
            if packet_id and reply.status==pb.StatusCode_OK:
                session.last_shop_purchase=(packet_id,fields,reply.SerializeToString())
        session.send(MsgId.ShopBuyRsp,reply,packet_id)
