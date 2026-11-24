"""Scoped, transactional personal save migration for a newly registered private-realm account."""
import hashlib
import json
import re
import secrets
import time

import save_import_identity as role_identity

TTL=600
TICKET=re.compile(r"ofi_[A-Za-z0-9_-]{43}\Z",re.ASCII)
PREVIEW=re.compile(r"ofv_[0-9a-f]{32}\Z",re.ASCII)
WARN="本服账号用于登录。可接续备份中的游戏 UID、角色名和已支持的个人进度。"
EQUIPMENT="装备在本服分配新实例。能匹配的穿戴预设将恢复，随机词条由本服生成。"
PRISTINE="请选择尚未进入游戏的新账号。已有游戏进度的角色不能导入。"
SEALED="进入游戏前可撤销；进入后将保留当前进度并关闭撤销。"
BLOBS={"characters":("character_id","character_blob"),"items":("item_id","item_detail_blob"),
       "items_s":("instance_id","item_detail_blob"),"quests":("quest_id","quest_blob"),
       "chapters":("chapter_id","chapter_blob"),"achieve":("achieve_id","achieve_blob")}


def error(status,code):
    from realm_accounts import AccountError
    raise AccountError(status,code)


def schema(conn):
    role_identity.schema(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS private_import_tickets(hash BLOB PRIMARY KEY,slot TEXT NOT NULL,expires INTEGER NOT NULL) WITHOUT ROWID")
    conn.execute("CREATE TABLE IF NOT EXISTS private_import_preview(slot TEXT PRIMARY KEY,id TEXT NOT NULL,plan TEXT NOT NULL,counts TEXT NOT NULL,expires INTEGER NOT NULL) WITHOUT ROWID")
    conn.execute("CREATE TABLE IF NOT EXISTS private_import_receipt(slot TEXT PRIMARY KEY,player INTEGER NOT NULL,id TEXT NOT NULL,counts TEXT NOT NULL,state TEXT NOT NULL,sealed INTEGER NOT NULL DEFAULT 0) WITHOUT ROWID")
    conn.execute("CREATE TABLE IF NOT EXISTS private_import_delta(slot TEXT NOT NULL,kind TEXT NOT NULL,k INTEGER NOT NULL,prior BLOB,applied BLOB,PRIMARY KEY(slot,kind,k)) WITHOUT ROWID")

    conn.execute("CREATE INDEX IF NOT EXISTS private_import_ticket_expiry ON private_import_tickets(expires)")
    conn.execute("CREATE INDEX IF NOT EXISTS private_import_preview_expiry ON private_import_preview(expires)")


def cleanup_expired(conn):
    now=int(time.time())
    conn.execute("DELETE FROM private_import_tickets WHERE expires<=?",(now,))
    conn.execute("DELETE FROM private_import_preview WHERE expires<=?",(now,))


def seal_native_entry(conn,slot):
    updated=conn.execute("UPDATE private_import_receipt SET sealed=1 WHERE slot=? AND sealed=0",(slot,))
    # A native session can transfer some equipment away. Never restore its old inventory afterwards.
    if updated.rowcount:
        conn.execute("DELETE FROM private_import_delta WHERE slot=?",(slot,))
        role_identity.forget(conn,slot)


def snapshot(conn,pid):
    result={}
    for table,(key,col) in BLOBS.items():
        for identifier,blob in conn.execute(f"SELECT {key},{col} FROM {table} WHERE player_id=?",(pid,)):
            result[table,identifier]=blob
    for table,sql in (("level","SELECT level,exp,world_level,character_id FROM players WHERE player_id=?"),
                      ("head","SELECT head FROM players WHERE player_id=?"),
                      ("location","SELECT scene,channel FROM private_locations WHERE player_id=?"),
                      ("intro","SELECT v FROM player_kv WHERE player_id=? AND ns='profile' AND k='bottle_intro'")):
        value=conn.execute(sql,(pid,)).fetchone()
        if value is not None:result[table,0]=json.dumps(value)
    for scene,pos,rot in conn.execute("SELECT scene,pos,rot FROM private_poses WHERE player_id=?",(pid,)):
        result["pose",scene]=len(pos).to_bytes(4,"big")+pos+rot
    area=conn.execute("SELECT area_blob FROM areas WHERE player_id=? AND scene_id=100 AND area_id=101",(pid,)).fetchone()
    if area:result["bottle_device",101]=area[0]
    result["team",0]=conn.execute("SELECT team FROM players WHERE player_id=?",(pid,)).fetchone()[0]
    return result


def restore_prior(conn,pid,table,key,value):
    if table=="pose":
        if value is None:
            conn.execute("DELETE FROM private_poses WHERE player_id=? AND scene=?",(pid,key))
        else:
            cut=4+int.from_bytes(value[:4],"big")
            conn.execute("INSERT OR REPLACE INTO private_poses VALUES(?,?,?,?)",(pid,key,value[4:cut],value[cut:]))
        return
    if table=="bottle_device" and key==101:
        if value is None:conn.execute("DELETE FROM areas WHERE player_id=? AND scene_id=100 AND area_id=101",(pid,))
        else:conn.execute("INSERT OR REPLACE INTO areas(player_id,scene_id,area_id,area_blob) VALUES(?,100,101,?)",(pid,value))
        return
    if table in BLOBS:
        identifier,col=BLOBS[table]
        if value is None:
            conn.execute(f"DELETE FROM {table} WHERE player_id=? AND {identifier}=?",(pid,key))
        else:
            conn.execute(f"INSERT OR REPLACE INTO {table}(player_id,{identifier},{col}) VALUES(?,?,?)",(pid,key,value))
        return
    if table=="team":
        conn.execute("UPDATE players SET team=? WHERE player_id=?",(value,pid))
        return
    values=json.loads(value)
    if table=="level":conn.execute("UPDATE players SET level=?,exp=?,world_level=?,character_id=? WHERE player_id=?",(*values,pid))
    elif table=="head":conn.execute("UPDATE players SET head=? WHERE player_id=?",(*values,pid))
    elif table=="location":conn.execute("UPDATE private_locations SET scene=?,channel=? WHERE player_id=?",(*values,pid))
    elif table=="intro":conn.execute("UPDATE player_kv SET v=? WHERE player_id=? AND ns='profile' AND k='bottle_intro'",(*values,pid))
    else:raise ValueError("invalid import journal")


def eligible(conn,slot):
    if not conn.execute("SELECT 1 FROM private_slots WHERE slot=?",(slot,)).fetchone():return True
    return bool(conn.execute("SELECT 1 FROM private_import_receipt WHERE slot=? AND state='undone' AND sealed=0",(slot,)).fetchone())


class ImportService:
    def __init__(self):
        self.game=None

    def issue(self,slot):
        from utils import db
        game=self.game
        if not game:return {}
        ticket="ofi_"+secrets.token_urlsafe(32)
        now=int(time.time())
        with game.lock:
            if game.closing.is_set() or db.conn is None:return {}
            with db.conn:
                db.conn.execute("INSERT INTO private_import_tickets VALUES(?,?,?)",(hashlib.sha256(ticket.encode()).digest(),slot,now+TTL))
        return {"import_ticket":ticket,"import_expires_in":TTL}

    def request(self,action,body):
        from utils import db
        from save_import_gameplay import validate,restore
        required={"import-preview":{"data"},"import-apply":{"preview_id"},"import-undo":set()}
        if action not in required or type(body) is not dict:error(400,"invalid_request")
        keys=set(body)
        if action=="import-apply":keys.discard("identity")
        if keys!=required[action]|{"import_ticket"}:error(400,"invalid_request")
        ticket=body["import_ticket"]
        if not isinstance(ticket,str) or not TICKET.fullmatch(ticket):error(401,"import_login_required")
        hash_key=hashlib.sha256(ticket.encode()).digest()
        game=self.game
        if not game:error(503,"import_service_unavailable")
        def authorized():
            if game.closing.is_set() or db.conn is None:error(503,"import_service_unavailable")
            account=db.conn.execute("""SELECT t.slot,p.nickname FROM private_import_tickets t
                     JOIN authority.realm_accounts a ON a.slot=t.slot JOIN authority.profiles p ON p.slot=a.slot
                     WHERE t.hash=? AND t.expires>?""",(hash_key,int(time.time()))).fetchone()
            if not account:error(401,"import_login_required")
            return account
        if action=="import-preview":
            with game.lock:authorized()
            try:
                if type(body["data"]) is not dict:raise ValueError("invalid import data")
                projected=dict(body["data"])
                source=role_identity.validate_source(projected.pop("identity",{}))
                plan,counts=validate(projected)
                if source:plan["identity"]=source
            except (ValueError,TypeError,KeyError):error(400,"invalid_import_data")
            serialized=json.dumps(plan,separators=(",",":"))
        with game.lock:
            slot,name=authorized()
            conn=db.conn
            with (conn if action=="import-preview" else role_identity.transaction(conn)):
                if action=="import-preview":
                    ready=eligible(conn,slot) and slot not in game.active
                    key="ofv_"+secrets.token_hex(16)
                    warnings=[WARN,SEALED]
                    from save_import_location import summary as resume_summary
                    warnings.append(resume_summary(plan))
                    if plan["gear"]:warnings.append(EQUIPMENT)
                    if not ready:warnings.append(PRISTINE)
                    conn.execute("DELETE FROM private_import_preview WHERE slot=?",(slot,))
                    if ready:
                        conn.execute("INSERT INTO private_import_preview VALUES(?,?,?,?,?)",(slot,key,serialized,json.dumps(counts),int(time.time())+TTL))
                    return {"ok":True,"preview_id":key,"eligible":ready,"counts":counts,"warnings":warnings,
                            "identity":role_identity.preview(conn,slot,source)}
                receipt=conn.execute("SELECT player,id,counts,state,sealed FROM private_import_receipt WHERE slot=?",(slot,)).fetchone()
                if action=="import-apply":
                    key=body["preview_id"]
                    if not isinstance(key,str) or not PREVIEW.fullmatch(key):error(400,"invalid_preview_id")
                    selected=role_identity.choices(body.get("identity",{}))
                    if receipt and receipt[1]==key and receipt[3]=="applied":
                        return {"ok":True,"counts":json.loads(receipt[2]),"identity":role_identity.display(conn,slot,receipt[0])}
                    if slot in game.active:error(409,"import_player_online")
                    saved=conn.execute("SELECT plan,counts FROM private_import_preview WHERE slot=? AND id=? AND expires>?",(slot,key,int(time.time()))).fetchone()
                    if not saved:error(409,"import_preview_expired")
                    if not eligible(conn,slot):error(409,"import_character_has_progress")
                    row=conn.execute("SELECT player_id FROM private_slots WHERE slot=?",(slot,)).fetchone()
                    pid,_=game._prepare_role(slot,name,row)
                    before=snapshot(conn,pid)
                    plan=json.loads(saved[0])
                    final_identity=role_identity.apply(conn,slot,pid,plan.pop("identity",{}),selected)
                    restore(pid,plan)
                    after=snapshot(conn,pid)
                    changes=[(slot,kind,ident,before.get((kind,ident)),after.get((kind,ident)))
                             for kind,ident in before.keys()|after.keys() if before.get((kind,ident))!=after.get((kind,ident))]
                    conn.execute("DELETE FROM private_import_delta WHERE slot=?",(slot,))
                    conn.executemany("INSERT INTO private_import_delta VALUES(?,?,?,?,?)",changes)
                    conn.execute("INSERT OR REPLACE INTO private_import_receipt VALUES(?,?,?,?,'applied',0)",(slot,pid,key,saved[1]))
                    conn.execute("DELETE FROM private_import_preview WHERE slot=?",(slot,))
                    return {"ok":True,"counts":json.loads(saved[1]),"identity":final_identity}
                if not receipt:error(409,"no_import_to_undo")
                if receipt[3]=="undone":return {"ok":True,"counts":json.loads(receipt[2]),"identity":role_identity.display(conn,slot,receipt[0])}
                if slot in game.active:error(409,"import_player_online")
                if receipt[4]:error(409,"import_progress_changed")
                pid=receipt[0]
                current=snapshot(conn,pid)
                changes=conn.execute("SELECT kind,k,prior,applied FROM private_import_delta WHERE slot=?",(slot,)).fetchall()
                if any(current.get((kind,key))!=applied for kind,key,_,applied in changes):error(409,"import_progress_changed")
                for kind,key,prior,_ in changes:restore_prior(conn,pid,kind,key,prior)
                # Older import receipts may predate this realm's permanent avatar collection.
                if any(kind=="items" and key in game.entitlements.head_stickers for kind,key,_,_ in changes):
                    from private_entitlements import fill_head_stickers
                    fill_head_stickers(conn,pid,game.entitlements.head_stickers)
                final_identity=role_identity.undo(conn,slot,pid)
                conn.execute("UPDATE private_import_receipt SET state='undone' WHERE slot=?",(slot,))
                conn.execute("DELETE FROM private_import_delta WHERE slot=?",(slot,))
                return {"ok":True,"counts":json.loads(receipt[2]),"identity":final_identity}
