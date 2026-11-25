"""Realm-only secondary PIN management for the retail SPOperate protocol."""
from dataclasses import dataclass
from hashlib import blake2s
from hmac import compare_digest
import re
import secrets
from threading import BoundedSemaphore
import time

from private_request import cache_after_commit, set_after_commit
from proto import net_pb2 as pb
from realm_accounts import derive, SALT_SIZE
from utils import db, res_loader

# Local anti-guessing rule; the retail table supplies the PIN length, not a documented lockout unit.
MAX_ERRORS = 5
COOLDOWN_SECONDS = 60
WORKERS = BoundedSemaphore(2)


def setup(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS private_secondary_password (
        player_id INTEGER PRIMARY KEY REFERENCES players(player_id) ON DELETE CASCADE,
        salt BLOB NOT NULL, verifier BLOB NOT NULL, failures INTEGER NOT NULL DEFAULT 0,
        blocked_until INTEGER NOT NULL DEFAULT 0)""")


def state(player):
    return db.conn.execute("SELECT salt,verifier,failures,blocked_until FROM private_secondary_password WHERE player_id=?",
                           (player,)).fetchone()


def flags(session, reply, row, verified):
    reply.sp_open_status = row is not None
    reply.sp_check_status = row is not None and verified == row[0]


def snapshot(session, reply):
    flags(session, reply, state(session.player_id), getattr(session, "secondary_verified", None))


@dataclass
class Operation:
    kind: int
    receipt: tuple
    before: object
    status: int = pb.StatusCode_BAD_REQ
    wrong: bool = False
    replacement: tuple = ()
    replay: bytes = b""


def prepare(host_lock, session, data, packet_id):
    """Read current revision briefly, then run the bounded password KDF outside the realm lock."""
    req = pb.SPOperateReq.FromString(data)
    kind = req.operate_type
    if not hasattr(session, "secondary_receipt_key"):
        session.secondary_receipt_key = secrets.token_bytes(32)
    receipt = (packet_id, blake2s(data, key=session.secondary_receipt_key).digest())
    with host_lock:
        row = state(session.player_id)
        op = Operation(kind, receipt, row)
        receipts = getattr(session, "secondary_receipts", {})
        if packet_id and receipt in receipts:
            op.replay = receipts[receipt]
            return op
    opening, changing, checking, closing = (pb.SPOperateType_Open, pb.SPOperateType_Change,
                                           pb.SPOperateType_Check, pb.SPOperateType_Close)
    if kind not in (opening, changing, checking, closing):
        return op
    required = int(next(r["values"][0] for r in res_loader.res["GlobalConfig"]["config"]["datas"]
                        if r["key"] == "SecondaryVerificationPasswordLength"))
    pin = req.password
    old = req.old_password if kind == changing else pin
    if len(pin) != required or not re.fullmatch("[0-9]+", pin):
        return op
    if kind == changing and (len(old) != required or not re.fullmatch("[0-9]+", old)):
        return op
    op.status = pb.StatusCode_FAIL
    if ((kind == opening) != (row is None)) or (row and row[3] > time.time()):
        return op
    if not WORKERS.acquire(timeout=2):
        return op
    try:
        if row is not None and not compare_digest(derive(old.encode("ascii"), row[0]), row[1]):
            op.wrong = True
            return op
        if kind in (opening, changing):
            salt = secrets.token_bytes(SALT_SIZE)
            op.replacement = (salt, derive(pin.encode("ascii"), salt))
        op.status = pb.StatusCode_OK
        return op
    finally:
        WORKERS.release()


def apply(session, op, reply):
    if op.replay:
        reply.ParseFromString(op.replay)
        snapshot(session, reply)  # An older receipt cannot restore verification after a later change.
        return
    row = state(session.player_id)
    verified = getattr(session, "secondary_verified", None)
    reply.operate_type = op.kind
    reply.status = op.status
    counted_wrong = op.wrong and row is not None and row == op.before
    if row != op.before:
        reply.status = pb.StatusCode_FAIL
    elif counted_wrong:
        fails = row[2] + 1
        until = int(time.time()) + COOLDOWN_SECONDS if fails >= MAX_ERRORS else 0
        db.conn.execute("UPDATE private_secondary_password SET failures=?,blocked_until=? WHERE player_id=?",
                        (0 if until else fails, until, session.player_id))
        verified = None
    elif op.status == pb.StatusCode_OK:
        if op.kind == pb.SPOperateType_Close:
            db.conn.execute("DELETE FROM private_secondary_password WHERE player_id=?", (session.player_id,))
            row = verified = None
        elif op.replacement:
            salt, verifier = op.replacement
            db.conn.execute("""INSERT INTO private_secondary_password(player_id,salt,verifier) VALUES(?,?,?)
                ON CONFLICT(player_id) DO UPDATE SET salt=excluded.salt,verifier=excluded.verifier,
                failures=0,blocked_until=0""", (session.player_id, salt, verifier))
            row = (salt, verifier, 0, 0)
            verified = salt
        else:
            if row[2] or row[3]:
                db.conn.execute("UPDATE private_secondary_password SET failures=0,blocked_until=0 WHERE player_id=?",
                                (session.player_id,))
            verified = row[0]
    flags(session, reply, row, verified)
    set_after_commit(session, "secondary_verified", verified)
    # Only settled operations need replay protection. Lockout and worker contention expire.
    if op.receipt[0] and (reply.status == pb.StatusCode_OK or counted_wrong):
        cache_after_commit(session, "secondary_receipts", op.receipt, reply.SerializeToString())
