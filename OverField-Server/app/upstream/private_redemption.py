"""Settle a realm-issued code and its per-player receipt in the game database transaction."""
import json

from network.msg_id import MsgId
from private_activity_rewards import deliver_once
from private_dungeons import credit_player_experience
from private_gameplay_transaction import run
from private_shop import now
from proto import net_pb2 as pb
from realm_codes import SCHEMA, normalize
from utils import db


def setup():
    db.conn.execute("CREATE TABLE IF NOT EXISTS authority.realm_reward_codes "+SCHEMA)
    db.conn.execute("CREATE TABLE IF NOT EXISTS pc_redeem_totals(code TEXT PRIMARY KEY, uses INTEGER NOT NULL) WITHOUT ROWID")


def claim(session, req, rsp, pending):
    try:
        code = normalize(req.code)
    except ValueError:
        return pb.StatusCode_REDEMPTION_CODE_NOT_EXIST
    row = db.conn.execute("SELECT quota,expiry,rewards FROM authority.realm_reward_codes WHERE code=? AND enabled=1",
                          (code,)).fetchone()
    if not row or row[1] and row[1] < now().strftime("%Y-%m-%d %H:%M:%S"):
        return pb.StatusCode_REDEMPTION_CODE_NOT_EXIST
    pid = session.player_id
    if db.conn.execute("SELECT 1 FROM claimed_rewards WHERE player_id=? AND system='reward_code' AND key=?",
                       (pid,code)).fetchone():
        return pb.StatusCode_REDEMPTION_CODE_USED
    used = db.conn.execute("SELECT uses FROM pc_redeem_totals WHERE code=?", (code,)).fetchone()
    if row[0] and used and used[0]>=row[0]:
        return pb.StatusCode_REDEMPTION_CODE_NUM_IS_MAX
    amounts = {int(k):v for k,v in json.loads(row[2]).items()}
    status = deliver_once(session,"reward_code",code,amounts,rsp.rewards)
    if status != pb.StatusCode_OK:
        return status
    if 1 in amounts:
        pending.notices.append((MsgId.PlayerLevelExpNotice,credit_player_experience(pid,amounts[1])))
    db.conn.execute("""INSERT INTO pc_redeem_totals VALUES(?,1)
                    ON CONFLICT(code) DO UPDATE SET uses=uses+1""",(code,))
    rsp.code=req.code
    return pb.StatusCode_OK


def redeem(session,req,rsp):
    return run(session,req,rsp,claim)
