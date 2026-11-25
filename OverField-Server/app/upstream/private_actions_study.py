"""Teach and learn published plaza animations using native ownership conditions."""
import time

from network.msg_id import MsgId
from private_request import set_after_commit
from private_social_profile import config
from private_vehicle_seats import available, world
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res

# The public community server uses 24 hours. The PC table gives no labelled unit for the learned duration.
LEARNED_MS = 24 * 60 * 60 * 1000
_index = None


def now_ms():
    return int(time.time() * 1000)


def setup(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS private_studied_actions(
        player INTEGER NOT NULL, clip INTEGER NOT NULL, expires INTEGER NOT NULL,
        PRIMARY KEY(player,clip)) WITHOUT ROWID""")
    # Before starter ownership was configurable every provisioned role permanently owned these actions.
    conn.execute("CREATE TABLE IF NOT EXISTS private_study_migrations(version INTEGER PRIMARY KEY)")
    if conn.execute("INSERT OR IGNORE INTO private_study_migrations VALUES(1)").rowcount:
        from private_entitlements import VERSION
        conn.execute("""INSERT OR IGNORE INTO claimed_rewards(player_id,system,key)
            SELECT r.player_id,r.system,'study-all-v1' FROM claimed_rewards r
            WHERE r.system='private_entitlements' AND r.key=? AND NOT EXISTS (
                SELECT 1 FROM claimed_rewards c WHERE c.player_id=r.player_id AND c.system=r.system AND c.key='study-manual-v1')""",(VERSION,))


def catalog():
    global _index
    rows = res.index("Expression", "expression")
    if _index is None or _index[0] is not rows:
        clips, menus, conditions = {}, {}, {}
        for menu, row in rows.items():
            for group in row["expression_group_info"]:
                if group.get("is_study"):
                    clip, cond = group["action_i_d"], group["achieve_condition_i_d"]
                    clips.setdefault(clip, set()).add(cond)
                    menus[menu] = clip
                    conditions[menu] = cond
        _index = (rows, clips, menus, conditions)
    return _index[1:]


def permanently_owned(session, condition_ids):
    if getattr(session,"full_study_actions",False):
        return True
    player = session.player_id
    marks = ",".join("?" for _ in condition_ids)
    rows = db.conn.execute(f"SELECT achieve_blob FROM achieve WHERE player_id=? AND achieve_id IN ({marks})",
                           (player, *condition_ids))
    if any(pb.Achieve.FromString(raw).count >= 1 for raw, in rows):
        return True
    # A shop purchase may already have delivered the corresponding manual before the achievement refresh.
    achievements = res.index("Achieve", "achieve")
    item_ids = {achievements[key]["param"][0] for key in condition_ids}
    marks = ",".join("?" for _ in item_ids)
    rows = db.conn.execute(f"SELECT item_detail_blob FROM items WHERE player_id=? AND item_id IN ({marks})",
                           (player, *item_ids))
    return any(pb.ItemDetail.FromString(raw).main_item.base_item.num > 0 for raw, in rows)


def unlocks(session, notice):
    _, menus, conditions = catalog()
    full = db.conn.execute("""SELECT 1 FROM claimed_rewards WHERE player_id=?
                           AND system='private_entitlements' AND key=?""", (session.player_id, "study-all-v1")).fetchone()
    session.full_study_actions = bool(full)
    if full:
        notice.action_id_lst.extend(sorted(session.entitlements.menu_actions))
    else:
        ids = tuple(set(conditions.values()))
        rows = db.conn.execute("SELECT achieve_id,achieve_blob FROM achieve WHERE player_id=? AND achieve_id IN (" +
                               ",".join("?" for _ in ids) + ")", (session.player_id,*ids))
        permanent = {cond for cond,raw in rows if pb.Achieve.FromString(raw).count >= 1}
        # Include shop-bought manuals for which the achievement is not refreshed yet.
        achievements = res.index("Achieve", "achieve")
        by_item = {achievements[cond]["param"][0]:cond for cond in conditions.values()}
        rows = db.conn.execute("SELECT item_id,item_detail_blob FROM items WHERE player_id=? AND item_id IN (" +
                               ",".join("?" for _ in by_item) + ")", (session.player_id, *by_item))
        for item, raw in rows:
            if pb.ItemDetail.FromString(raw).main_item.base_item.num > 0:
                permanent.add(by_item[item])
        notice.action_id_lst.extend(sorted(m for m in session.entitlements.menu_actions
                                           if m not in menus or conditions[m] in permanent))
    stamp = now_ms()
    db.conn.execute("DELETE FROM private_studied_actions WHERE player=? AND expires<=?", (session.player_id,stamp))
    for clip, expires in db.conn.execute("SELECT clip,expires FROM private_studied_actions WHERE player=?", (session.player_id,)):
        if clip in catalog()[0] and not permanently_owned(session, catalog()[0][clip]):
            notice.study_action[clip] = expires


def in_reach(a, b):
    # The scene protocol uses centimetres for player poses, including old saves without decimal_places.
    a, b = a.scene_player.team.char1.pos, b.scene_player.team.char1.pos
    return sum((getattr(a,k)-getattr(b,k))**2 for k in ("x","y","z")) <= (100*config("ExpressionLearningDistance"))**2


def perform(session, action, notice):
    clips, menus, _ = catalog()
    clip = action if action in clips else menus.get(action)
    if not clip:
        set_after_commit(session, "study_offer", None)
        return pb.StatusCode_OK
    stamp = now_ms()
    if not permanently_owned(session, clips[clip]):
        row = db.conn.execute("SELECT expires FROM private_studied_actions WHERE player=? AND clip=?",
                              (session.player_id,clip)).fetchone()
        if not row or row[0] <= stamp:
            return pb.StatusCode_ACTION_NOT_UNLOCK
        notice.is_study = True
        set_after_commit(session, "study_offer", None)
        return pb.StatusCode_OK
    if available(session) and session.scene_membership == world(session):
        expiry = stamp + 1000 * config("ExpressionLearningTimer")
        set_after_commit(session, "study_offer", (world(session), clip, expiry))
        offer = pb.SendActionStudyNotice(status=pb.StatusCode_OK, action_id=clip, from_player_id=session.player_id,
                                         from_player_name=session.player_name, end_time=expiry)
        for peer in session.social_rooms.get(world(session), ()):
            if peer is not session and available(peer) and world(peer)==world(session) and in_reach(session,peer):
                peer.send(MsgId.SendActionStudyNotice, offer, 0)
    else:
        set_after_commit(session, "study_offer", None)
    return pb.StatusCode_OK


def learn(session, req, rsp):
    clips, _, _ = catalog()
    if req.action_id not in clips:
        return pb.StatusCode_CFG_NOT_FOUND
    from server.scene_data import online_session
    peer = online_session(req.player_id)
    stamp = now_ms()
    offer = getattr(peer,"study_offer",None)
    if (peer is None or peer is session or not available(peer) or not available(session) or world(peer)!=world(session)
            or not offer or offer[:2]!=(world(session),req.action_id) or offer[2]<=stamp or not in_reach(session,peer)):
        return pb.StatusCode_ACTION_STUDY_TIME_OUT
    if not permanently_owned(peer, clips[req.action_id]):
        return pb.StatusCode_ACTION_NOT_UNLOCK
    already = db.conn.execute("SELECT expires FROM private_studied_actions WHERE player=? AND clip=?",
                              (session.player_id,req.action_id)).fetchone()
    if permanently_owned(session, clips[req.action_id]) or already and already[0]>stamp:
        return pb.StatusCode_HasUnlocked
    expires = stamp + LEARNED_MS
    db.conn.execute("""INSERT INTO private_studied_actions VALUES(?,?,?) ON CONFLICT(player,clip)
                    DO UPDATE SET expires=excluded.expires""", (session.player_id,req.action_id,expires))
    learned = pb.ActionStudyNotice(status=pb.StatusCode_OK, player_id=session.player_id,
                                   name=session.player_name,action_id=req.action_id)
    for viewer in session.social_rooms.get(world(session), ()):
        if available(viewer) and world(viewer)==world(session):
            viewer.send(MsgId.ActionStudyNotice, learned, 0)
    unlock = pb.AchieveActionUnLockNotice(status=pb.StatusCode_OK)
    unlock.study_action[req.action_id] = expires
    session.send(MsgId.AchieveActionUnLockNotice, unlock, 0)
    return pb.StatusCode_OK
