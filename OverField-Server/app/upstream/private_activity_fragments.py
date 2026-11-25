"""Settle the separate national moon-fragment notification for outdoor activities."""
import random

from network.msg_id import MsgId
from private_shop import UnsupportedGoods, award, fixed_gift
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res


FRAGMENT = 124
REWARD_POOL = 107
CHEST_SOURCE = 1120
GATHER_SOURCES = (1105, 1106, 1107, 1108, 1109, 1110)


def setup(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(private_boxes)")}
    if "moon_checked" not in columns:
        conn.execute("ALTER TABLE private_boxes ADD COLUMN moon_checked INTEGER NOT NULL DEFAULT 0")


def outdoor(session):
    row = res.index("Scene", "scene").get(session.scene_id, {})
    return not session.dungeon[0] and session.scene_id != 100 and row.get("new_scene_type") == 1


def grant(session, source):
    percent = session.realm_rules.activity_fragment_percent
    if percent == 0 or (percent < 100 and random.randrange(100) >= percent):
        return
    pid = session.player_id
    quantity = session.realm_rules.activity_fragment_quantity
    if quantity:
        amounts = {FRAGMENT: quantity}
    else:
        level = db.get_players_info(pid, "level")[0]
        amounts = fixed_gift(pid, REWARD_POOL, level)
    if not amounts or set(amounts) != {FRAGMENT}:
        return
    outcome = pb.ShopBuyRsp()
    pack = pb.PackNotice(status=pb.StatusCode_OK)
    try:
        award(pid, amounts, outcome, pack)
    except UnsupportedGoods:
        # A full legacy fragment stack must not roll back the primary activity.
        # This grant contains one non-instance item; award validates it before writing.
        return
    notice = pb.MoonQuestGamePlayRewardNotice(status=pb.StatusCode_OK, id=source)
    notice.rewards.extend(outcome.items)
    session.send(MsgId.MoonQuestGamePlayRewardNotice, notice, 0)
    session.send(MsgId.PackNotice, pack, 0)


def chest_opened(session, request):
    if not outdoor(session):
        return
    # The ordinary supply boxes refresh in place. Regeneration replaces their row
    # and clears this bit; opening the same chest twice never rolls a second time.
    changed = db.conn.execute(
        "UPDATE private_boxes SET moon_checked=1 WHERE player_id=? AND zone=? AND dynamic=? "
        "AND idx=? AND claimed=0 AND moon_checked=0 AND "
        "((dynamic=1 AND template=300000) OR (dynamic=0 AND refresh>0))",
        (session.player_id, session.scene_id, int(request.box_type), request.treasure_box_index),
    ).rowcount
    if changed:
        grant(session, CHEST_SOURCE)


def gathered(session, kind):
    if outdoor(session):
        grant(session, GATHER_SOURCES[kind])
