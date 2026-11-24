"""Adopt optional public role attributes for one authenticated, unplayed local slot."""
from contextlib import contextmanager
import json
import sqlite3

from local_identity import _name
from social_query import name_key
import realm_uids


def reject(code,status=409):
    from realm_accounts import AccountError
    raise AccountError(status,code)


def schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS private_import_identity(
        slot TEXT PRIMARY KEY, prior TEXT NOT NULL, applied TEXT NOT NULL) WITHOUT ROWID""")


def validate_source(value):
    if type(value) is not dict or set(value)-{"uid","nickname"}:
        raise ValueError("Invalid role identity")
    if "uid" in value and (type(value["uid"]) is not int or not 0<=value["uid"]<=0xffffffff):
        raise ValueError("Invalid source UID")
    if "nickname" in value and (not isinstance(value["nickname"],str) or len(value["nickname"])>64):
        raise ValueError("Invalid source nickname")
    return value


def choices(value):
    if type(value) is not dict or set(value)-{"uid_mode","nickname_mode","nickname"}:
        reject("invalid_import_identity",400)
    if "uid_mode" in value and value["uid_mode"] not in ("original","new"):
        reject("invalid_import_identity",400)
    if "nickname_mode" in value and value["nickname_mode"] not in ("original","custom","local"):
        reject("invalid_import_identity",400)
    if value.get("nickname_mode")=="custom":
        try:
            _name(value.get("nickname"))
        except ValueError:
            reject("invalid_nickname",400)
    elif "nickname" in value:
        reject("invalid_import_identity",400)
    return value


def uid_status(conn,pid,uid):
    if uid is None:
        return "missing"
    if not realm_uids.FIRST<=uid<realm_uids.INVALID:
        return "invalid"
    if conn.execute("""SELECT 1 FROM players WHERE player_id=? AND player_id!=?
            UNION ALL SELECT 1 FROM private_slots WHERE player_id=? AND player_id!=?
            UNION ALL SELECT 1 FROM realm_public_uids WHERE uid=? AND player_id!=?
            UNION ALL SELECT 1 FROM private_import_uid_reservations WHERE uid=? AND player_id!=? LIMIT 1""",
            (uid,pid,uid,pid,uid,pid,uid,pid)).fetchone():
        return "taken"
    return "available"


def nickname_status(conn,slot,pid,name):
    if name is None:
        return "missing"
    try:
        _,key=_name(name)
    except ValueError:
        return "invalid"
    reserved=conn.execute("""SELECT 1 FROM authority.profiles WHERE name_key=? AND slot!=?
        UNION ALL SELECT 1 FROM authority.realm_import_name_reservations WHERE name_key=? AND slot!=?
        LIMIT 1""",(key,slot,key,slot)).fetchone()
    if reserved:
        return "taken"
    if conn.execute("SELECT 1 FROM players WHERE private_name_key(player_name)=? AND player_id!=? LIMIT 1",
                    (name_key(name),pid)).fetchone():
        return "taken"
    return "available"


def preview(conn,slot,source):
    row=conn.execute("SELECT player_id FROM private_slots WHERE slot=?",(slot,)).fetchone()
    pid=row[0] if row else 0
    uid,name=source.get("uid"),source.get("nickname")
    status=nickname_status(conn,slot,pid,name)
    return {"uid":uid,"nickname":_name(name)[0] if status in ("available","taken") else "",
            "uid_status":uid_status(conn,pid,uid),"nickname_status":status}


def state(conn,slot,pid):
    name,key=conn.execute("SELECT nickname,name_key FROM authority.profiles WHERE slot=?",(slot,)).fetchone()
    player=conn.execute("SELECT player_name FROM players WHERE player_id=?",(pid,)).fetchone()[0]
    alias=conn.execute("SELECT uid FROM realm_public_uids WHERE player_id=?",(pid,)).fetchone()
    pending=conn.execute("SELECT 1 FROM authority.realm_pending_names WHERE slot=?",(slot,)).fetchone()
    named=conn.execute("SELECT 1 FROM authority.realm_named_profiles WHERE slot=?",(slot,)).fetchone()
    return {"alias":alias[0] if alias else None,"nickname":name,"key":key,"player_name":player,
            "pending":bool(pending),"named":bool(named)}


def display(conn,slot,pid):
    current=state(conn,slot,pid)
    return {"uid":current["alias"] or pid,"nickname":"" if current["pending"] else current["nickname"]}


def save_state(conn,slot,pid,target):
    conn.execute("DELETE FROM realm_public_uids WHERE player_id=?",(pid,))
    if target["alias"] is not None:
        conn.execute("INSERT INTO realm_public_uids(player_id,uid) VALUES(?,?)",(pid,target["alias"]))
    conn.execute("UPDATE authority.profiles SET nickname=?,name_key=? WHERE slot=?",
                 (target["nickname"],target["key"],slot))
    conn.execute("UPDATE players SET player_name=? WHERE player_id=?",(target["player_name"],pid))
    for table,column in (("realm_pending_names","pending"),("realm_named_profiles","named")):
        if target[column]:
            conn.execute(f"INSERT OR IGNORE INTO authority.{table}(slot) VALUES(?)",(slot,))
        else:
            conn.execute(f"DELETE FROM authority.{table} WHERE slot=?",(slot,))


def apply(conn,slot,pid,source,selected):
    previous=state(conn,slot,pid)
    result=dict(previous)
    uid_mode=selected.get("uid_mode","original" if "uid" in source else "new")
    if uid_mode=="original":
        uid=source.get("uid")
        if uid_status(conn,pid,uid)!="available":
            reject("import_uid_unavailable")
        result["alias"]=uid if uid!=pid else None
    # New/local retains the public ID selected by the realm; historical public mappings stay intact.
    name_mode=selected.get("nickname_mode","original" if "nickname" in source else "local")
    if name_mode!="local":
        name=selected.get("nickname") if name_mode=="custom" else source.get("nickname")
        if nickname_status(conn,slot,pid,name)!="available":
            reject("import_nickname_unavailable")
        name,key=_name(name)
        result.update(nickname=name,key=key,player_name=name,named=True,pending=False)
    if result!=previous:
        if previous["alias"] is not None and previous["alias"]!=result["alias"]:
            conn.execute("INSERT INTO private_import_uid_reservations(uid,player_id) VALUES(?,?)",
                         (previous["alias"],pid))
        if previous["key"]!=result["key"]:
            conn.execute("INSERT INTO authority.realm_import_name_reservations(name_key,slot) VALUES(?,?)",
                         (previous["key"],slot))
        try:
            save_state(conn,slot,pid,result)
        except sqlite3.IntegrityError:
            reject("import_identity_conflict")
        conn.execute("INSERT OR REPLACE INTO private_import_identity VALUES(?,?,?)",
                     (slot,json.dumps(previous),json.dumps(result)))
    return display(conn,slot,pid)


def undo(conn,slot,pid):
    row=conn.execute("SELECT prior,applied FROM private_import_identity WHERE slot=?",(slot,)).fetchone()
    if row:
        if state(conn,slot,pid)!=json.loads(row[1]):
            reject("import_progress_changed")
        save_state(conn,slot,pid,json.loads(row[0]))
        forget(conn,slot)
    return display(conn,slot,pid)


def forget(conn,slot):
    conn.execute("""DELETE FROM private_import_uid_reservations
                 WHERE player_id=(SELECT player_id FROM private_slots WHERE slot=?)""",(slot,))
    conn.execute("DELETE FROM private_import_identity WHERE slot=?",(slot,))
    conn.execute("DELETE FROM authority.realm_import_name_reservations WHERE slot=?",(slot,))


@contextmanager
def transaction(conn):
    with conn:
        # Registration also reserves names in authority. Acquire the writer before checking either database.
        conn.execute("UPDATE authority.profiles SET nickname=nickname WHERE 0")
        yield
    realm_uids.refresh(conn)
