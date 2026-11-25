"""Owner-published native announcements, image assets and verified realm releases."""
from collections import OrderedDict
from contextlib import closing, contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from tempfile import NamedTemporaryFile
from threading import Lock
from weakref import WeakValueDictionary
import time
import warnings
from state_lock import exclusive_state, is_link_or_reparse
import notice_images

DIGEST = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"(?:0|[1-9]\d{0,4})(?:\.(?:0|[1-9]\d{0,4})){2,3}",re.ASCII)
FILENAME = re.compile(r"[\w .()-]{1,85}\.(?:zip|7z|exe|tar\.gz)",re.IGNORECASE)
MAX_SIZE = 4*1024**3
# Twelve localized long notices, four images each and release notes can exceed
# 256 KiB when the HTTP encoder represents supplementary characters as surrogate pairs.
MAX_FEED = 320*1024
EMPTY = {"notices":[], "release":None}
PATH = "/private/releases/"
NATIVE_IMAGE_PATH = "/private-game"+notice_images.IMAGE_PATH
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS realm_notices (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, content TEXT NOT NULL, published INTEGER NOT NULL, active INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS realm_notice_assets (digest TEXT PRIMARY KEY, mime TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, data BLOB NOT NULL)",
    "CREATE TABLE IF NOT EXISTS realm_notice_images (notice_id INTEGER NOT NULL, position INTEGER NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(notice_id,position))",
    "CREATE INDEX IF NOT EXISTS realm_notice_images_by_digest ON realm_notice_images(digest)",
    "CREATE TABLE IF NOT EXISTS realm_release_visibility (id INTEGER PRIMARY KEY CHECK(id=1), visible INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS realm_release (id INTEGER PRIMARY KEY CHECK(id=1), version TEXT NOT NULL, sha256 TEXT NOT NULL, filename TEXT NOT NULL, size INTEGER NOT NULL, notes TEXT NOT NULL, published INTEGER NOT NULL)",
)


def plain(value, limit, multiline=False):
    if not isinstance(value, str):
        raise ValueError("Expected plain text")
    value=value.replace("\r\n","\n").strip()
    if not value or len(value)>limit or any((ord(c)<32 and not (multiline and c=="\n")) or c in "<>" or ord(c)==127 for c in value):
        raise ValueError("Invalid plain-text length or markup")
    return value


def filename(value):
    if not isinstance(value,str) or not FILENAME.fullmatch(value) or value.startswith(("."," ")) or value.split(".")[0].upper() in {"CON","PRN","AUX","NUL",*[f"{p}{i}" for p in ("COM","LPT") for i in range(1,10)]}:
        raise ValueError("Choose a portable package name ending in .zip, .7z, .exe or .tar.gz")
    return value


def checked_release(item):
    if not isinstance(item,dict) or not VERSION.fullmatch(item["version"]) or not DIGEST.fullmatch(item["sha256"]) or item["url"]!=PATH+item["sha256"]:
        raise ValueError("Untrusted release metadata")
    if type(item["size"])!=int or not 0<item["size"]<=MAX_SIZE or type(item["published"])!=int or item["published"]<=0:
        raise ValueError("Invalid release size or publication time")
    return dict(version=item["version"],sha256=item["sha256"],size=item["size"],url=item["url"],filename=filename(item["filename"]),notes=plain(item["notes"],1200,True),published=item["published"])


def checked_feed(raw):
    if not isinstance(raw,dict) or not isinstance(raw.get("notices"),list) or "release" not in raw or len(raw["notices"])>12:
        raise ValueError("Invalid private maintenance feed")
    values=[]
    for item in raw["notices"]:
        if not isinstance(item,dict) or type(item["id"])!=int or not 0<item["id"]<2**30 or type(item["published"])!=int or item["published"]<=0:
            raise ValueError("Invalid private announcement")
        values.append(dict(id=item["id"],published=item["published"],title=plain(item["title"],96),content=plain(item["content"],1600,True)))
        images=notice_images.checked_images(item.get("images",[]))
        if images:
            values[-1]["images"]=images
    return {"notices":values, "release":checked_release(raw["release"]) if raw["release"] is not None else None}


class _ReleaseVerification:
    def __init__(self):
        self.hash_lock = Lock()
        self.admin_reads = Lock()
        self.stamp = None
        self.matches_digest = False
        self.admin_owner = None
        self.database_stamp = None


# One last verified file per realm. Store neither package bytes nor an open descriptor.
_verifiers = OrderedDict()
_active_verifiers = WeakValueDictionary()
_verifier_lock = Lock()
_MAX_VERIFIERS = 32


def _realm_verifier(directory):
    with _verifier_lock:
        entry = _active_verifiers.get(directory)
        if entry is None:
            entry = _ReleaseVerification()
            _active_verifiers[directory] = entry
        _verifiers[directory] = entry
        _verifiers.move_to_end(directory)
        if len(_verifiers) > _MAX_VERIFIERS:
            _verifiers.popitem(last=False)
        return entry


if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    import msvcrt

    # Windows stat().st_ctime is creation time. Query NTFS change time on the
    # opened handle so an equal-size edit followed by restoring mtime is detected.
    class _FileBasicInfo(ctypes.Structure):
        _fields_ = [("creation", ctypes.c_longlong), ("access", ctypes.c_longlong),
                    ("write", ctypes.c_longlong), ("change", ctypes.c_longlong),
                    ("attributes", wintypes.DWORD)]

    _basic_info = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
    _basic_info.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(_FileBasicInfo), wintypes.DWORD)
    _basic_info.restype = wintypes.BOOL


def _file_stamp(stream, info, digest):
    if not info.st_ino:
        return None
    if os.name == "nt":
        basic = _FileBasicInfo()
        handle = msvcrt.get_osfhandle(stream.fileno())
        if not _basic_info(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)) or not basic.change:
            return None
        changed = basic.change
    else:
        changed = info.st_ctime_ns
    return (digest, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, changed)


def _database_stamp(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def admin_feed(state):
    from local_identity import LocalIdentity
    database = state/"identity.sqlite3"
    if not database.is_file():
        return {"notices": [], "release": None}
    verifier = _realm_verifier(state.resolve()/"release-files")
    # Readers in the same admin process take turns holding the existing
    # cross-process restore/publication lock instead of reporting a false conflict.
    with verifier.admin_reads, exclusive_state(state, purpose="profiles"):
        if verifier.admin_owner is None or _database_stamp(database) != verifier.database_stamp:
            owner = Maintenance(LocalIdentity(database))
            verifier.database_stamp = _database_stamp(database)  # initialization may migrate an old store
            verifier.admin_owner = owner
        return verifier.admin_owner.feed()



class Maintenance:
    def __init__(self, identity):
        self.dbfile=identity.path
        self.data=self.dbfile.parent
        self.blobs=self.data/"release-files"
        with closing(sqlite3.connect(self.dbfile)) as db, db:
            for sql in SCHEMA:db.execute(sql)
        self._migrate_jpeg_notices()

    def _migrate_jpeg_notices(self):
        with closing(sqlite3.connect(self.dbfile)) as db:
            # Decode without holding a write lock, and retain ordering and timestamps.
            digests = [r[0] for r in db.execute("SELECT digest FROM realm_notice_assets WHERE mime='image/jpeg'")]
            for digest in digests:
                row = db.execute("SELECT data FROM realm_notice_assets WHERE digest=?", (digest,)).fetchone()
                if row is None:
                    continue
                try:
                    desc, data = notice_images.publish_image(row[0])
                    if desc["mime"] != "image/png" or hashlib.sha256(row[0]).hexdigest() != digest:
                        raise ValueError("Stored notice image failed verification")
                except (OSError, ValueError) as exc:
                    # A broken legacy attachment must not take the game server offline.
                    warnings.warn(f"Could not migrate notice image {digest}: {exc}", RuntimeWarning)
                    continue
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    if not db.execute("SELECT 1 FROM realm_notice_images WHERE digest=? LIMIT 1", (digest,)).fetchone():
                        continue
                    db.execute("INSERT OR IGNORE INTO realm_notice_assets VALUES(?,?,?,?,?)",
                               (desc["sha256"], desc["mime"], desc["width"], desc["height"], data))
                    db.execute("UPDATE realm_notice_images SET digest=? WHERE digest=?", (desc["sha256"], digest))
                    db.execute("DELETE FROM realm_notice_assets WHERE digest=?", (digest,))

    def plaza(self):
        with closing(sqlite3.connect(self.dbfile)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='private_event_config'").fetchone():
                return {}
            row = db.execute("SELECT settings FROM private_event_config WHERE id=1").fetchone()
            from realm_blessings import client_plan
            return client_plan(json.loads(row[0])) if row else {}

    def stored_feed(self):
        with closing(sqlite3.connect(self.dbfile)) as db, db:
            db.row_factory=sqlite3.Row
            db.execute("BEGIN")
            notices=[dict(v) for v in db.execute("SELECT id,title,content,published FROM realm_notices WHERE active=1 ORDER BY id DESC LIMIT 12")]
            for n in notices:
                rows=db.execute("SELECT a.digest,a.mime,a.width,a.height,length(a.data) FROM realm_notice_images i "
                                "JOIN realm_notice_assets a ON a.digest=i.digest WHERE i.notice_id=? ORDER BY i.position",(n["id"],))
                images=[notice_images.metadata(*row) for row in rows]
                if images:
                    n["images"]=images
            release=db.execute("SELECT version,sha256,filename,size,notes,published FROM realm_release WHERE id=1").fetchone()
            shown=db.execute("SELECT visible FROM realm_release_visibility WHERE id=1").fetchone()
        r=dict(release) if release and (not shown or shown[0]) else None
        if r:r["url"]=PATH+r["sha256"]
        return {"notices":notices,"release":r}

    def feed(self):
        data=self.stored_feed()
        if data["release"]:
            try:
                self.verified_stream(data["release"]).close()
            except (ValueError,OSError):
                data["release"]=None
        return data

    @staticmethod
    def _set_images(db,number,images):
        db.execute("DELETE FROM realm_notice_images WHERE notice_id=?",(number,))
        for index,(desc,data) in enumerate(images):
            db.execute("INSERT OR IGNORE INTO realm_notice_assets VALUES(?,?,?,?,?)",
                       (desc["sha256"],desc["mime"],desc["width"],desc["height"],data))
            db.execute("INSERT INTO realm_notice_images VALUES(?,?,?)",(number,index,desc["sha256"]))
        db.execute("DELETE FROM realm_notice_assets WHERE NOT EXISTS "
                   "(SELECT 1 FROM realm_notice_images i WHERE i.digest=realm_notice_assets.digest)")

    def announce(self,title,body,images=()):
        title,body=plain(title,96),plain(body,1600,True)
        assets=notice_images.load_files(images)
        with closing(sqlite3.connect(self.dbfile)) as db, db:
            cur=db.execute("INSERT INTO realm_notices(title,content,published,active) VALUES(?,?,?,1)",(title,body,time.time_ns()//1_000_000))
            number=cur.lastrowid
            self._set_images(db,number,assets)
            return number

    def edit_notice(self,number,title=None,body=None,images=None):
        if title is None and body is None and images is None:
            raise ValueError("Specify a title, body or images")
        title=plain(title,96) if title is not None else None
        body=plain(body,1600,True) if body is not None else None
        assets=notice_images.load_files(images) if images is not None else None
        with closing(sqlite3.connect(self.dbfile)) as db, db:
            changed=db.execute("UPDATE realm_notices SET title=COALESCE(?,title),content=COALESCE(?,content),"
                               "published=max(?,published+1) WHERE id=? AND active=1",
                               (title,body,time.time_ns()//1_000_000,number))
            if changed.rowcount!=1:
                raise ValueError("Active notice not found")
            if assets is not None:
                self._set_images(db,number,assets)
        return number

    def open_photo(self, code, *, head=False):
        from photo_media import local_photo
        return local_photo(self.data,code,head=head)

    def open_image(self,digest):
        with closing(sqlite3.connect(self.dbfile)) as db:
            row=db.execute("SELECT a.mime,a.width,a.height,a.data FROM realm_notice_assets a WHERE a.digest=? AND EXISTS "
                           "(SELECT 1 FROM realm_notice_images i JOIN realm_notices n ON i.notice_id=n.id "
                           "WHERE i.digest=a.digest AND n.active=1)",(digest,)).fetchone()
        if row is None:
            raise FileNotFoundError
        mime,width,height,data=row
        return notice_images.metadata(digest,mime,width,height,len(data)),data

    def withdraw(self,number):
        with closing(sqlite3.connect(self.dbfile)) as db, db:
            if db.execute("UPDATE realm_notices SET active=0 WHERE id=? AND active=1",(number,)).rowcount!=1:
                raise ValueError("Active notice not found")
            self._set_images(db,number,())

    def publish(self,source,version,expected,notes):
        published=time.time_ns()//1_000_000
        item=checked_release(dict(version=version,sha256=expected,url=PATH+expected,filename=source.name,
                                  size=source.stat().st_size,notes=notes,published=published))
        self.blobs.mkdir(mode=0o700,exist_ok=True)
        if is_link_or_reparse(self.blobs.lstat()):raise ValueError("Release directory must be a regular directory")
        if not stat.S_ISREG(source.stat().st_mode):raise ValueError("Expected a regular package file")
        stage=None
        try:
            with source.open("rb") as src, NamedTemporaryFile(dir=self.blobs,prefix=".pending-",delete=False) as out:
                stage=Path(out.name); h=hashlib.sha256();size=0
                while block:=src.read(1024*1024):
                    size+=len(block)
                    if size>item["size"]:raise ValueError("Package changed during publication")
                    h.update(block);out.write(block)
                out.flush();os.fsync(out.fileno())
            if h.hexdigest()!=expected or size!=item["size"]:
                raise ValueError("Package failed verification against expected SHA-256")
            # Stream verification runs before taking a write lock on the identity database.
            with closing(sqlite3.connect(self.dbfile)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                old=db.execute("SELECT version,sha256 FROM realm_release WHERE id=1").fetchone()
                def numbers(v):return tuple(map(int,(v+".0").split(".")[:4]))
                if old and numbers(version)<=numbers(old[0]):
                    raise ValueError("Choose a strictly newer version")
                stage.replace(self.blobs/expected)
                db.execute("INSERT OR REPLACE INTO realm_release_visibility VALUES(1,1)")
                db.execute("INSERT OR REPLACE INTO realm_release VALUES(1,?,?,?,?,?,?)",(version,expected,item["filename"],size,item["notes"],published))
        finally:
            if stage:stage.unlink(missing_ok=True)
        if old and old[1]!=expected:
            try:(self.blobs/old[1]).unlink(missing_ok=True)
            except OSError:pass  # Windows may still have an active verified download.
        return item

    def withdraw_release(self):
        with closing(sqlite3.connect(self.dbfile)) as db, db:
            if not db.execute("SELECT 1 FROM realm_release WHERE id=1").fetchone():
                raise ValueError("No published version")
            db.execute("INSERT OR REPLACE INTO realm_release_visibility VALUES(1,0)")

    def open_release(self,digest):
        r=self.stored_feed()["release"]
        if not r or r["sha256"]!=digest or not DIGEST.fullmatch(digest):raise FileNotFoundError
        return self.verified_stream(r),r

    def verified_stream(self,r):
        digest=r["sha256"]
        if not DIGEST.fullmatch(digest):raise ValueError("Invalid stored digest")
        if is_link_or_reparse(self.blobs.lstat()):raise ValueError("Release directory changed")
        path=self.blobs/digest
        if is_link_or_reparse(path.lstat()) or not stat.S_ISREG(path.stat().st_mode):raise ValueError("Release file changed")
        stream=path.open("rb")
        try:
            verifier = _realm_verifier(self.blobs)
            with verifier.hash_lock:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != r["size"]:
                    raise ValueError("Published file failed size verification")
                stamp = _file_stamp(stream, info, digest)
                if stamp is None or verifier.stamp != stamp:
                    matches = hashlib.file_digest(stream, "sha256").hexdigest() == digest
                    current = os.fstat(stream.fileno())
                    if current.st_size != r["size"] or _file_stamp(stream, current, digest) != stamp:
                        raise ValueError("Published file changed during verification")
                    stream.seek(0)
                    verifier.stamp, verifier.matches_digest = stamp, matches
                if not verifier.matches_digest:
                    raise ValueError("Published file failed SHA-256 verification")
        except BaseException:
            stream.close();raise
        return stream


class SelectedFeed:
    """Read only the selected realm's bounded public JSON through its existing fixed SSH endpoint."""
    def __init__(self, remote):
        self.remote=remote

    def feed(self):
        with self.remote.channels.open(self.remote.http_port) as channel:
            conn=http.client.HTTPConnection("127.0.0.1",self.remote.http_port,timeout=14)
            conn.sock=channel
            try:
                conn.request("GET","/private/maintenance",headers={"Connection":"close"})
                r=conn.getresponse()
                if r.status!=200:raise ValueError("Selected realm maintenance unavailable")
                data=r.read(MAX_FEED+1)
            finally:conn.close()
        if len(data)>MAX_FEED:raise ValueError("Public maintenance data too large")
        return checked_feed(json.loads(data))

    def plaza(self):
        from realm_blessings import validate_plan
        with self.remote.channels.open(self.remote.http_port) as channel:
            conn = http.client.HTTPConnection("127.0.0.1", self.remote.http_port, timeout=14)
            conn.sock = channel
            try:
                conn.request("GET", "/private/plaza", headers={"Connection": "close"})
                response = conn.getresponse()
                if response.status == 404:
                    return {}
                if response.status != 200:
                    raise ValueError("Selected realm plaza schedule unavailable")
                data = response.read(16385)
                if len(data) > 16384:
                    raise ValueError("Invalid plaza schedule size")
                return validate_plan(json.loads(data))
            finally:
                conn.close()

    def open_photo(self, code, *, head=False):
        from photo_media import forwarded_photo
        return forwarded_photo(self.remote,code,head=head)

    def open_image(self,digest):
        if not DIGEST.fullmatch(digest):
            raise FileNotFoundError
        with self.remote.channels.open(self.remote.http_port) as channel:
            conn=http.client.HTTPConnection("127.0.0.1",self.remote.http_port,timeout=14)
            conn.sock=channel
            try:
                conn.request("GET",notice_images.IMAGE_PATH+digest,headers={"Connection":"close"})
                return notice_images.read_response(conn.getresponse(),digest)
            finally:
                conn.close()


def native_notices(feed,origin=""):
    # Unity selects a named image widget by this height. The PC notice prefab has three sizes.
    image_heights=(232,432,732)
    entries=list(feed["notices"])
    release=feed["release"]
    if release:
        entries.insert(0,dict(id=0x40000000+(int(release["sha256"][:7],16)),title="私服版本 "+release["version"],
                              content=release["notes"]+"\n可在私服启动器的区服公告中查看更新包。",published=release["published"]))
    result=[]
    for e in entries:
        def block(order,text="",image=None):
            return dict(orderId=order,title="",subtitle="",text=text,imageUrl=(origin+"/private-game"+image["url"] if image else ""),
                        imageHeight=(min(image_heights,key=lambda height:abs(height-image["height"])) if image else 0),
                        jumpType=0,jumpValue="",inGameJumpType=0,inGameJumpValue=0)
        blocks=[block(1,e["content"])]
        blocks.extend(block(i,image=image) for i,image in enumerate(e.get("images",[]),2))
        result.append(dict(oriNoticeId=e["id"],noticeId=e["id"],type=0,isMaintenance=False,title=e["title"],
                           content=blocks,
                           startTime=e["published"]-1,endTime=4102444799000,imageUrl="",isPopup=False,lastUpdTime=e["published"],
                           orderId=e["id"],priority=False,beforeLoginPopup=True,afterLoginPopup=True,jumpType=0,jumpValue="",
                           imageHeight=0,condition=[],refreshRedPoint=True,todayOnceNotice=False,inGameJumpType=0,inGameJumpValue=0))
    return result


def public_get(handler):
    from photo_media import serve as serve_photo
    if serve_photo(handler):
        return True
    route=handler.path
    if route not in ("/private/plaza", "/private/maintenance") and not route.startswith((PATH,notice_images.IMAGE_PATH,NATIVE_IMAGE_PATH)):return False
    if any(key in handler.headers for key in ("Cookie","Authorization","Proxy-Authorization")):
        handler._reply(403,{"error":"public maintenance does not take credentials"});return True
    try:
        if route == "/private/plaza":
            handler._reply(200, handler.server.maintenance.plaza()); return True
        if route=="/private/maintenance":
            handler._reply(200,handler.server.maintenance.feed());return True
        if route.startswith((notice_images.IMAGE_PATH,NATIVE_IMAGE_PATH)):
            is_native=route.startswith(NATIVE_IMAGE_PATH)
            digest=route[len(NATIVE_IMAGE_PATH if is_native else notice_images.IMAGE_PATH):]
            if not DIGEST.fullmatch(digest):raise FileNotFoundError
            if is_native:
                source=getattr(getattr(handler.server,"game_dispatch",None),"maintenance",None)
                if source is None:raise FileNotFoundError
            else:
                source=handler.server.maintenance
            desc,data=source.open_image(digest)
            handler.send_response(200)
            handler.send_header("Content-Type",desc["mime"])
            handler.send_header("Content-Length",str(len(data)))
            handler.send_header("Cache-Control","no-store")
            handler.send_header("X-Content-Type-Options","nosniff")
            handler.end_headers()
            if handler.command!="HEAD":
                handler.wfile.write(data)
            return True
        digest=route[len(PATH):]
        if not DIGEST.fullmatch(digest):raise FileNotFoundError
        stream,r=handler.server.maintenance.open_release(digest)
        with stream:
            handler.send_response(200)
            handler.send_header("Content-Type","application/octet-stream")
            handler.send_header("Content-Length",str(r["size"]))
            handler.send_header("Cache-Control","no-store")
            handler.send_header("X-Content-Type-Options","nosniff")
            handler.send_header("Content-Disposition","attachment")
            handler.send_header("Connection","close")
            handler.end_headers()
            if handler.command!="HEAD":
                while block:=stream.read(1024*1024):handler.wfile.write(block)
        handler.close_connection=True
    except FileNotFoundError:handler._reply(404,{"error":"asset not found"})
    except (ConnectionResetError, BrokenPipeError):handler.close_connection=True
    except (OSError,ValueError,sqlite3.Error,http.client.HTTPException):handler._reply(503,{"error":"asset unavailable"})
    return True


def arguments(commands):
    cmd=commands.add_parser("announce")
    cmd.add_argument("--title",required=True);cmd.add_argument("--body-file",required=True,type=Path)
    cmd.add_argument("--image",type=Path,action="append",default=[],help="PNG/JPEG, repeat for up to four images")
    cmd=commands.add_parser("edit-notice")
    cmd.add_argument("--id",type=int,required=True)
    cmd.add_argument("--title");cmd.add_argument("--body-file",type=Path)
    image_args=cmd.add_mutually_exclusive_group()
    image_args.add_argument("--image",type=Path,action="append",help="replace images in supplied order")
    image_args.add_argument("--clear-images",action="store_true")
    cmd=commands.add_parser("withdraw-notice");cmd.add_argument("--id",type=int,required=True)
    cmd=commands.add_parser("publish-release")
    cmd.add_argument("--version",required=True);cmd.add_argument("--file",required=True,type=Path)
    cmd.add_argument("--sha256",required=True);cmd.add_argument("--notes-file",required=True,type=Path)
    commands.add_parser("maintenance-info")
    commands.add_parser("withdraw-release")


def owner_command(state,a):
    from local_identity import LocalIdentity
    def read(path,limit):
        if path.stat().st_size>limit*4+4:raise ValueError("Text file too large")
        return path.read_text("utf-8-sig")
    with exclusive_state(state,purpose="profiles"):
        state.mkdir(parents=True,exist_ok=True)
        source=Maintenance(LocalIdentity(state/"identity.sqlite3"))
        if a.command=="announce":
            n=source.announce(a.title,read(a.body_file,1600),a.image);print("Published notice",n)
        elif a.command=="edit-notice":
            source.edit_notice(a.id,a.title,read(a.body_file,1600) if a.body_file else None,[] if a.clear_images else a.image)
            print("Updated notice",a.id)
        elif a.command=="withdraw-notice":
            source.withdraw(a.id);print("Notice withdrawn")
        elif a.command=="withdraw-release":
            source.withdraw_release();print("Release withdrawn")
        elif a.command=="publish-release":
            r=source.publish(a.file,a.version,a.sha256,read(a.notes_file,1200));print("Published",r["version"],r["sha256"])
        else:print(json.dumps(source.feed(),ensure_ascii=False,indent=2))

