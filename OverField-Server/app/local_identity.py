"""Persistent private-service identities with short-lived single-use connection tickets."""
from contextlib import contextmanager
from hashlib import sha256
import hmac
from pathlib import Path
import re
import secrets
import sqlite3
import time
import unicodedata


class InvalidTicket(ValueError):
    """The connection ticket is invalid, expired or already consumed."""


class InvalidProfile(ValueError):
    """The profile secret is invalid for this server."""


class NicknameUnavailable(ValueError):
    """The selected nickname is already in use on this service."""


def _name(value):
    if not isinstance(value, str):
        raise ValueError("Nickname must be text")
    value = unicodedata.normalize("NFC", value.strip())
    if not 1 <= len(value) <= 16 or any(unicodedata.category(c)[0] in "CZ" and c != " " for c in value):
        raise ValueError("Nickname must be 1..16 printable characters")
    return value, unicodedata.normalize("NFKC", value).casefold()


SLOT = re.compile(r"ofp_[0-9a-f]{32}\Z", re.ASCII)
PROFILE_KEY = re.compile(r"ofk_[A-Za-z0-9_-]{43}\Z", re.ASCII)
TICKET = re.compile(r"oft_[A-Za-z0-9_-]{43}\Z", re.ASCII)
APPLICATION_ID = 0x4F464944


def _digest(secret):
    return sha256(secret.encode("ascii")).digest()


def _valid(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


class LocalIdentity:
    """One instance per service is shareable by worker threads."""

    def __init__(self, database, *, ticket_ttl=90, clock=time.time):
        if str(database) == ":memory:":
            raise ValueError("Identity storage requires a persistent SQLite path")
        if type(ticket_ttl) is not int or not 1 <= ticket_ttl <= 1800:
            raise ValueError("ticket_ttl must be 1..1800 seconds")
        self.path = Path(database).resolve()
        self.ticket_ttl = ticket_ttl
        self._clock = clock
        with self._write() as db:
            appid, = db.execute("PRAGMA application_id").fetchone()
            version, = db.execute("PRAGMA user_version").fetchone()
            if (appid, version) == (APPLICATION_ID, 2):
                return
            if (appid, version) == (APPLICATION_ID, 1):
                self._upgrade_names(db)
                return
            if appid or version or db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone():
                raise ValueError("Unknown identity database type/version")
            db.execute("""CREATE TABLE profiles (
                slot TEXT PRIMARY KEY, secret_hash BLOB NOT NULL CHECK(length(secret_hash)=32),
                nickname TEXT NOT NULL CHECK(length(nickname)>0), name_key TEXT NOT NULL
            ) WITHOUT ROWID""")
            db.execute("CREATE UNIQUE INDEX profiles_name ON profiles(name_key)")
            db.execute("""CREATE TABLE tickets (
                ticket_hash BLOB PRIMARY KEY CHECK(length(ticket_hash)=32),
                slot TEXT NOT NULL REFERENCES profiles(slot) ON DELETE CASCADE,
                expires INTEGER NOT NULL
            ) WITHOUT ROWID""")
            db.execute("CREATE INDEX tickets_expiry ON tickets(expires)")
            db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            db.execute("PRAGMA user_version=2")

    @staticmethod
    def _upgrade_names(db):
        db.execute("ALTER TABLE profiles ADD COLUMN nickname TEXT NOT NULL DEFAULT ''")
        db.execute("ALTER TABLE profiles ADD COLUMN name_key TEXT NOT NULL DEFAULT ''")
        for slot, in db.execute("SELECT slot FROM profiles ORDER BY slot").fetchall():
            display = "队长" + slot[-12:]
            while db.execute("SELECT 1 FROM profiles WHERE name_key=?", (display.casefold(),)).fetchone():
                display = "队长" + secrets.token_hex(6)
            db.execute("UPDATE profiles SET nickname=?, name_key=? WHERE slot=?", (display, display.casefold(), slot))
        db.execute("CREATE UNIQUE INDEX profiles_name ON profiles(name_key)")
        db.execute("PRAGMA user_version=2")

    @contextmanager
    def _write(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def creating_profile(self, nickname=None):
        """Commit a new profile only if the caller successfully saves its credentials."""
        chosen = _name(nickname) if nickname is not None else None
        with self._write() as db:
            has_imports = db.execute("SELECT 1 FROM sqlite_schema WHERE name='realm_import_name_reservations'").fetchone()
            while True:
                slot = "ofp_" + secrets.token_hex(16)
                secret = "ofk_" + secrets.token_urlsafe(32)
                display, name_key = chosen or _name("队长" + slot[-12:])
                reserved = has_imports and db.execute("SELECT 1 FROM realm_import_name_reservations WHERE name_key=?", (name_key,)).fetchone()
                if reserved or db.execute("SELECT 1 FROM profiles WHERE name_key=?", (name_key,)).fetchone():
                    if chosen is not None:
                        raise NicknameUnavailable("Nickname already in use")
                    continue
                db.execute("INSERT INTO profiles(slot,secret_hash,nickname,name_key) VALUES (?,?,?,?)",
                           (slot, _digest(secret), display, name_key))
                yield slot, secret, display
                return

    def create_profile(self, nickname=None):
        """Return a new slot and its independent recovery key exactly once."""
        with self.creating_profile(nickname) as (slot, secret, display):
            return slot, secret

    def profile_name(self, slot):
        if not _valid(SLOT, slot):
            raise InvalidProfile("Unknown profile")
        db = sqlite3.connect(self.path, timeout=10)
        try:
            row = db.execute("SELECT nickname FROM profiles WHERE slot=?", (slot,)).fetchone()
        finally:
            db.close()
        if row is None:
            raise InvalidProfile("Unknown profile")
        return row[0]

    def issue_ticket(self, slot, profile_key):
        """Prove ownership of the local slot, then issue one gameplay ticket."""
        if not _valid(SLOT, slot) or not _valid(PROFILE_KEY, profile_key):
            raise InvalidProfile("Invalid profile credentials")
        now = int(self._clock())
        ticket = "oft_" + secrets.token_urlsafe(32)
        with self._write() as db:
            row = db.execute("SELECT secret_hash FROM profiles WHERE slot=?", (slot,)).fetchone()
            expected = bytes(32) if row is None else row[0]
            digest = _digest(profile_key)
            original = hmac.compare_digest(digest, expected) and row is not None
            if not original:
                from profile_issuance import authorizes_game_key
                if row is None or not authorizes_game_key(db, slot, digest):
                    raise InvalidProfile("Invalid profile credentials")
            db.execute("DELETE FROM tickets WHERE expires <= ?", (now,))
            db.execute("INSERT INTO tickets VALUES (?,?,?)", (_digest(ticket), slot, now + self.ticket_ttl))
        return ticket

    def resolve_ticket(self, ticket):
        """Atomically consume the ticket at the gameplay boundary and return its slot."""
        if not _valid(TICKET, ticket):
            raise InvalidTicket("Invalid connection ticket")
        digest = _digest(ticket)
        with self._write() as db:
            row = db.execute("SELECT slot FROM tickets WHERE ticket_hash=? AND expires>?",
                             (digest, int(self._clock()))).fetchone()
            if row is None:
                raise InvalidTicket("Invalid connection ticket")
            db.execute("DELETE FROM tickets WHERE ticket_hash=?", (digest,))
        return row[0]

