"""Player-home likes, native daily budget and the profile fields visible in the visitor history."""
from private_request import cache_after_commit
from datetime import timedelta

from network.msg_id import MsgId
from private_channels import HOME, owner_for
from utils.res_loader import res
import private_shop as shop
from proto import net_pb2 as pb
from utils import db
from private_bless_tasks import record as prayer


def day_key():
    hours = int(res.index("GlobalConfig", "config", "key")["SystemRefreshTime"]["values"][0])
    return (shop.now().astimezone(shop.CN) - timedelta(hours=hours)).date().isoformat()


def remaining(session, day):
    from private_life_skills import garden_like_bonus
    player = session.player_id
    cap = int(res.index("GlobalConfig", "config", "key")["HomeLikesNum"]["values"][0])
    cap += garden_like_bonus(session)
    used = db.conn.execute("SELECT count FROM realm_like_daily WHERE player=? AND day=?", (player,day)).fetchone()
    return max(0, cap - (used[0] if used else 0))


def visible_home(session):
    if session.scene_id == HOME and not session.dungeon[0]:
        owner = owner_for(session.channel_id)
        if owner and (owner == session.player_id or db.get_garden_info(owner)[4]):
            return owner
    return None


def records(session, req, rsp):
    owner = visible_home(session)
    if owner is None:
        return pb.StatusCode_BAD_REQ
    sql = """SELECT p.player_id, p.player_name, p.head, p.sign, p.hide_value, p.team_leader_badge, h.count
             FROM realm_home_likes h INNER JOIN players p ON p.player_id=h.visitor
             WHERE h.owner=? ORDER BY h.count DESC, p.player_id"""
    for pid,name,head,sign,hidden,badge,count in db.conn.execute(sql,(owner,)):
        rsp.record.add(player_id=pid, name=name or "", head=head, like_num=count,
                       sign="" if hidden & pb.HideType_SIGN_Type else (sign or ""),team_leader_badge=badge)
    return pb.StatusCode_OK


def like(session, packet):
    player = session.player_id
    owner = visible_home(session)
    rsp = pb.DoLikesRsp()
    if not owner or owner == player:
        rsp.status = pb.StatusCode_BAD_REQ
        return rsp
    receipts = getattr(session, "garden_like_receipts", None)
    key = owner,packet
    if packet and receipts is not None and key in receipts:
        return pb.DoLikesRsp.FromString(receipts[key])
    day = day_key()
    left = remaining(session,day)
    rsp.like_nums = db.get_garden_info(owner)[0]
    if not left:
        rsp.status = pb.StatusCode_TIPS_LIKES_NUM_LIMIT
        return rsp

    db.conn.execute("""INSERT INTO realm_like_daily(player,day,count) VALUES(?,?,1)
        ON CONFLICT(player) DO UPDATE SET count=CASE WHEN day=excluded.day THEN count+1 ELSE 1 END,day=excluded.day""",
        (player,day))
    db.conn.execute("""INSERT INTO realm_home_likes(owner,visitor,count) VALUES(?,?,1)
        ON CONFLICT(owner,visitor) DO UPDATE SET count=count+1""", (owner,player))
    db.conn.execute("""INSERT INTO garden_info(player_id,like_num) VALUES(?,1)
        ON CONFLICT(player_id) DO UPDATE SET like_num=like_num+1""", (owner,))
    rsp.status, rsp.left_like_nums = pb.StatusCode_OK, left-1
    rsp.like_nums += 1
    db.conn.execute("UPDATE players SET garden_like_num=? WHERE player_id=?", (rsp.like_nums,owner))
    # Active quest 522003 uses condition 15220031, authored as type 176 (like another garden).
    prayer(session,176)
    if packet:
        cache_after_commit(session, "garden_like_receipts", key, rsp.SerializeToString())
    return rsp
