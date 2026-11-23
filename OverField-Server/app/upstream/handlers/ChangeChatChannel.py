from network.msg_id import MsgId
from network.packet_handler import PacketHandler,packet_handler
from proto import net_pb2 as pb
from private_chat import switch


@packet_handler(MsgId.ChangeChatChannelReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        switch(session,pb.ChangeChatChannelReq.FromString(data),packet_id)
