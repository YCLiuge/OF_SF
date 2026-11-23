from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from utils import db


@packet_handler(MsgId.SwitchGardenStatusReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        r=pb.SwitchGardenStatusReq.FromString(data)
        previous=db.get_garden_info(session.player_id)[4]
        rsp=pb.SwitchGardenStatusRsp(status=pb.StatusCode_BAD_REQ,is_open=previous)
        if not r.password:
            db.conn.execute("UPDATE garden_info SET is_open=? WHERE player_id=?",(int(r.is_open),session.player_id))
            rsp.status=pb.StatusCode_OK;rsp.is_open=r.is_open
        session.send(MsgId.SwitchGardenStatusRsp,rsp,packet_id)

