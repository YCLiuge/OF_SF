from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from server.scene_data import up_scene_action
from utils import db
from utils.pb_create import make_SceneTeam


@packet_handler(MsgId.UpdateTeamReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        req = pb.UpdateTeamReq.FromString(data)
        slots = (req.char1, req.char2, req.char3)
        members = [cid for cid in slots if cid]
        rsp = pb.UpdateTeamRsp(status=pb.StatusCode_BAD_REQ)
        if session.dungeon[0] or not req.char1 or len(set(members)) != len(members):
            session.send(MsgId.UpdateTeamRsp, rsp, packet_id)
            return
        pid = session.player_id
        if any(not db.get_characters(pid, cid) for cid in members):
            rsp.status = pb.StatusCode_CharNotExist
            session.send(MsgId.UpdateTeamRsp, rsp, packet_id)
            return

        from private_garden_residents import find as placed
        if placed(pid,req.char1):
            rsp.status=pb.StatusCode_CHARACTER_PLACED
            session.send(MsgId.UpdateTeamRsp,rsp,packet_id)
            return

        # Build a fresh team so empty slots disappear. Keep the party at its current scene position.
        team = make_SceneTeam(pid, slots)
        previous_leader = session.scene_player.team.char1
        old_leader = previous_leader.char_id
        for member in (team.char1, team.char2, team.char3):
            if member.char_id:
                member.pos.CopyFrom(previous_leader.pos)
                member.rot.CopyFrom(previous_leader.rot)
        db.set_players_info(pid, "team", slots)
        db.set_players_info(pid, "character_id", req.char1)
        db.set_players_info(pid, "team_leader_badge", team.char1.character_appearance.badge)
        session.scene_player.team.CopyFrom(team)
        if session.fashion_rider:
            from private_chairs import refresh_fashion
            session.commit_callbacks.append((refresh_fashion,(session,)))
        if session.vehicle_riders:
            from private_vehicle_seats import refresh_equipment
            session.commit_callbacks.append((refresh_equipment, (session,)))
        if old_leader and old_leader != req.char1:
            from private_newcomer import record
            record(session, 128)

        rsp.status = pb.StatusCode_OK
        session.send(MsgId.UpdateTeamRsp, rsp, packet_id)
        notice = pb.ServerSceneSyncDataNotice(status=pb.StatusCode_OK)
        entry = notice.data.add(player_id=pid).server_data.add(action_type=pb.SceneActionType_UPDATE_TEAM)
        entry.player.CopyFrom(session.scene_player)
        up_scene_action(session.scene_id, session.channel_id, notice)
