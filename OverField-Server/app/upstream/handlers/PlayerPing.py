from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto.net_pb2 import PlayerPingReq, PlayerPingRsp, StatusCode
import time


@packet_handler(MsgId.PlayerPingReq)
class Handler(PacketHandler):
    def handle(self, session, data: bytes, packet_id: int):
        req = PlayerPingReq()
        req.ParseFromString(data)

        rsp = PlayerPingRsp()
        rsp.status = StatusCode.StatusCode_OK
        rsp.client_time_ms = req.client_time_ms
        rsp.server_time_ms = int(time.time() * 1000)

        session.send(MsgId.PlayerPingRsp, rsp, packet_id)
        if getattr(session, "logged_in", False):
            from private_fireworks import update as fireworks
            fireworks(session)
            from private_mail import poll as check_mail
            check_mail(session)
            from private_bless_tasks import tick as prayer_tick
            prayer_tick(session)
            from private_field_gathering import tick as gather_tick
            gather_tick(session)
            if getattr(session,"fishing_cast",None):
                from private_fishing import tick as settle_cast
                settle_cast(session)
            from private_arcade_wallet import recover
            recover(session)
            if getattr(session,"mp_room_team",0):
                from private_mp_rooms import tick
                tick(session)
            from private_life_skills import refresh
            refresh(session)
        if hasattr(session, "event_calendar"):
            from private_events import refresh
            refresh(session)
