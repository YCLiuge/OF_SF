"""Timed national field challenges. The persisted result is the player's personal best."""
from private_dungeons import _table
from private_request import handler, set_after_commit
from proto import net_pb2 as pb
from utils import db


def update(session, req, rsp):
    row = _table("Challenge", "challenge").get(req.challenge_id)
    if not row or not row.get("scene_i_d"):
        return pb.StatusCode_CFG_NOT_FOUND
    if row["scene_i_d"] != session.scene_id or session.dungeon[0]:
        return pb.StatusCode_BAD_REQ

    key = (session.scene_id, session.channel_id, req.challenge_id)
    active = getattr(session, "active_challenge", None)
    state = req.battle_state
    if state == pb.Start:
        if active and active != key:
            return pb.StatusCode_OTHER_CHALLENGE_STARTED
        set_after_commit(session, "active_challenge", key)
    elif state in (pb.Finish, pb.Fail, pb.Close, pb.Reset):
        if active != key:
            return pb.StatusCode_ChallengeNotStart
        if state == pb.Finish and not req.use_time:
            return pb.StatusCode_BAD_REQ
        set_after_commit(session, "active_challenge", None)
    else:
        return pb.StatusCode_BAD_REQ

    result = rsp.challenge_data
    result.challenge_id, result.state = req.challenge_id, state
    if state == pb.Finish:
        # SuccessCallback sends integer milliseconds. ChallengeModel compares its clock
        # with the three thresholds in the asset multiplied by 1000, inclusively.
        result.use_time = req.use_time
        result.star = sum(req.use_time <= 1000 * row[f"star_timer{level}"] for level in (1, 2, 3))
        raw = db.get_challenge(session.player_id, session.scene_id, req.challenge_id)
        best = pb.ChallengeData.FromString(raw) if raw else None
        if not best or best.state != pb.Finish or not best.use_time or result.use_time < best.use_time:
            db.set_challenge(session.player_id, session.scene_id, req.challenge_id, result.SerializeToString())
        # These 111 rows specify a treasure box but contain no box or loot-pool ID.
        # Do not mint generic currency or create unrelated dynamic boxes.
    return pb.StatusCode_OK


Handler = handler("ChallengeStateUpdate", update, replay=True)
