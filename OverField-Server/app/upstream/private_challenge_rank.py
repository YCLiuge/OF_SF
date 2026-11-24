"""Per-scene best challenge times for the player and their confirmed friends."""
from collections import defaultdict
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_PlayerBriefInfo


def friend_rank(session, req, rsp):
    player = session.player_id
    # Keep players without a score in the board. Load all challenge blobs for this
    # room's scene in one indexed query instead of querying each friend's score twice.
    rows = db.conn.execute("""
        WITH members(pid) AS (
            SELECT ? UNION SELECT f.friend_id FROM friend f JOIN friend r
            ON r.player_id=f.friend_id AND r.friend_id=f.player_id
            WHERE f.player_id=? AND f.friend_status=? AND r.friend_status=?)
        SELECT p.player_id, c.challenge_blob FROM members m JOIN players p ON p.player_id=m.pid
        LEFT JOIN challenges c ON c.player_id=m.pid AND c.scene_id=?
        ORDER BY p.player_id,c.challenge_id""",
        (player, player, pb.FriendStatus_FRIEND, pb.FriendStatus_FRIEND, session.scene_id))
    scores = defaultdict(list)
    for who, raw in rows:
        board = scores[who]
        if raw:
            data = pb.ChallengeData.FromString(raw)
            if data.use_time and data.state == pb.Finish:
                board.append(pb.PlayerChallengeInfo(challenge_id=data.challenge_id, use_time=data.use_time))
    rsp.self_challenge.player_id = player
    rsp.self_challenge.challenge_infos.extend(scores.pop(player, ()))
    for who, records in sorted(scores.items(), key=lambda it: (
            not it[1], sum(value.use_time for value in it[1]), it[0])):
        row = rsp.rank_info.add()
        make_PlayerBriefInfo(who, row.player_info)
        row.challenge.player_id = who
        row.challenge.challenge_infos.extend(records)
    return pb.StatusCode_OK
