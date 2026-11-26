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
ROUTES = {PREFIX+k for k in ("challenge","lookup","register","login","change-password","bind","launch","claim-gift","import-preview","import-apply","import-undo")}
USER = re.compile(r"[A-Za-z0-9_.\-\u3400-\u4dbf\u4e00-\u9fff]{3,40}\Z")
N, R, P = 32768, 8, 1
SALT_SIZE = 16
RESUME = re.compile(r"ofr_[A-Za-z0-9_-]{43}\Z", re.ASCII)
RESUME_TTL = 8 * 3600
RESUME_GRACE = 120


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
        # Supported account letters are Latin ASCII and Han. SQLite NOCASE folds exactly
        # the case-bearing characters of this alphabet, including within a mixed name.
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS realm_account_login ON realm_accounts(username COLLATE NOCASE)")
        db.execute("""CREATE TABLE IF NOT EXISTS realm_native_resume(
            slot TEXT PRIMARY KEY REFERENCES realm_accounts(slot) ON DELETE CASCADE,
            digest BLOB NOT NULL UNIQUE REFERENCES tickets(ticket_hash) ON DELETE CASCADE,
            game_digest BLOB NOT NULL, reconnect_until INTEGER NOT NULL DEFAULT 0
        ) WITHOUT ROWID""")
        db.execute("""CREATE TABLE IF NOT EXISTS realm_password_epochs(slot TEXT PRIMARY KEY REFERENCES
            realm_accounts(slot) ON DELETE CASCADE, epoch INTEGER NOT NULL) WITHOUT ROWID""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS realm_rotate_password_epoch AFTER UPDATE OF verifier ON realm_accounts
            WHEN NEW.verifier!=OLD.verifier BEGIN INSERT INTO realm_password_epochs VALUES(NEW.slot,1)
            ON CONFLICT(slot) DO UPDATE SET epoch=epoch+1; END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS realm_password_changed AFTER UPDATE OF verifier
            ON realm_accounts WHEN NEW.verifier!=OLD.verifier BEGIN
            DELETE FROM tickets WHERE slot=NEW.slot; END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS realm_password_removed AFTER DELETE ON realm_accounts
            BEGIN DELETE FROM tickets WHERE slot=OLD.slot; END""")


def username_spelling(value):
    if not isinstance(value,str) or not 3 <= len(value) <= 40:
        raise AccountError(400,"invalid_username")
    name=unicodedata.normalize("NFKC",value)
    if not USER.fullmatch(name):
        raise AccountError(400,"invalid_username")
    return name


def username_key(value):
    return username_spelling(value).lower()


def password_bytes(p):
    if not isinstance(p,str) or not 8 <= len(p) <= 128:
        raise AccountError(400,"invalid_password")
    try:
        password = p.encode("utf8")
    except UnicodeError:
        raise AccountError(400,"invalid_password") from None
    return password


def credentials(body):
    user=username_spelling(body.get("username"))
    password=password_bytes(body.get("password"))
    # Preserve the legacy representation of ASCII-only accounts.
    return (user.lower() if user.isascii() else user),password


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
        from player_name_index import played_by_other
        return played_by_other(db,slot,key)


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
        if db.execute("SELECT 1 FROM realm_accounts WHERE username=? COLLATE NOCASE",(user,)).fetchone():
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


def verify_password(identity,user,password):
    with closing(sqlite3.connect(identity.path)) as db:
        row=db.execute("SELECT a.slot,a.salt,a.verifier,a.n,a.r,a.p FROM realm_accounts a WHERE a.username=? COLLATE NOCASE",(user,)).fetchone()
    # Keep the same KDF cost for unknown names. Never run unbounded parameters from stored data.
    candidate=derive(password,row[1] if row else bytes(SALT_SIZE))
    if not hmac.compare_digest(candidate,row[2] if row else bytes(32)) or not row or row[3:]!=(N,R,P):
        raise AccountError(401,"invalid_credentials")
    return row[0],row[2]


def change_password(identity,body):
    if set(body)!={"username","password","new_password"}:
        raise AccountError(400,"invalid_request")
    user,old=credentials(body)
    new=password_bytes(body["new_password"])
    slot,previous=verify_password(identity,user,old)
    if hmac.compare_digest(old,new):
        raise AccountError(400,"password_unchanged")
    salt=secrets.token_bytes(SALT_SIZE)
    verifier=derive(new,salt)
    with identity._write() as db:
        updated=db.execute("""UPDATE realm_accounts SET salt=?,verifier=?,n=?,r=?,p=?
            WHERE username=? COLLATE NOCASE AND slot=? AND verifier=?""",(salt,verifier,N,R,P,user,slot,previous))
        if updated.rowcount!=1:
            raise AccountError(401,"invalid_credentials")
    return {"changed":True,"reauthenticate":True}


def login(identity,body):
    if set(body)!={"username","password"}:
        raise AccountError(400,"invalid_request")
    user,password=credentials(body)
    slot,previous=verify_password(identity,user,password)
    with identity._write() as db:
        current=db.execute("SELECT p.nickname FROM realm_accounts a JOIN profiles p ON p.slot=a.slot WHERE a.username=? COLLATE NOCASE AND a.slot=? AND a.verifier=?",
                           (user,slot,previous)).fetchone()
        if not current:
            raise AccountError(401,"invalid_credentials")
        pending=db.execute("SELECT 1 FROM realm_pending_names WHERE slot=?",(slot,)).fetchone()
        return ticket(identity,db,slot,"" if pending else current[0])


def issue_epoch(conn,slot,login_ticket):
    # Verify the login that initiated issuance again, after acquiring the gameplay writer.
    row=conn.execute("""SELECT coalesce(v.epoch,0) FROM authority.tickets t
        LEFT JOIN authority.realm_password_epochs v ON v.slot=t.slot
        WHERE t.slot=? AND t.ticket_hash=?""",(slot,_digest(login_ticket))).fetchone()
    if not row:
        raise AccountError(401,"invalid_credentials")
    return row[0]


def rename_login(identity,slot,old_name,new_name):
    """Host-only compare-and-set rename. Slot, password material and tickets keep their identity."""
    old=username_key(old_name)
    name=username_spelling(new_name)
    with identity._write() as db:
        row=db.execute("SELECT username FROM realm_accounts WHERE slot=?",(slot,)).fetchone()
        if not row or row[0].lower() not in (old,name.lower()):
            raise AccountError(409,"account_changed")
        if row[0]==name:
            return False
        try:
            db.execute("UPDATE realm_accounts SET username=? WHERE slot=? AND username=?",(name,slot,row[0]))
        except sqlite3.IntegrityError:
            raise AccountError(409,"username_taken") from None
        return True


def reserve_native(identity,body):
    from local_identity import TICKET
    slot=body.get("slot")
    fields=set(body)-{"slot"}
    if not isinstance(slot,str) or not SLOT.fullmatch(slot) or fields not in ({"ticket"},{"resume"}):
        raise AccountError(401,"invalid_credentials")
    field=next(iter(fields))
    value=body[field]
    if not isinstance(value,str) or not (TICKET if field=="ticket" else RESUME).fullmatch(value):
        raise AccountError(401,"invalid_credentials")
    now=int(identity._clock())
    digest=_digest(value)
    with identity._write() as db:
        if field=="ticket":
            valid=db.execute("SELECT 1 FROM tickets t JOIN realm_accounts a ON a.slot=t.slot WHERE t.ticket_hash=? AND t.slot=? AND t.expires>? AND NOT EXISTS(SELECT 1 FROM realm_launch_tickets n WHERE n.digest=t.ticket_hash)",(digest,slot,now)).fetchone()
            if not valid:
                raise AccountError(401,"invalid_credentials")
            expires=now+RESUME_TTL
        else:
            row=db.execute("""SELECT t.expires,r.reconnect_until FROM realm_native_resume r JOIN tickets t ON t.ticket_hash=r.digest
                WHERE r.slot=? AND r.digest=? AND t.expires>?""",(slot,digest,now)).fetchone()
            if not row:
                raise AccountError(401,"invalid_credentials")
            if row[1]==0:
                raise AccountError(409,"game_session_active")
            if row[1]<=now:
                raise AccountError(401,"invalid_credentials")
            expires=row[0]
        db.execute("DELETE FROM tickets WHERE ticket_hash=(SELECT game_digest FROM realm_native_resume WHERE slot=?)",(slot,))
        db.execute("DELETE FROM tickets WHERE ticket_hash=(SELECT digest FROM realm_native_resume WHERE slot=?)",(slot,))
        db.execute("DELETE FROM tickets WHERE ticket_hash=? OR expires<=?",(digest,now))
        # One rotating secret per role. It cannot be used as a native gameplay ticket.
        resume="ofr_"+secrets.token_urlsafe(32)
        resume_digest=_digest(resume)
        db.execute("INSERT INTO tickets VALUES(?,?,?)",(resume_digest,slot,expires))
        value="oft_"+secrets.token_urlsafe(32)
        hashed=_digest(value)
        db.execute("INSERT INTO tickets VALUES(?,?,?)",(hashed,slot,now+300))
        db.execute("INSERT INTO realm_launch_tickets VALUES(?)",(hashed,))
        db.execute("INSERT INTO realm_native_resume(slot,digest,game_digest) VALUES(?,?,?)",(slot,resume_digest,hashed))
        return {"realm":"overfield-local-v1","slot":slot,"ticket":value,"expires_in":300,"resume":resume}


def end_native_session(identity,slot,game_digest,*,logout):
    with identity._write() as db:
        if db.execute("SELECT 1 FROM sqlite_schema WHERE name='realm_native_resume'").fetchone():
            if logout:
                db.execute("""DELETE FROM tickets WHERE ticket_hash=(
                    SELECT digest FROM realm_native_resume WHERE slot=? AND game_digest=?)""",(slot,game_digest))
            else:
                db.execute("""UPDATE realm_native_resume SET reconnect_until=?
                    WHERE slot=? AND game_digest=? AND reconnect_until=0""",
                    (int(identity._clock())+RESUME_GRACE,slot,game_digest))


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
            family = "login" if action=="change-password" else (action if action in ("challenge","lookup","register","login") else "other")
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
                    exists=db.execute("SELECT 1 FROM realm_accounts WHERE username=? COLLATE NOCASE",(user,)).fetchone() is not None
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
            signing_in=action in ("login","change-password")
            required=public and (not signing_in or self.captcha.login_challenge(source,user,shared=shared))
            self.captcha.verify(form,user,source,action if signing_in else "register",required)
            try:
                if action=="change-password":
                    result=change_password(self.identity,form)
                else:
                    result=login(self.identity,form) if signing_in else register(self.identity,form,action=="bind")
            except AccountError as error:
                if signing_in and public and error.code=="invalid_credentials":
                    self.captcha.failed(source,user)
                raise
            if signing_in and public:self.captcha.succeeded(source,user)
            if action=="change-password":return result
            if self.gifts.game is not None:
                result.update(self.gifts.issue(result["slot"],result["ticket"]))
            if self.imports.game is not None:
                result.update(self.imports.issue(result["slot"],result["ticket"]))
            return result
        finally:
            self.workers.release()

