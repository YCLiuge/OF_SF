"""Issue an additional private realm login key locally for a played character."""
import csv
import ctypes
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
from contextlib import closing
from uuid import uuid4

from local_identity import LocalIdentity, SLOT, _digest, _name
from migrate import fsync_directory, publish_new_file
from state_lock import exclusive_state


def authorizes_game_key(db,slot,key_hash):
    table=db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='issued_game_keys'").fetchone()
    return table is not None and db.execute(
        "SELECT 1 FROM issued_game_keys WHERE key_hash=? AND slot=?",(key_hash,slot)).fetchone() is not None


def _private_file(path):
    if os.name!="nt":
        return os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),"wb")
    # Protect the file from its first byte, even in a folder with inheritable user/group permissions.
    import msvcrt
    from ctypes import wintypes as w
    sid=next(csv.reader([subprocess.check_output(["whoami","/user","/fo","csv","/nh"]).decode("ascii","ignore").strip()]))[-1]
    if not re.fullmatch(r"S-1-(?:[0-9]+-)*[0-9]+",sid):
        raise OSError("Could not determine the local Windows account SID")
    class SecurityAttributes(ctypes.Structure):
        _fields_=[("length",w.DWORD),("descriptor",w.LPVOID),("inherit",w.BOOL)]
    adv=ctypes.WinDLL("advapi32",use_last_error=True)
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    convert=adv.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes=[w.LPCWSTR,w.DWORD,ctypes.POINTER(w.LPVOID),ctypes.POINTER(w.DWORD)]
    convert.restype=w.BOOL
    create=kernel.CreateFileW
    create.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.POINTER(SecurityAttributes),w.DWORD,w.DWORD,w.HANDLE]
    create.restype=w.HANDLE
    kernel.LocalFree.argtypes=[w.HLOCAL];kernel.LocalFree.restype=w.HLOCAL
    kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle.restype=w.BOOL
    descriptor=w.LPVOID()
    if not convert("D:P(A;;FA;;;"+sid+")",1,ctypes.byref(descriptor),None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes=SecurityAttributes(ctypes.sizeof(SecurityAttributes),descriptor,False)
        handle=create(str(path),0x40000000,0,ctypes.byref(attributes),1,0x80,None)
        if handle==ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.LocalFree(descriptor)
    try:
        fd=msvcrt.open_osfhandle(handle,os.O_WRONLY|os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(fd,"wb")


def _played(game):
    # Only locally stored game roles can be exported. No issuing-platform user ID is queried.
    with closing(sqlite3.connect(game.as_uri()+"?mode=ro",uri=True)) as db:
        return db.execute("""SELECT s.slot,p.player_name FROM private_slots s
            JOIN players p ON p.player_id=s.player_id""").fetchall()


def _choose(db,played,nickname,slot):
    roles=dict(played)
    if slot:
        if not SLOT.fullmatch(slot):raise ValueError("Invalid private slot")
        candidates={slot} if slot in roles else set()
    elif nickname:
        _,wanted=_name(nickname)
        candidates={s for s,n in roles.items() if n and _name(n)[1]==wanted}
        candidates.update(s for s, in db.execute("SELECT slot FROM profiles WHERE name_key=?",(wanted,)) if s in roles)
    else:
        has_bindings=db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='online_bindings'").fetchone()
        candidates={s for s, in db.execute("SELECT slot FROM online_bindings") if s in roles} if has_bindings else set()
    if len(candidates)!=1:
        raise ValueError("Choose one already-played private character with --nickname or --slot")
    choice=candidates.pop()
    if not db.execute("SELECT 1 FROM profiles WHERE slot=?",(choice,)).fetchone():
        raise ValueError("The selected game has no matching private identity")
    return choice,roles[choice]


def issue_profile(state,output,*,nickname=None,slot=None):
    with exclusive_state(state) as root,exclusive_state(root,purpose="profiles"):
        identity_file=root/"identity.sqlite3"
        game=root/"game.sqlite"
        if not identity_file.is_file() or not game.is_file():
            raise ValueError("Choose an existing stopped realm with a played character")
        out=Path(output).absolute()
        out=out.parent.resolve()/out.name
        if out.is_relative_to(root):
            raise ValueError("Save the private credential outside the realm backup directory")
        if out.exists() or out.is_symlink():
            raise FileExistsError("Choose an unused private profile filename")
        out.parent.mkdir(parents=True,exist_ok=True)
        temporary=out.parent/(".of-issue-"+uuid4().hex+".tmp")
        identity=LocalIdentity(identity_file)
        key="ofk_"+secrets.token_urlsafe(32)
        digest=_digest(key)
        committed=published=False
        try:
            with identity._write() as db:
                choice,display=_choose(db,_played(game),nickname,slot)
                db.execute("""CREATE TABLE IF NOT EXISTS issued_game_keys (
                    key_hash BLOB PRIMARY KEY CHECK(length(key_hash)=32),
                    slot TEXT NOT NULL REFERENCES profiles(slot) ON DELETE CASCADE) WITHOUT ROWID""")
                db.execute("INSERT INTO issued_game_keys(key_hash,slot) VALUES (?,?)",(digest,choice))
                record=dict(realm="overfield-local-v1",slot=choice,profile_key=key,nickname=display)
                with _private_file(temporary) as dest:
                    dest.write((json.dumps(record,ensure_ascii=False)+"\n").encode("utf8"))
                    dest.flush()
                    os.fsync(dest.fileno())
            committed=True
            publish_new_file(temporary,out)
            published=True
            fsync_directory(out.parent)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            finally:
                if committed and not published:
                    with identity._write() as db:
                        db.execute("DELETE FROM issued_game_keys WHERE key_hash=? AND slot=?",(digest,choice))
        print("Additional private game profile written to: "+str(out))
        return out
