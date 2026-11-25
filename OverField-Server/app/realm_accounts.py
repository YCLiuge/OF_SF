"""Password authentication for independent private-realm accounts."""
from collections import OrderedDict, deque
from contextlib import closing
import hashlib
import hmac
import ipaddress
import re
import secrets
import sqlite3
import threading
import time
import unicodedata

from local_identity import SLOT, PROFILE_KEY, _digest, _name
from profile_issuance import authorizes_game_key

PREFIX = "/local/v1/accounts/"
ROUTES = {PREFIX+k for k in ("challenge","lookup","register","login","bind","launch","claim-gift","import-preview","import-apply","import-undo")}
USER = re.compile(r"[A-Za-z0-9_.-]{3,40}\Z", re.ASCII)
N, R, P = 32768, 8, 1
SALT_SIZE = 16


IMPORT_ROUTES = {PREFIX+k for k in ("import-preview","import-apply","import-undo")}


class AccountError(ValueError):
    def __init__(self,status,code):
        self.status, self.code = status,code
        super().__init__(code)


def initialize(identity):
    with identity._write() as db:
        db.execute("CREATE TABLE IF NOT EXISTS realm_launch_tickets(digest BLOB PRIMARY KEY REFERENCES tickets(ticket_hash) ON DELETE CASCADE) WITHOUT ROWID")
        db.execute("CREATE TABLE IF NOT EXISTS realm_pending_names(slot TEXT PRIMARY KEY REFERENCES profiles(slot) ON DELETE CASCADE) WITHOUT ROWID")
        db.execute("CREATE TABLE IF NOT EXISTS realm_named_profiles(slot TEXT PRIMARY KEY REFERENCES profiles(slot) ON DELETE CASCADE) WITHOUT ROWID")
        db.execute("""CREATE TABLE IF NOT EXISTS realm_import_name_reservations(
            name_key TEXT PRIMARY KEY, slot TEXT NOT NULL UNIQUE REFERENCES profiles(slot) ON DELETE CASCADE) WITHOUT ROWID""")
        db.execute("""CREATE TABLE IF NOT EXISTS realm_accounts(
            username TEXT PRIMARY KEY, slot TEXT NOT NULL UNIQUE REFERENCES profiles(slot) ON DELETE CASCADE,
            salt BLOB NOT NULL, verifier BLOB NOT NULL, n INTEGER NOT NULL,r INTEGER NOT NULL,p INTEGER NOT NULL
        ) WITHOUT ROWID""")


def credentials(body):
    u,p = body.get("username"),body.get("password")
    if not isinstance(u,str) or not USER.fullmatch(u):
        raise AccountError(400,"invalid_username")
    if not isinstance(p,str) or not 8 <= len(p) <= 128:
        raise AccountError(400,"invalid_password")
    try:
        password = p.encode("utf8")
    except UnicodeError:
        raise AccountError(400,"invalid_password") from None
    return u.lower(),password


def nickname(text):
    try:
        return _name(text)
    except ValueError:
        raise AccountError(400,"invalid_nickname") from None


def derive(password, salt):
    return hashlib.scrypt(password,salt=salt,n=N,r=R,p=P,maxmem=64*1024*1024,dklen=32)


def ticket(identity,db,slot,name):
    now = int(identity._clock())
    value = "oft_" + secrets.token_urlsafe(32)
    db.execute("DELETE FROM tickets WHERE expires<=?",(now,))
    db.execute("INSERT INTO tickets VALUES (?,?,?)",(_digest(value),slot,now+identity.ticket_ttl))
    return {"realm":"overfield-local-v1","slot":slot,"nickname":name,"ticket":value,"expires_in":identity.ticket_ttl}


def matches_played(identity,slot,key):
    game = identity.path.parent/"game.sqlite"
    if not game.is_file():
        return False
    with closing(sqlite3.connect(game.as_uri()+"?mode=ro",uri=True,timeout=2)) as db:
        if not db.execute("SELECT 1 FROM sqlite_schema WHERE name='private_slots'").fetchone():
            return False
        rows = db.execute("SELECT p.player_name,s.slot FROM players p LEFT JOIN private_slots s ON s.player_id=p.player_id WHERE p.player_name IS NOT NULL AND p.player_name!=''")
        return any(other != slot and unicodedata.normalize("NFKC",name.strip()).casefold()==key for name,other in rows)


def register(identity,body,bind):
    keys={"username","password","nickname"} | ({"slot","profile_key"} if bind else set())
    if set(body)!=keys and (bind or set(body)!=keys-{"nickname"}):
        raise AccountError(400,"invalid_request")
    user,password=credentials(body)
    slot=body["slot"] if bind else "ofp_"+secrets.token_hex(16)
    deferred=not bind and body.get("nickname","")==""
    display,key=nickname("队长"+secrets.token_hex(6) if deferred else body["nickname"])
    salt=secrets.token_bytes(SALT_SIZE)
    verifier=derive(password,salt)
    with identity._write() as db:
        if bind:
            secret=body["profile_key"]
            if not isinstance(slot,str) or not SLOT.fullmatch(slot) or not isinstance(secret,str) or not PROFILE_KEY.fullmatch(secret):
                raise AccountError(401,"invalid_credentials")
            row=db.execute("SELECT secret_hash FROM profiles WHERE slot=?",(slot,)).fetchone()
            digest=_digest(secret)
            if not row or not (hmac.compare_digest(digest,row[0]) or authorizes_game_key(db,slot,digest)):
                raise AccountError(401,"invalid_credentials")
            if db.execute("SELECT 1 FROM realm_accounts WHERE slot=?",(slot,)).fetchone():
                raise AccountError(409,"profile_already_bound")
        if db.execute("SELECT 1 FROM realm_accounts WHERE username=?",(user,)).fetchone():
            raise AccountError(409,"username_taken")
        reserved=db.execute("""SELECT 1 FROM profiles WHERE name_key=? AND slot!=?
                          UNION ALL SELECT 1 FROM realm_import_name_reservations WHERE name_key=? AND slot!=? LIMIT 1""",
                          (key,slot,key,slot)).fetchone()
        if reserved or matches_played(identity,slot,key):
            raise AccountError(409,"nickname_taken")
        if bind:
            db.execute("UPDATE profiles SET nickname=?,name_key=? WHERE slot=?",(display,key,slot))
        else:
            db.execute("INSERT INTO profiles(slot,secret_hash,nickname,name_key) VALUES(?,?,?,?)",
                       (slot,hashlib.sha256(secrets.token_bytes(32)).digest(),display,key))
        db.execute("INSERT INTO realm_accounts VALUES(?,?,?,?,?,?,?)",(user,slot,salt,verifier,N,R,P))
        if deferred:
            db.execute("INSERT INTO realm_pending_names VALUES(?)",(slot,))
        return ticket(identity,db,slot,"" if deferred else display)


def login(identity,body):
    if set(body)!={"username","password"}:
        raise AccountError(400,"invalid_request")
    user,password=credentials(body)
    with closing(sqlite3.connect(identity.path)) as db:
        row=db.execute("SELECT a.slot,a.salt,a.verifier,a.n,a.r,a.p FROM realm_accounts a WHERE a.username=?",(user,)).fetchone()
    # Keep the same KDF cost for unknown names. Never run unbounded parameters from stored data.
    candidate=derive(password,row[1] if row else bytes(SALT_SIZE))
    if not hmac.compare_digest(candidate,row[2] if row else bytes(32)) or not row or row[3:]!=(N,R,P):
        raise AccountError(401,"invalid_credentials")
    with identity._write() as db:
        current=db.execute("SELECT p.nickname FROM realm_accounts a JOIN profiles p ON p.slot=a.slot WHERE a.username=? AND a.slot=? AND a.verifier=?",
                           (user,row[0],row[2])).fetchone()
        if not current:
            raise AccountError(401,"invalid_credentials")
        pending=db.execute("SELECT 1 FROM realm_pending_names WHERE slot=?",(row[0],)).fetchone()
        return ticket(identity,db,row[0],"" if pending else current[0])


def reserve_native(identity,body):
    from local_identity import TICKET
    if set(body)!={"slot","ticket"} or not isinstance(body["slot"],str) or not SLOT.fullmatch(body["slot"]) or not isinstance(body["ticket"],str) or not TICKET.fullmatch(body["ticket"]):
        raise AccountError(401,"invalid_credentials")
    slot,now=body["slot"],int(identity._clock())
    digest=_digest(body["ticket"])
    with identity._write() as db:
        valid=db.execute("SELECT 1 FROM tickets t JOIN realm_accounts a ON a.slot=t.slot WHERE t.ticket_hash=? AND t.slot=? AND t.expires>? AND NOT EXISTS(SELECT 1 FROM realm_launch_tickets n WHERE n.digest=t.ticket_hash)",(digest,slot,now)).fetchone()
        if not valid:
            raise AccountError(401,"invalid_credentials")
        db.execute("DELETE FROM tickets WHERE ticket_hash=? OR expires<=?",(digest,now))
        value="oft_"+secrets.token_urlsafe(32)
        hashed=_digest(value)
        db.execute("INSERT INTO tickets VALUES(?,?,?)",(hashed,slot,now+300))
        db.execute("INSERT INTO realm_launch_tickets VALUES(?)",(hashed,))
        return {"realm":"overfield-local-v1","slot":slot,"ticket":value,"expires_in":300}


class AccountService:
    def __init__(self,identity):
        from private_gift_service import GiftService
        self.gifts=GiftService()
        from save_import_service import ImportService
        self.imports=ImportService()
        initialize(identity)
        self.identity=identity
        from player_captcha import PlayerCaptcha
        self.captcha=PlayerCaptcha()
        from player_gateway_peer import prepare,source_id,PROXY_LOOPBACKS
        self.gateway_peer_key=prepare(identity)
        self.shared_sources=frozenset(source_id(self.gateway_peer_key,address) for address in PROXY_LOOPBACKS)
        self.workers=threading.BoundedSemaphore(3)
        self.lock=threading.Lock()
        self.attempts=OrderedDict()

    def request(self,path,body,peer,local_bind,tls,open_registration,*,gateway_source=None):
        if not tls and not (ipaddress.ip_address(peer).is_loopback and ipaddress.ip_address(local_bind).is_loopback):
            raise AccountError(403,"secure_transport_required")
        if path==PREFIX+"register" and not open_registration:
            raise AccountError(403,"registration_closed")
        action=path.removeprefix(PREFIX)
        now=time.monotonic()
        bucket, limit = peer, 30
        shared=gateway_source in self.shared_sources
        if gateway_source is not None:
            family = action if action in ("challenge","lookup","register","login") else "other"
            bucket = (gateway_source, family)
            if shared:
                limit=120 if family in ("challenge","lookup","register") else 240
            else:
                limit=6 if family=="register" else 30
        with self.lock:
            if bucket not in self.attempts and len(self.attempts)>=4096:
                self.attempts=OrderedDict((k,v) for k,v in self.attempts.items() if v and v[-1]>=now-60)
                if len(self.attempts)>=4096:
                    raise AccountError(429,"rate_limited")
            hits=self.attempts.setdefault(bucket,deque())
            self.attempts.move_to_end(bucket)
            while hits and hits[0]<now-60:
                hits.popleft()
            if len(hits)>=limit:
                raise AccountError(429,"rate_limited")
            if not self.workers.acquire(False):
                raise AccountError(429,"rate_limited")
            hits.append(now)
        try:
            public=gateway_source is not None or not ipaddress.ip_address(peer).is_loopback
            source=gateway_source or peer
            if action=="challenge":
                return self.captcha.issue(body,source,shared=shared)
            if action=="lookup":
                from player_captcha import username
                form=dict(body)
                user=username(form)
                self.captcha.verify(form,user,source,"lookup",True)
                if set(form)!={"username"}:raise AccountError(400,"invalid_request")
                with closing(sqlite3.connect(self.identity.path)) as db:
                    exists=db.execute("SELECT 1 FROM realm_accounts WHERE username=?",(user,)).fetchone() is not None
                return self.captcha.lookup_proof(source,user,exists)
            if path==PREFIX+"launch":
                return reserve_native(self.identity,body)
            if path==PREFIX+"claim-gift":
                return self.gifts.claim(body)
            if path in IMPORT_ROUTES:
                return self.imports.request(path.removeprefix(PREFIX),body)
            from player_captcha import username
            user=username(body)
            form=dict(body)
            signing_in=action=="login"
            required=public and (not signing_in or self.captcha.login_challenge(source,user,shared=shared))
            self.captcha.verify(form,user,source,"login" if signing_in else "register",required)
            try:
                result=login(self.identity,form) if signing_in else register(self.identity,form,action=="bind")
            except AccountError as error:
                if signing_in and public and error.code=="invalid_credentials":
                    self.captcha.failed(source,user)
                raise
            if signing_in and public:self.captcha.succeeded(source,user)
            if self.gifts.game is not None:
                result.update(self.gifts.issue(result["slot"]))
            if self.imports.game is not None:
                result.update(self.imports.issue(result["slot"]))
            return result
        finally:
            self.workers.release()

