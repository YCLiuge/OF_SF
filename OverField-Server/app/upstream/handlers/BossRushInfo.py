"""National client 2701. Season schedules use China Standard Time on every server."""
from datetime import datetime, timedelta, timezone
import time

from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res


CN = timezone(timedelta(hours=8))


def timestamp(txt):
    return int(datetime.strptime(txt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CN).timestamp())


def read_season(player_id, requested, now):
    tables = res["BossRush"]
    seasons = tables["boss_rush_season"]["datas"]
    if requested:
        season = next((s for s in seasons if s["i_d"] == requested), None)
    else:
        season = next((s for s in seasons if timestamp(s["start_time"]) <= now <= timestamp(s["end_time"])), None)
    if season is None:
        return pb.BossRushInfoRsp(status=pb.StatusCode_CFG_NOT_FOUND if requested else pb.StatusCode_OK)
    result = pb.BossRushInfoRsp(status=pb.StatusCode_OK)
    season_id = season["i_d"]
    info = result.info
    saved = db.get_boss_rush_info(player_id, season_id)
    if saved:
        info.ParseFromString(saved)
        if not info.challenge_end_time or now < info.challenge_end_time:
            return result
        info.challenge_end_time = 0
        info.current_stage_index = -1
        for stage in info.stage_infos:
            stage.best_score = max(stage.best_score, stage.total_score)
            stage.total_score = 0
            stage.total_damage_hp = 0
            stage.ClearField("teams")
        info.best_total_score = sum(stage.best_score for stage in info.stage_infos)
    else:
        info.season_id = season_id
        info.current_stage_index = -1
        info.start_time = timestamp(season["start_time"]) * 1000
        info.end_time = timestamp(season["end_time"]) * 1000
        info.show_rank_time = timestamp(season["show_rank_time"]) * 1000
        stages = next(row for row in tables["boss_rush_stage"]["datas"] if row["i_d"] == season["season_stage"])
        for index, _ in enumerate(stages["boss_rush_stage_group_info"]):
            info.stage_infos.add(stage_index=index)
    db.set_boss_rush_info(player_id, season_id, info.SerializeToString())
    return result


@packet_handler(MsgId.BossRushInfoReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.BossRushInfoReq.FromString(data)
        rsp = read_season(session.player_id, req.season_id, int(time.time()))
        session.send(MsgId.BossRushInfoRsp, rsp, packet_id)

