"""Native player-published photo wall. All metadata, interactions and media share the realm transaction."""
from datetime import timedelta
from functools import partial
import hashlib

import photo_media
import realm_plaza
from private_appearance import WEAR_SLOTS, publish
from private_chat_text import chat_text
from utils.res_loader import res
from private_gameplay_transaction import Rejected, run
from private_outfit_dye import outfit, store_outfit, worn
from private_shop import now
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_ScenePlayer


def setup(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS realm_photos(
            code INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER NOT NULL, activity INTEGER NOT NULL,
            posted INTEGER NOT NULL, name TEXT NOT NULL, title TEXT NOT NULL, tag1 TEXT NOT NULL,
            tag2 TEXT NOT NULL, tag3 TEXT NOT NULL, snapshot BLOB NOT NULL, fingerprint BLOB NOT NULL,
            likes INTEGER NOT NULL DEFAULT 0, saves INTEGER NOT NULL DEFAULT 0, downloads INTEGER NOT NULL DEFAULT 0,
            UNIQUE(owner,fingerprint));
        CREATE INDEX IF NOT EXISTS photos_owner ON realm_photos(owner,activity,posted DESC);
        CREATE INDEX IF NOT EXISTS photos_feed ON realm_photos(activity,posted DESC,code DESC);
        CREATE INDEX IF NOT EXISTS photos_rank ON realm_photos(activity,likes DESC,posted DESC);
        CREATE TABLE IF NOT EXISTS realm_photo_media(code INTEGER PRIMARY KEY REFERENCES realm_photos(code) ON DELETE CASCADE,
            file BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS realm_photo_marks(code INTEGER NOT NULL REFERENCES realm_photos(code) ON DELETE CASCADE,
            player INTEGER NOT NULL, liked INTEGER NOT NULL DEFAULT 0, saved INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(code,player));
        CREATE INDEX IF NOT EXISTS photo_favorites ON realm_photo_marks(player,saved,code);
        CREATE TABLE IF NOT EXISTS realm_photo_quotas(player INTEGER PRIMARY KEY,extra INTEGER DEFAULT 0,
            day TEXT NOT NULL DEFAULT '', used INTEGER NOT NULL DEFAULT 0);
    """)


def configs(key):
    return res.index("GlobalConfig","config","key")["PhotoWall_"+key]["values"]


def limit(key):
    return int(configs(key)[0])


def pay(write, key):
    currency, cost = map(int, configs(key))
    write.balances.change(currency, -cost)


def day():
    return (now() - timedelta(hours=5)).date().isoformat()


def quota(player):
    row = db.conn.execute("SELECT extra,day,used FROM realm_photo_quotas WHERE player=?", (player,)).fetchone()
    extra, previous, used = row or (0,"",0)
    # MY_SHARE derives its number of pages from Share_Num. Each normal page displays eight tiles.
    return limit("Share_Num") + extra, max(0, limit("Daily_Limit") - (used if day() == previous else 0))


def clean(session, value, size):
    result = chat_text(value)
    if result is None or len(result.encode("utf-16-le")) // 2 > size or not session.chat_guard.allows(result):
        raise Rejected(pb.StatusCode_BAD_REQ)
    return result


def text_fields(s, r):
    title = clean(s,r.title,limit("Title_Length"))
    if not title:
        raise Rejected(pb.StatusCode_BAD_REQ)
    return (title,clean(s,r.tag1,limit("Label_Length")),
            clean(s,r.tag2,limit("Label_Length")))


def active_show(uploading=False):
    from private_events import _decoded, _overrides, current, raw_settings
    instant = now()
    raw = raw_settings()
    plaza = _decoded(raw).get("plaza")
    in_plaza = realm_plaza.active(plaza,instant)
    overrides = _overrides(raw,in_plaza)
    shows = res.index("ActivityFashionShow","activity_fashion_show")
    for row in res.index("Activity","activity").values():
        if row.get("new_activity_type") != 9 or row.get("activity_i_d") not in shows:
            continue
        selected = current(row,instant,overrides)
        if selected:
            aid = row["i_d"]
            effective = shows[row["activity_i_d"]]["start_day"]
            if in_plaza and aid in plaza["activities"]:
                effective = realm_plaza.photo_deadline(aid,selected["opening_time"],selected["closing_time"])
            if uploading and instant.strftime("%Y-%m-%d %H:%M:%S") > effective:
                raise Rejected(pb.StatusCode_PS_ACTIVITY_UPLOAD_END)
            return aid
    return 0


DETAIL = """SELECT p.snapshot,p.code,p.activity,p.title,p.tag1,p.tag2,p.tag3,p.likes,p.saves,p.downloads,
            COALESCE(m.liked,0),COALESCE(m.saved,0) FROM realm_photos p
            LEFT JOIN realm_photo_marks m ON m.code=p.code AND m.player=? WHERE p.code=?"""


def detail_for(player, code):
    row = db.conn.execute(DETAIL,(player,code)).fetchone()
    if not row:
        raise Rejected(pb.StatusCode_PS_NOT_EXIST)
    data = pb.PhotoDetailInfo.FromString(row[0])
    (data.code,data.activity_id,data.title,data.tag1,data.tag2,data.tag3,data.likes_total,
     data.favorite_total,data.dye_download_total) = row[1:10]
    data.likes_status, data.favorite_status = bool(row[10]), bool(row[11])
    return data


def detail(session, req, rsp):
    try:
        info = detail_for(session.player_id,req.code)
    except Rejected as e:
        return e.status
    rsp.info.CopyFrom(info)
    return pb.StatusCode_OK


def snapshot(s, req, posted):
    make_ScenePlayer(s)
    body = s.scene_player.team.char1
    head, = db.get_players_info(s.player_id, "head")
    result = pb.PhotoDetailInfo(player_id=s.player_id,player_label=s.player_id,player_name=s.player_name,
        head=head,badge=s.badge_id,upload_time=posted//1000,character_id=body.char_id,
        weapon_id=body.weapon_id,status=pb.AuditStatus_SUCCESS)
    if req.HasField("camera_param"):
        result.camera_param.CopyFrom(req.camera_param)
    for slot in WEAR_SLOTS:
        key = getattr(body.outfit_preset, slot)
        if key:
            result.outfit_ids.append(key)
            prefix = {"clothes":"cloth","ornament":"orn"}.get(slot,slot)
            result.outfits.add(outfit_id=key).dye_schemes.CopyFrom(getattr(body.outfit_preset, prefix+"_dye_scheme"))
    return result


def upload(s, req, rsp, write):
    title, t1, t2 = text_fields(s,req)
    t3 = clean(s,req.tag3,limit("Label_Length"))
    try:
        photo_media.dimensions(req.file)
    except ValueError:
        return pb.StatusCode_BAD_REQ
    activity = active_show(uploading=True) if req.is_activity else 0
    if req.is_activity and not activity:
        return pb.StatusCode_PS_ACTIVITY_NOT_OPEN
    fingerprint = hashlib.sha256(req.SerializeToString(deterministic=True) + activity.to_bytes(4,"big")).digest()
    player = s.player_id
    prior = db.conn.execute("SELECT code FROM realm_photos WHERE owner=? AND fingerprint=?",(player,fingerprint)).fetchone()
    if prior:
        rsp.info.CopyFrom(detail_for(player,prior[0]))
        return pb.StatusCode_OK
    maximum, remaining = quota(player)
    if remaining <= 0:
        return pb.StatusCode_PS_TODAY_UPLOAD_LIMIT
    count, = db.conn.execute("SELECT COUNT(*) FROM realm_photos WHERE owner=? AND activity=?",(player,activity)).fetchone()
    if count >= (limit("ActivityPhoto_MaxNum") if activity else maximum):
        return pb.StatusCode_PS_ACTIVITY_UPLOAD_LIMIT if activity else pb.StatusCode_PS_UPLOAD_LIMIT
    stamp = int(now().timestamp() * 1000)
    info = snapshot(s, req, stamp)
    code = db.conn.execute("""INSERT INTO realm_photos(owner,activity,posted,name,title,tag1,tag2,tag3,snapshot,fingerprint)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",(player,activity,stamp,s.player_name,title,t1,t2,t3,
                    info.SerializeToString(),fingerprint)).lastrowid
    db.conn.execute("INSERT INTO realm_photo_media(code,file) VALUES(?,?)",(code,req.file))
    db.conn.execute("""INSERT INTO realm_photo_quotas(player,day,used) VALUES(?,?,1)
        ON CONFLICT(player) DO UPDATE SET day=excluded.day,used=CASE WHEN day=excluded.day THEN used+1 ELSE 1 END""",
                    (player,day()))
    rsp.info.CopyFrom(detail_for(player,code))
    return pb.StatusCode_OK


def rename(s, req, rsp, write):
    info = detail_for(s.player_id,req.code)
    if info.player_id != s.player_id:
        return pb.StatusCode_FAIL
    labels = text_fields(s,req)
    if labels != (info.title,info.tag1,info.tag2):
        pay(write, "ChangeTitle_Currency")
        db.conn.execute("UPDATE realm_photos SET title=?,tag1=?,tag2=? WHERE code=?",(*labels,req.code))
    rsp.title,rsp.tag1,rsp.tag2 = labels
    return pb.StatusCode_OK


def search(s, req, rsp):
    player, tab = s.player_id, req.search_type
    if tab not in pb.PhotoSearchType.values() or req.text_type not in pb.PhotoSearchTextType.values():
        return pb.StatusCode_BAD_REQ
    where, args = [], []
    pinned = []
    order = "p.posted DESC,p.code DESC"
    daily = tab == pb.PhotoSearchType_TODAY
    page_cap = limit("Rank_MaxNum") if tab in (pb.PhotoSearchType_TOTAL,pb.PhotoSearchType_TODAY,
                                              pb.PhotoSearchType_Activity_Rank) else limit("Find_MaxNum")
    is_event = tab in (pb.PhotoSearchType_Activity,pb.PhotoSearchType_Activity_Rank)
    if is_event:
        where.append("p.activity=?")
        args.append(active_show() or -1)
        if tab == pb.PhotoSearchType_Activity_Rank:
            weights = [limit("Activity_"+s+"Score") for s in ("Like","Save","Download")]
            order = "(p.likes*%d+p.saves*%d+p.downloads*%d) DESC," % tuple(weights) + order
    elif tab == pb.PhotoSearchType_MY_FAVORITE:
        page_cap = limit("Collect_MaxNum")
        where.append("m.saved=1")
    elif tab == pb.PhotoSearchType_MY_SHARE:
        page_cap = -1
        where.append("p.owner=?")
        args.append(player)
        # The first MY_SHARE page reserves tiles for competition entries. On later
        # pages the client subtracts those tiles from the requested regular-photo offset.
        pinned = [row[0] for row in db.conn.execute("SELECT code FROM realm_photos WHERE owner=? AND activity<>0"
                  " ORDER BY posted DESC,code DESC LIMIT ?", (player,limit("ActivityPhoto_MaxNum")))]
        if pinned:
            where.append("p.code NOT IN (" + ",".join("?" for _ in pinned) + ")")
            args.extend(pinned)
    else:
        where.append("p.activity=0")
    if daily:
        today = (now()-timedelta(hours=5)).replace(hour=5,minute=0,second=0,microsecond=0)
        where.append("p.posted>=?")
        args.append(int(today.timestamp()*1000))
    if daily or tab == pb.PhotoSearchType_TOTAL:
        order = "p.likes DESC," + order
    if tab in (pb.PhotoSearchType_SEARCH,pb.PhotoSearchType_SEARCH_PAGE):
        if req.text_type in (pb.PhotoSearchTextType_UID,pb.PhotoSearchTextType_CODE):
            if not req.text.isascii() or not req.text.isdigit() or len(req.text)>19 or int(req.text)>2**63-1:
                return pb.StatusCode_PS_SEARCH_FAIL
            number = int(req.text)
            if req.text_type == pb.PhotoSearchTextType_UID:
                from realm_uids import local_uid
                number = local_uid(number)
            where.append("p.owner=?" if req.text_type==pb.PhotoSearchTextType_UID else "p.code=?")
            args.append(number)
        else:
            if not req.text or len(req.text)>limit("Title_Length"):
                return pb.StatusCode_PS_SEARCH_FAIL
            where.append("(" + " OR ".join("instr(p."+column+",?)>0" for column in ("title","tag1","tag2","tag3","name")) + ")")
            args.extend([req.text]*5)
    source = """ FROM realm_photos p LEFT JOIN realm_photo_marks m ON m.code=p.code AND m.player=? WHERE """+" AND ".join(where)
    params = [player,*args]
    if req.end_index and req.end_index < req.start_index:
        return pb.StatusCode_BAD_REQ
    regular = db.conn.execute("SELECT COUNT(*) FROM (SELECT 1"+source+" LIMIT ?)", (*params,page_cap)).fetchone()[0]
    rsp.total_num = regular + len(pinned)
    rsp.upload_max_num, rsp.today_upload_limit = quota(player)
    start = min(req.start_index,regular)
    requested_end = req.end_index or start + 8
    if pinned and not req.start_index:
        for code in pinned[:requested_end]:
            info = detail_for(player,code)
            rsp.activity_photos.add(code=code,player_id=info.player_id,player_label=info.player_label,
                player_name=info.player_name,head=info.head,activity_id=info.activity_id,title=info.title,
                likes_total=info.likes_total,likes_status=info.likes_status,status=pb.AuditStatus_SUCCESS)
        requested_end -= len(rsp.activity_photos)
    end = min(requested_end,start+limit("Find_MaxNum"),regular)
    result = rsp.activity_photos if is_event else rsp.photos
    sql = """SELECT p.code,p.owner,p.activity,p.title,p.likes,COALESCE(m.liked,0),p.snapshot"""+source+" ORDER BY "+order+" LIMIT ? OFFSET ?"
    for code,owner,activity,title,likes,liked,blob in db.conn.execute(sql,(*params,end-start,start)):
        summary = pb.PhotoDetailInfo.FromString(blob)
        result.add(code=code,player_id=owner,player_label=owner,player_name=summary.player_name,head=summary.head,
                   activity_id=activity,title=title,likes_total=likes,likes_status=bool(liked),status=pb.AuditStatus_SUCCESS)
    rsp.end_index = end
    return pb.StatusCode_OK


def operate(s, req, rsp, write):
    player = s.player_id
    photo = detail_for(player,req.code)
    if req.activity_id and req.activity_id != photo.activity_id:
        return pb.StatusCode_BAD_REQ
    op = req.operate_type
    if op in (pb.PhotoOperateType_DEL,pb.PhotoOperateType_MOVE):
        if player != photo.player_id:
            return pb.StatusCode_FAIL
        if op == pb.PhotoOperateType_DEL:
            db.conn.execute("DELETE FROM realm_photo_marks WHERE code=?",(req.code,))
            db.conn.execute("DELETE FROM realm_photo_media WHERE code=?",(req.code,))
            db.conn.execute("DELETE FROM realm_photos WHERE code=?",(req.code,))
        else:
            if not photo.activity_id:
                return pb.StatusCode_PS_MOVE_NORMAL_ERR
            count = db.conn.execute("SELECT COUNT(*) FROM realm_photos WHERE owner=? AND activity=0",(player,)).fetchone()[0]
            if count >= quota(player)[0]:
                return pb.StatusCode_PS_UPLOAD_LIMIT
            db.conn.execute("UPDATE realm_photos SET activity=0,tag3='' WHERE code=?",(req.code,))
    elif op in (pb.PhotoOperateType_LIKE,pb.PhotoOperateType_LIKE_CANCEL,
                pb.PhotoOperateType_FAVORITE,pb.PhotoOperateType_FAVORITE_CANCEL):
        liking = op in (pb.PhotoOperateType_LIKE,pb.PhotoOperateType_LIKE_CANCEL)
        enabled = op in (pb.PhotoOperateType_LIKE,pb.PhotoOperateType_FAVORITE)
        if not liking and enabled and player == photo.player_id:
            return pb.StatusCode_BAD_REQ
        before = photo.likes_status if liking else photo.favorite_status
        if before != enabled:
            if not liking and enabled:
                saved = db.conn.execute("SELECT COUNT(*) FROM realm_photo_marks WHERE player=? AND saved=1",(player,)).fetchone()[0]
                if saved >= limit("Collect_MaxNum"):
                    return pb.StatusCode_PS_FAVORITE_LIMIT
            mark, count = ("liked","likes") if liking else ("saved","saves")
            db.conn.execute(f"""INSERT INTO realm_photo_marks(code,player,{mark}) VALUES(?,?,?)
                ON CONFLICT(code,player) DO UPDATE SET {mark}=excluded.{mark}""",(req.code,player,enabled))
            db.conn.execute(f"UPDATE realm_photos SET {count}={count}+? WHERE code=?",(1 if enabled else -1,req.code))
            if not enabled:
                db.conn.execute("DELETE FROM realm_photo_marks WHERE code=? AND player=? AND saved=0 AND liked=0",(req.code,player))
    else:
        return pb.StatusCode_BAD_REQ
    rsp.code = req.code
    return pb.StatusCode_OK


def copy_dye(s, req, rsp, write):
    photo = detail_for(s.player_id,req.code)
    if req.activity_id and req.activity_id != photo.activity_id:
        return pb.StatusCode_BAD_REQ
    key = req.outfit.outfit_id
    if photo.player_id == s.player_id:
        return pb.StatusCode_BAD_REQ
    source = next((value for value in photo.outfits if value.outfit_id==key),None)
    if source is None or not source.dye_schemes.colors:
        return pb.StatusCode_NOT_DYE
    item = outfit(s.player_id,key)
    # Only use colors signed by the server's immutable snapshot. The requested index
    # identifies the receiver's destination scheme; any submitted colors must match.
    requested = req.outfit.dye_schemes
    if requested.colors and list(requested.colors)!=list(source.dye_schemes.colors):
        return pb.StatusCode_BAD_REQ
    index = requested.scheme_index
    schemes = item.main_item.outfit.dye_schemes
    if index>=len(schemes) or not schemes[index].is_un_lock:
        return pb.StatusCode_OUTFIT_SCHEME_INDEX_NOT_UNLOCK
    if list(schemes[index].colors) == list(source.dye_schemes.colors):
        return pb.StatusCode_OK
    pay(write,"CopyDye_Currency")
    del schemes[index].colors[:]
    schemes[index].colors.extend(source.dye_schemes.colors)
    store_outfit(s.player_id,item,write)
    db.conn.execute("UPDATE realm_photos SET downloads=downloads+1 WHERE code=?",(req.code,))
    if worn(s.player_id,key,index):
        write.after_commit.append((publish,(s,pb.SceneActionType_UPDATE_FASHION)))
    return pb.StatusCode_OK


def add_grid(s, req, rsp, write):
    # No price was published in the national tables. The community expansion grants five.
    db.conn.execute("""INSERT INTO realm_photo_quotas(player,extra) VALUES(?,5)
        ON CONFLICT(player) DO UPDATE SET extra=extra+5""",(s.player_id,))
    return pb.StatusCode_OK


upload_photo = partial(run, operation=upload)
change_title = partial(run, operation=rename)
operate_photo = partial(run, operation=operate)
copy_colors = partial(run, operation=copy_dye)
expand_gallery = partial(run, operation=add_grid)
