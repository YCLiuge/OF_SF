"""Issue scoped gift tickets and serialize HTTP and native inventory changes on the same game worker."""
import base64
import hashlib
import hmac
import re
import secrets
import time

from local_identity import SLOT

TICKET = re.compile(r"ofg_[A-Za-z0-9_-]{43}\Z", re.ASCII)
REQUEST = re.compile(r"[A-Za-z0-9_-]{16,100}\Z", re.ASCII)
TTL = 300


class GiftService:
    def __init__(self):
        self.game = None

    def issue(self,slot,login_ticket):
        from realm_accounts import issue_epoch
        from utils import db
        game=self.game
        if not game or game.closing.is_set() or not SLOT.fullmatch(slot):
            return {}
        ticket="ofg_"+secrets.token_urlsafe(32)
        now=int(time.time())
        with game.lock:
            if game.closing.is_set() or db.conn is None:
                return {}
            with db.conn:
                epoch=issue_epoch(db.conn,slot,login_ticket)
                db.conn.execute("DELETE FROM private_gift_tickets WHERE expires<=?",(now,))
                db.conn.execute("INSERT INTO private_gift_tickets(digest,slot,expires,epoch) VALUES(?,?,?,?)",
                                (hashlib.sha256(ticket.encode("ascii")).digest(),slot,now+TTL,epoch))
        return {"gift_ticket":ticket,"gift_expires_in":TTL}

    def catalog(self):
        from realm_accounts import AccountError
        if not self.game or self.game.closing.is_set():
            raise AccountError(503,"gift_service_unavailable")
        from private_gifts import catalog
        with self.game.lock:
            return {"ok":True,"gifts":catalog()}

    def claim(self,body):
        from realm_accounts import AccountError
        from utils import db
        from network.msg_id import MsgId
        from proto import net_pb2 as pb
        if set(body) not in ({"ticket","shop","grid","request_id"},{"ticket","shop","grid","pool","request_id"}):
            raise AccountError(400,"invalid_request")
        ticket=body["ticket"]
        if not isinstance(ticket,str) or not TICKET.fullmatch(ticket):
            raise AccountError(401,"gift_login_required")
        request_id=body["request_id"]
        shop,grid,pool=body["shop"],body["grid"],body.get("pool",0)
        if (not isinstance(request_id,str) or not REQUEST.fullmatch(request_id) or
                any(type(i) is not int or not 0<=i<=0xffffffff for i in (shop,grid,pool)) or not shop or not grid):
            raise AccountError(400,"invalid_request")
        game=self.game
        if not game or game.closing.is_set():
            raise AccountError(503,"gift_service_unavailable")
        digest=hashlib.sha256(ticket.encode("ascii")).digest()
        # The bearer is random and secret. Derivation permits an exact retry to recover
        # the same successor while the database contains only token digests.
        successor="ofg_"+base64.urlsafe_b64encode(hmac.digest(ticket.encode("ascii"),b"gift-next/"+request_id.encode("ascii"),"sha256")).decode("ascii").rstrip("=")
        from private_gifts import claim
        with game.lock:
            now=int(time.time())
            if game.closing.is_set() or db.conn is None:
                raise AccountError(503,"gift_service_unavailable")
            with db.conn:
                table=db.conn.execute("SELECT 1 FROM sqlite_schema WHERE name='private_gift_tickets'").fetchone()
                row=db.conn.execute("SELECT t.slot,t.expires,t.epoch,t.request_id,t.shop,t.grid,t.pool FROM private_gift_tickets t LEFT JOIN authority.realm_password_epochs e ON e.slot=t.slot WHERE t.digest=? AND t.expires>? AND t.epoch=coalesce(e.epoch,0)",
                                    (digest,now)).fetchone() if table else None
                if not row:
                    raise AccountError(401,"gift_login_required")
                slot,expiry,epoch,previous,*params=row
                player=db.conn.execute("""SELECT p.player_id FROM private_slots p JOIN players g ON g.player_id=p.player_id
                    JOIN authority.realm_accounts a ON a.slot=p.slot WHERE p.slot=?
                    AND NOT EXISTS(SELECT 1 FROM authority.realm_pending_names n WHERE n.slot=p.slot)""",(slot,)).fetchone()
                if not player:
                    raise AccountError(409,"gift_character_required")
                replay=previous is not None
                if replay and (previous!=request_id or params!=[shop,grid,pool]):
                    raise AccountError(409,"gift_ticket_used")
                if not replay:
                    status,delivery,inventory,extra=claim(player[0],shop,grid,request_id,pool=pool)
                    if status!=pb.StatusCode_OK:
                        raise AccountError(409,"gift_unavailable")
                    db.conn.execute("UPDATE private_gift_tickets SET request_id=?,shop=?,grid=?,pool=? WHERE digest=?",
                                    (request_id,shop,grid,pool,digest))
                    db.conn.execute("INSERT INTO private_gift_tickets(digest,slot,expires,epoch) VALUES(?,?,?,?)",
                                    (hashlib.sha256(successor.encode("ascii")).digest(),slot,expiry,epoch))
            session=game.active.get(slot)
            if not replay and session and session.logged_in and session.running:
                for cmd,message in [(MsgId.PaySendGoodsNotice,delivery),(MsgId.PackNotice,inventory),*extra]:
                    if message is not None:
                        session.send(cmd,message,0)
                session.publish()
            return {"ok":True,"claimed":True,"gift_ticket":successor,"gift_expires_in":max(0,expiry-now)}

