"""Persist the native nearby, world and private chat channels on the selected realm."""
import time

from network.msg_id import MsgId
from proto import net_pb2 as pb
from server.scene_data import channel_members, online_session
from utils import db
from utils.res_loader import res
from private_chat_text import chat_text
from private_friends import peer_exists,state

NEAR,WORLD,DM=pb.ChatChannel_Default,pb.ChatChannel_ChatRoom,pb.ChatChannel_Private


def now():
    return time.time_ns()//1_000_000


def setup():
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS private_chat_messages(
            seq INTEGER PRIMARY KEY AUTOINCREMENT, kind INTEGER NOT NULL, room INTEGER NOT NULL, subroom INTEGER NOT NULL,
            author INTEGER NOT NULL, sent INTEGER NOT NULL, body BLOB NOT NULL);
        CREATE INDEX IF NOT EXISTS private_chat_room ON private_chat_messages(kind,room,subroom,seq);
        CREATE INDEX IF NOT EXISTS private_chat_author ON private_chat_messages(author,kind,sent);
        CREATE TABLE IF NOT EXISTS private_chat_contacts(
            player INTEGER NOT NULL, peer INTEGER NOT NULL, newest INTEGER NOT NULL, read_to INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(player,peer));
    """)
    # Early private builds split nearby history by (scene, channel). Both map to the same actual channel.
    db.conn.execute("UPDATE private_chat_messages SET room=subroom,subroom=0 WHERE kind=? AND room>0 AND subroom>0",(NEAR,))
    db.conn.commit()


def global_value(key):
    return int(res.index("GlobalConfig","config","key")[key]["values"][0])


def nearby(session):
    return (NEAR,session.channel_id,0)


def transcript(key,newest=False):
    limit=global_value("ChatMsgRecordLimit")
    rows=db.conn.execute("""SELECT body FROM private_chat_messages WHERE kind=? AND room=? AND subroom=?
                            ORDER BY seq DESC LIMIT ?""",(*key,limit)).fetchall()
    if not newest:
        rows.reverse()
    return [pb.ChatMsgData.FromString(raw) for (raw,) in rows]


def public_history(session,key):
    notice=pb.ChatMsgRecordInitNotice(status=pb.StatusCode_OK,type=key[0],msg=transcript(key))
    session.send(MsgId.ChatMsgRecordInitNotice,notice,0)


def nearby_history(session):
    public_history(session,nearby(session))


def bounds(session):
    rules=getattr(session,"realm_rules",None)
    return (rules.world_min,rules.world_max) if rules else (1,9999)


def login(session,packet_id):
    lo,hi=bounds(session)
    row=db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='chat' AND k='world'",(session.player_id,)).fetchone()
    session.chat_channel_id=row[0] if row and lo<=row[0]<=hi else lo
    session.send(MsgId.ChangeChatChannelRsp,pb.ChangeChatChannelRsp(status=pb.StatusCode_OK,channel_id=session.chat_channel_id),packet_id)
    nearby_history(session)
    public_history(session,(WORLD,session.chat_channel_id,0))
    # A DM conversation is only ever initialized for one of its two participants.
    offline=pb.PrivateChatOfflineNotice(status=pb.StatusCode_OK)
    result=db.conn.execute("""SELECT c.peer,p.player_name,p.head,p.avatar_frame,c.newest>c.read_to
        FROM private_chat_contacts AS c JOIN players AS p ON p.player_id=c.peer WHERE c.player=? ORDER BY c.newest DESC LIMIT ?""",
        (session.player_id,global_value("ChatMsgRecordLimit")))
    for peer,name,head,frame,unread in result:
        offline.offline_msg.add(player_id=peer,name=name,head=head,avatar_frame=frame,is_new_msg=bool(unread))
    if offline.offline_msg:
        session.send(MsgId.PrivateChatOfflineNotice,offline,0)


def switch(session,req,packet_id):
    lo,hi=bounds(session)
    if not lo<=req.channel_id<=hi:
        response=pb.ChangeChatChannelRsp(status=pb.StatusCode_CHAT_CHANNEL_NOT_EXIST,channel_id=session.chat_channel_id)
        session.send(MsgId.ChangeChatChannelRsp,response,packet_id)
        return
    session.chat_channel_id=req.channel_id
    db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'chat','world',?)",(session.player_id,req.channel_id))
    session.send(MsgId.ChangeChatChannelRsp,pb.ChangeChatChannelRsp(status=pb.StatusCode_OK,channel_id=req.channel_id),packet_id)
    public_history(session,(WORLD,req.channel_id,0))


def unread(player,peer,seq,read):
    db.conn.execute("""INSERT INTO private_chat_contacts VALUES(?,?,?,?) ON CONFLICT(player,peer)
        DO UPDATE SET newest=excluded.newest, read_to=MAX(private_chat_contacts.read_to,excluded.read_to)""",
        (player,peer,seq,seq if read else 0))


def history(session,req,rsp):
    player,peer=session.player_id,req.target_player_id
    if not peer or player==peer or not peer_exists(peer):
        return pb.StatusCode_PLAYER_NOT_FOUND
    key=(DM,*sorted((player,peer)))
    rsp.msg_record.extend(transcript(key,newest=True))
    db.conn.execute("UPDATE private_chat_contacts SET read_to=newest WHERE player=? AND peer=?",(player,peer))
    return pb.StatusCode_OK


def expression_ok(player,emotion):
    row=res.index("Chat","chat_emotion").get(emotion)
    if not row:return False
    if row.get("is_show"):return True
    if not row.get("achieve_condition_i_d"):return False
    source=db.get_achieve(player,row["achieve_condition_i_d"])
    cfg=res.index("Achieve","achieve").get(row["achieve_condition_i_d"],{})
    return bool(source and pb.Achieve.FromString(source).count>=cfg.get("count_param",1))


def send(session,req,rsp):
    player=session.player_id
    kind=req.type
    if kind not in (NEAR,WORLD,DM):
        return pb.StatusCode_BAD_REQ
    text=chat_text(req.text)
    guard=getattr(session,"chat_guard",None)
    if text is None or guard and not guard.allows(req.text) or not text and not req.expression:
        return pb.StatusCode_BAD_REQ
    if req.expression and not expression_ok(player,req.expression):
        return pb.StatusCode_CFG_NOT_FOUND
    peer=req.player_id
    if kind==DM:
        if not peer or peer==player or not peer_exists(peer):
            return pb.StatusCode_PLAYER_NOT_FOUND
        if pb.FriendStatus_BLACK in (state(player,peer),state(peer,player)):
            return pb.StatusCode_FRIEND_BLACK
        key=(DM,*sorted((player,peer)))
    else:
        key=nearby(session) if kind==NEAR else (WORLD,session.chat_channel_id,0)
    sent=now()
    cfg=next(row for row in res["Chat"]["chat"]["datas"] if row.get("new_channel_type",0)==kind)
    last=db.conn.execute("SELECT MAX(sent) FROM private_chat_messages WHERE author=? AND kind=?",(player,kind)).fetchone()[0]
    if last and sent-last<cfg.get("interval_seconds",0)*1000:
        return pb.StatusCode_BAD_REQ
    if cfg.get("enabled_repeatting") and cfg.get("repeated_times"):
        recent=db.conn.execute("""SELECT body FROM private_chat_messages WHERE author=? AND kind=? AND room=? AND subroom=? AND sent>=?
            ORDER BY seq DESC LIMIT ?""",(player,*key,sent-cfg["repeated_time"]*1000,global_value("ChatMsgRecordLimit")))
        matching=0
        for (raw,) in recent:
            prev=pb.ChatMsgData.FromString(raw)
            if (prev.text,prev.expression)==(text,req.expression):matching+=1
        if matching>=cfg["repeated_times"]:return pb.StatusCode_BAD_REQ
    name,head,badge,frame=db.get_players_info(player,"player_name,head,team_leader_badge,avatar_frame")
    msg=pb.ChatMsgData(player_id=player,name=name,head=head,badge=badge,avatar_frame=frame,text=text,expression=req.expression,send_time=sent)
    if req.expression:
        from private_bless_tasks import record as prayer
        prayer(session,74,only=522001)
    seq=db.conn.execute("INSERT INTO private_chat_messages(kind,room,subroom,author,sent,body) VALUES(?,?,?,?,?,?)",
                        (*key,player,sent,msg.SerializeToString())).lastrowid
    limit=global_value("ChatMsgRecordLimit")
    db.conn.execute("""DELETE FROM private_chat_messages WHERE kind=? AND room=? AND subroom=? AND seq<=(
        SELECT seq FROM private_chat_messages WHERE kind=? AND room=? AND subroom=? ORDER BY seq DESC LIMIT 1 OFFSET ?)""",
        (*key,*key,limit))
    if kind==DM:
        unread(player,peer,seq,True)
        unread(peer,player,seq,False)
    notice=pb.ChatMsgNotice(status=pb.StatusCode_OK,type=kind,msg=msg)
    if kind==DM:
        destination=online_session(peer)
        if destination:
            destination.send(MsgId.ChatMsgNotice,notice,0)
    else:
        for destination in channel_members(key[1], world=kind==WORLD):
            if destination is not session:  # The sender's client adds its own SendChatMsgRsp.
                destination.send(MsgId.ChatMsgNotice,notice,0)
    rsp.text=text
    return pb.StatusCode_OK
