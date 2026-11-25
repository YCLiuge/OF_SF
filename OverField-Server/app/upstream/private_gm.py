"""Small in-game GM allowlist for an authenticated connection to its own realm."""
from datetime import datetime, timezone
from hashlib import blake2s
import time
import unicodedata

from network.msg_id import MsgId
from private_chat_text import chat_text
from private_request import cache_after_commit
from proto import net_pb2 as pb
from realm_gm import audit
from realm_uids import public_uid, local_uid
from server.scene_data import get_session, online_session
from utils import db


COMMANDS = {"help", "whoami", "online", "give", "announce", "mail", "moonspot", "challenge"}
HELP = "服管命令：help、whoami、online、announce 正文、mail UID 正文、give 代币ID 数量、moonspot place、moonspot move/remove 编号、challenge UID。"


def chat_command(text):
    return unicodedata.normalize("NFKC",text[:180]).lstrip().casefold().startswith("/gm")


def number(value, ceiling):
    if not isinstance(value,str) or not value.isascii() or not value.isdecimal() or len(value)>10:
        return 0
    n=int(value)
    return n if 1<=n<=ceiling else 0


def message_body(value):
    # Plain text only, including content passed straight to the native GM notice renderer.
    return value if value and len(value)<=120 and not chat_command(value) else None


def dispatch(session, verb, arg, reply):
    pid=session.player_id
    actor=public_uid(pid)
    if verb=="challenge":
        from private_action_challenge import renew
        uid=number(arg,0xfffffffe)
        target=online_session(local_uid(uid)) if uid else None
        if not target or not target.logged_in:
            return None
        renew(target, issuer=session)
        reply.result=f"已向 UID {uid} 发送本服操作验证"
        return f"uid={uid}"
    if verb=="moonspot":
        from private_moon_spots import command
        return command(session,arg,reply)
    if verb in ("help","whoami","online"):
        if arg:
            return None
        if verb=="help":
            reply.result=HELP
        elif verb=="whoami":
            reply.result=f"本服管理员 UID {actor}"
        else:
            reply.result=f"本服在线 {len(get_session())} 人"
        return ""
    if verb=="give":
        item,sep,count=arg.partition(" ")
        item,count=number(item,9999),number(count.strip(),1000)
        if not sep or item not in (101,102,103) or not count:
            return None
        from private_dungeons import grant
        raw=db.get_item_detail(pid,item)
        if raw and pb.ItemDetail.FromString(raw).main_item.base_item.num>2147483647-count:
            return None
        grant(session,{item:count},pb.ClaimQuestRewardRsp().items)
        reply.reward_infos.add(item_id=item,num=count)
        reply.result=f"已向自己的角色发放 {item} × {count}"
        return f"item={item} count={count}"
    if verb=="announce":
        body=message_body(arg)
        if not body:
            return None
        notice=pb.GmNotice(status=pb.StatusCode_OK,notice=[body])
        peers=get_session()
        for peer in peers:
            peer.send(MsgId.GmNotice,notice,0)
        reply.result=f"已向本服 {len(peers)} 名在线玩家发送通知"
        return body
    if verb=="mail":
        label,sep,text=arg.partition(" ")
        target=number(label,0xfffffffe)
        text=message_body(text.strip())
        peer_id=local_uid(target)
        if not sep or not text or not target or not db.conn.execute(
                "SELECT 1 FROM players p JOIN private_slots s ON p.player_id=s.player_id WHERE p.player_id=?",(peer_id,)).fetchone():
            return None
        from private_wishes import send_mail
        mid=send_mail(peer_id,"区服通知",text,(),datetime.now(timezone.utc),online_session(peer_id),sender="区服管理")
        reply.result=f"已向 UID {target} 投递邮件 #{mid}"
        return f"uid={target} mail={mid} {text}"
    return None


def current_grant(session):
    # Acquire the same SQLite write lock used by CLI grant/revoke before the permission read.
    # The operator change, command side effects, receipts and audit thus have a defined order.
    db.conn.execute("UPDATE gm_grants SET generation=generation WHERE 0")
    return db.conn.execute("""SELECT g.generation FROM private_slots p JOIN gm_grants g ON g.slot=p.slot
        JOIN authority.profiles a ON a.slot=p.slot WHERE p.player_id=? AND p.slot=?""",(session.player_id,session.slot)).fetchone()


def execute(session, raw, via, packet_id):
    reply=pb.GmCodeRsp(status=pb.StatusCode_FAIL,result="此账号没有本服游戏管理员权限")
    pid=session.player_id
    actor=public_uid(pid)
    row=current_grant(session)
    if not row:
        db.conn.execute("""INSERT INTO gm_denials VALUES(?,?,?,1)
            ON CONFLICT(day,player,via) DO UPDATE SET attempts=attempts+1""",(int(time.time())//86400,actor,via))
        return reply
    clean=chat_text(raw)
    guard=getattr(session,"chat_guard",None)
    if not clean or guard and not guard.allows(raw):
        audit(db.conn,actor,via,"invalid","rejected")
        reply.status,reply.result=pb.StatusCode_BAD_REQ,"命令无效"
        return reply
    signed=clean.split(None,1)
    if signed and unicodedata.normalize("NFKC",signed[0]).casefold() in ("/gm","gm"):
        clean=signed[1] if len(signed)>1 else "help"
    parts=clean.split(None,1)
    verb=parts[0].casefold()
    arg=parts[1].strip() if len(parts)>1 else ""
    cache_key=(row[0],via,packet_id,blake2s(clean.encode()).digest())
    receipts=getattr(session,"gm_receipts",None)
    if packet_id and receipts and cache_key in receipts:
        return pb.GmCodeRsp.FromString(receipts[cache_key])
    detail=dispatch(session,verb,arg,reply) if verb in COMMANDS else None
    if detail is None:
        audit(db.conn,actor,via,verb if verb in COMMANDS else "invalid","rejected")
        reply.status,reply.result=pb.StatusCode_BAD_REQ,"命令或参数无效，请用 /gm help"
    else:
        audit(db.conn,actor,via,verb,"ok",detail)
        reply.status=pb.StatusCode_OK
        if packet_id:
            cache_after_commit(session,"gm_receipts",cache_key,reply.SerializeToString())
    return reply
