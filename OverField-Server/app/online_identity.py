"""Validate elected native PC sessions against the product issuer using its documented protocol."""
from hashlib import sha256
import hmac
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import secrets
from contextlib import closing
import ssl
from urllib.parse import urlencode

from local_identity import SLOT, PROFILE_KEY

# The installed national PC product fixes this issuer in QuickSDK PackageSettings.
ISSUER = "sdkapi-of.inutan.com"
ROUTE = "/webapi/checkUserInfo"
VERIFIER_URL = "https://" + ISSUER + ROUTE
UID = re.compile(r"[A-Za-z0-9._@:+\-]{1,128}\Z", re.ASCII)
DENIED = "Online identity validation failed"

def _uid_bytes(uid):
    if not isinstance(uid,str) or UID.fullmatch(uid) is None:
        raise ValueError("Invalid SDK user ID")
    return (ISSUER+"\0"+uid).encode("ascii")

def _validate_issuer(uid, token):
    form=urlencode({"uid":uid,"token":token}).encode("ascii")
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_default_certs()
    connection=http.client.HTTPSConnection(ISSUER,443,timeout=4,context=context)
    try:
        connection.request("POST",ROUTE,body=form,headers={
            "Content-Type":"application/x-www-form-urlencoded","Accept":"application/json",
            "Cache-Control":"no-store"})
        response=connection.getresponse()
        if response.status!=200:
            return False
        data=response.read(8193)
        if len(data)>8192:
            return False
        result=json.loads(data)
        if not isinstance(result,dict) or result.get("status") is not True:
            return False
        info=result.get("data")
        if not isinstance(info,dict) or type(info.get("uid")) not in (str,int):
            return False
        return str(info["uid"])==uid
    except (OSError,http.client.HTTPException,ValueError,RecursionError):
        return False
    finally:
        connection.close()

def _schema(connection):
    connection.execute("CREATE TABLE IF NOT EXISTS online_salt(id INTEGER PRIMARY KEY CHECK(id=1), salt BLOB NOT NULL CHECK(length(salt)=32))")
    connection.execute("INSERT OR IGNORE INTO online_salt VALUES(1,?)",(secrets.token_bytes(32),))
    connection.execute("""CREATE TABLE IF NOT EXISTS online_bindings (
        subject_hash BLOB PRIMARY KEY CHECK(length(subject_hash)=32),
        slot TEXT UNIQUE NOT NULL REFERENCES profiles(slot) ON DELETE CASCADE
    ) WITHOUT ROWID""")

def bind(identity, profile_path, uid, *, remove=False):
    uid_bytes=_uid_bytes(uid)
    if Path(profile_path).stat().st_size>16384:
        raise ValueError("Invalid private profile file")
    with open(profile_path,encoding="utf8") as source:
        profile=json.load(source)
    if not isinstance(profile,dict) or profile.get("realm")!="overfield-local-v1":
        raise ValueError("Invalid private profile file")
    slot,secret=profile.get("slot"),profile.get("profile_key")
    if not isinstance(slot,str) or not SLOT.fullmatch(slot) or not isinstance(secret,str) or not PROFILE_KEY.fullmatch(secret):
        raise ValueError("Invalid private profile file")
    with identity._write() as db:
        _schema(db)
        subject=hmac.digest(db.execute("SELECT salt FROM online_salt WHERE id=1").fetchone()[0],uid_bytes,"sha256")
        row=db.execute("SELECT secret_hash FROM profiles WHERE slot=?",(slot,)).fetchone()
        if not row or not hmac.compare_digest(row[0],sha256(secret.encode("ascii")).digest()):
            raise ValueError("This file does not authorize the private profile")
        if remove:
            if db.execute("DELETE FROM online_bindings WHERE subject_hash=? AND slot=?",(subject,slot)).rowcount!=1:
                raise ValueError("The selected binding does not exist")
        else:
            existing=db.execute("SELECT subject_hash,slot FROM online_bindings WHERE subject_hash=? OR slot=?",(subject,slot)).fetchall()
            if existing and existing!=[(subject,slot)]:
                raise ValueError("That SDK user or private profile already has another binding")
            if not existing:
                db.execute("INSERT INTO online_bindings VALUES(?,?)",(subject,slot))

class OnlineIdentity:
    def __init__(self,identity,auto_create=False):
        self.identity=identity
        self.auto_create=auto_create
        with identity._write() as db:
            _schema(db)
            self.salt=db.execute('SELECT salt FROM online_salt WHERE id=1').fetchone()[0]

    @classmethod
    def configured(cls,identity,bind_address):
        url=os.environ.get("OF_QUICKSDK_VERIFY_URL","").strip()
        if not url:
            return None
        if url!=VERIFIER_URL:
            raise ValueError("SDK verification must use the national PC product's exact HTTPS issuer")
        if not ipaddress.ip_address(bind_address).is_loopback:
            raise ValueError("The national PC online issuer accepts only same-machine realm sessions here")
        creation=os.environ.get("OF_QUICKSDK_CREATE_PLAYERS","0")
        if creation not in ("0","1"):
            raise ValueError("OF_QUICKSDK_CREATE_PLAYERS must be 0 or 1")
        return cls(identity,auto_create=(creation=="1"))

    def _binding(self,subject):
        with closing(sqlite3.connect(self.identity.path)) as db:
            row=db.execute("SELECT slot FROM online_bindings WHERE subject_hash=?",(subject,)).fetchone()
        return row[0] if row else None

    def _create_after_verification(self,subject):
        # The gameplay DB stores only realm slots. Claim the binding and fresh identity atomically.
        with self.identity._write() as db:
            existing=db.execute("SELECT slot FROM online_bindings WHERE subject_hash=?",(subject,)).fetchone()
            if existing:
                return existing[0]
            while True:
                slot="ofp_"+secrets.token_hex(16)
                name="队长"+slot[-12:]
                inserted=db.execute("INSERT OR IGNORE INTO profiles(slot,secret_hash,nickname,name_key) VALUES(?,?,?,?)",
                                    (slot,secrets.token_bytes(32),name,name.casefold()))
                if inserted.rowcount:
                    break
            db.execute("INSERT INTO online_bindings VALUES(?,?)",(subject,slot))
            return slot

    def verify_channel(self,uid,token,peer):
        if not ipaddress.ip_address(peer).is_loopback:
            raise ValueError(DENIED)
        _uid_bytes(uid)
        if not isinstance(token,str) or not 1<=len(token)<=4096 or not token.isascii() or not token.isprintable() or token.startswith(("oft_","ofk_")):
            raise ValueError(DENIED)
        if not _validate_issuer(uid,token):
            raise ValueError(DENIED)

    def authenticate(self,uid,token,peer):
        address=ipaddress.ip_address(peer)
        if not address.is_loopback:
            raise ValueError(DENIED)
        subject=hmac.digest(self.salt,_uid_bytes(uid),"sha256")
        if not isinstance(token,str) or not 1<=len(token)<=4096 or not token.isascii() or not token.isprintable():
            raise ValueError(DENIED)
        if token.startswith(("oft_","ofk_")):
            raise ValueError(DENIED)
        slot=self._binding(subject)
        if (slot is None and not self.auto_create) or not _validate_issuer(uid,token):
            raise ValueError(DENIED)
        if slot is None:
            return self._create_after_verification(subject)
        if self._binding(subject)!=slot:
            raise ValueError(DENIED)
        return slot
