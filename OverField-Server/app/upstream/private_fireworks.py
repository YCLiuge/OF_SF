"""Schedule the native cherry-blossom fireworks for public plaza channels (UTC+8)."""
from datetime import datetime, timedelta

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils.res_loader import res
from private_channels import HOME, is_public
import private_events
import private_shop as shop
import realm_plaza
from realm_events import SAKURA


def window(instant,raw):
    """Use the operator's active plaza schedule, then the original event/theme dates."""
    plaza=private_events._decoded(raw).get("plaza")
    plaza_active=realm_plaza.active(plaza,instant)
    overrides=private_events._overrides(raw,plaza_active)
    activities=res.index("Activity","activity")
    for aid in SAKURA:
        entry=private_events.current(activities[aid],instant,overrides)
        if entry:
            return entry["opening_time"],entry["closing_time"]
    themes=res.index("Scene","scene_theme")
    if plaza_active:
        if "/樱花季/" in themes[plaza["theme"]]["asset_path"]:
            return plaza["opens_at"],plaza["closes_at"]
        return None
    for theme in themes.values():
        if "/樱花季/" in theme["asset_path"] and shop.in_window(theme,instant):
            return theme["opening_time"],theme["closing_time"]
    return None


def show(session,instant):
    if (session.scene_id!=HOME or session.dungeon[0] or getattr(session,"mp_room_team",0)
            or not is_public(session.realm_rules,session.channel_id)):
        return None
    hour=show_hour()
    if instant.hour!=hour or instant.minute>=10:
        return None
    return schedule(instant,private_events.raw_settings())


def show_hour():
    return int(res.index("GlobalConfig","config","key")["Fireworks_Party_StartRealTime"]["values"][0])


def schedule(instant,raw):
    """Read-only preview for an operator or a simulated realm clock."""
    hour=show_hour()
    if instant.hour!=hour or instant.minute>=10:
        return None
    period=window(instant,raw)
    if period is None:
        return None
    start=instant.replace(hour=hour,minute=0,second=0,microsecond=0)
    end=min(start+timedelta(minutes=10),datetime.fromisoformat(period[1]).replace(tzinfo=shop.CN)+timedelta(seconds=1))
    start=max(start,datetime.fromisoformat(period[0]).replace(tzinfo=shop.CN))
    return pb.FireworksInfo(fireworks_id=10000,fireworks_start_time=int(start.timestamp()),
                            fireworks_duration_time=int((end-start).total_seconds()))


def scene_seconds(session,normal,instant=None):
    active=getattr(session,"fireworks_show",None)
    if active and session.scene_id==HOME and session.channel_id==active[0]:
        elapsed=int((instant or shop.now()).timestamp())-active[1]
        if 0<=elapsed<active[2]:
            hour=int(res.index("GlobalConfig","config","key")["Fireworks_Party_StartGameTime"]["values"][0])
            return normal-normal%86400+hour*3600+elapsed*50
    return normal


def update(session,scene=None):
    instant=shop.now()
    entry=show(session,instant)
    current=(session.channel_id,entry.fireworks_start_time,entry.fireworks_duration_time) if entry else None
    previous=getattr(session,"fireworks_show",None)
    session.fireworks_show=current
    if scene is not None:
        if entry:
            scene.fireworks_info.CopyFrom(entry)
    elif current!=previous:
        from private_plaza import game_seconds
        clock=pb.ServerSceneSyncDataNotice(status=pb.StatusCode_OK)
        clock.data.add().server_data.add(action_type=pb.SceneActionType_TOD_UPDATE,
                                         tod_time=scene_seconds(session,game_seconds(),instant)%86400)
        session.send(MsgId.ServerSceneSyncDataNotice,clock,0)
        if entry:
            session.send(MsgId.FireworksStartNotice,pb.FireworksStartNotice(status=pb.StatusCode_OK,fireworks_info=entry),0)
