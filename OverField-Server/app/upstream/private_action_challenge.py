"""Three-digit, realm-local action challenges issued to an authenticated online role."""
from secrets import randbelow
import time

from network.msg_id import MsgId
from private_request import set_after_commit
from proto import net_pb2 as pb
from utils import db


def notify(session, code):
    session.send(MsgId.FreezePlayerActionNotice,
                 pb.FreezePlayerActionNotice(status=pb.StatusCode_OK, verification_code=code), 0)


def renew(session, old_code=0, *, issuer=None):
    code = 100 + randbelow(900)
    if code == old_code:
        code = 100 + (code - 99) % 900
    expires = int(time.time()) + 300
    db.conn.execute("INSERT OR REPLACE INTO private_action_challenges VALUES(?,?,?,0)",
                    (session.player_id, code, expires))
    (issuer or session).commit_callbacks.append((setattr, (session, "action_challenge", True)))
    notify(session, code)


def restore(session):
    row = db.conn.execute("SELECT code, expires FROM private_action_challenges WHERE player=?",
                          (session.player_id,)).fetchone()
    if not row:
        return
    if row[1] <= time.time():
        renew(session, row[0])
    else:
        set_after_commit(session, "action_challenge", True)
        notify(session, row[0])


def release(session):
    db.conn.execute("DELETE FROM private_action_challenges WHERE player=?", (session.player_id,))
    set_after_commit(session, "action_challenge", False)


def verify(session, req, rsp):
    pid = session.player_id
    row = db.conn.execute("SELECT code, expires, attempts FROM private_action_challenges WHERE player=?",
                          (pid,)).fetchone()
    if not row:
        return pb.StatusCode_BAD_REQ
    code, expires, attempts = row
    if expires <= time.time() or (req.verification_code != code and attempts >= 4):
        renew(session, code)
        return pb.StatusCode_PLAYER_OPERATION_LOCKED
    if req.verification_code != code:
        db.conn.execute("UPDATE private_action_challenges SET attempts=attempts+1 WHERE player=?", (pid,))
        return pb.StatusCode_PLAYER_OPERATION_LOCKED
    release(session)
    return pb.StatusCode_OK
