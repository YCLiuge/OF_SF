"""Bind private gameplay operations to their native request and response messages."""
from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb


def handler(name, update):
    request=getattr(pb,name+"Req")
    response=getattr(pb,name+"Rsp")
    reply_id=getattr(MsgId,name+"Rsp")

    @packet_handler(getattr(MsgId,name+"Req"))
    class Handler(PacketHandler):
        def handle(self,session,data,packet_id):
            result=response()
            result.status=update(session,request.FromString(data),result)
            session.send(reply_id,result,packet_id)
    return Handler
