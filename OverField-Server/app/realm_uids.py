"""Public game UIDs. Old player rows keep their keys and all of their related save data."""
from google.protobuf.descriptor import FieldDescriptor as F
from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils import db

FIRST=1000001
INVALID=0xffffffff
_legacy={}
_reversed={}
_TYPES={v:getattr(pb,k) for k,v in vars(MsgId).items() if type(v) is int and hasattr(pb,k)}
_PLAYER_FIELDS={"player_id","player_label","from_player_id","target_player_id","join_player_id","to_player_id",
                "reported_player_id","finished_player_list","friend_id","matched_player_id_lst","action_player_ids"}


def initialize(conn,first=1000001):
    global FIRST
    FIRST=first
    conn.execute("""CREATE TABLE IF NOT EXISTS realm_public_uids(
        player_id INTEGER PRIMARY KEY,uid INTEGER NOT NULL UNIQUE CHECK(uid>0 AND uid<4294967295))""")
    old={pid:uid for pid,uid in conn.execute("SELECT player_id,uid FROM realm_public_uids")}
    high=max([FIRST-1,*old.values(),conn.execute("SELECT COALESCE(MAX(player_id),0) FROM players").fetchone()[0],
              conn.execute("SELECT COALESCE(MAX(player_id),0) FROM private_slots").fetchone()[0]])
    for (pid,) in conn.execute("SELECT player_id FROM players WHERE player_id>0 AND player_id<? UNION SELECT player_id FROM private_slots WHERE player_id>0 AND player_id<? ORDER BY player_id",(FIRST,FIRST)).fetchall():
        if pid not in old:
            high+=1
            if high>=INVALID:
                raise ValueError("Private UID range is full")
            conn.execute("INSERT INTO realm_public_uids VALUES(?,?)",(pid,high))
            old[pid]=high
    _legacy.clear()
    _legacy.update(old)
    _reversed.clear()
    _reversed.update((uid,pid) for pid,uid in old.items())


def next_uid(conn):
    floor=max([FIRST-1,*_reversed])
    return max(floor,conn.execute("SELECT max(v) FROM (SELECT coalesce(max(player_id),0) v FROM private_slots UNION ALL SELECT coalesce(max(player_id),0) FROM players)").fetchone()[0])+1


def public_uid(pid):
    return _legacy.get(pid,pid)


def local_uid(uid):
    return _reversed.get(uid,uid if uid>=FIRST or uid==0 else INVALID)


def _translate(msg,outward):
    convert=public_uid if outward else local_uid
    name=msg.DESCRIPTOR.name
    for field,value in msg.ListFields():
        k=field.name
        if field.message_type and field.message_type.GetOptions().map_entry:
            vf=field.message_type.fields_by_name["value"]
            if vf.message_type:
                for part in value.values():
                    _translate(part,outward)
                if (name,k)==("MPTeamMembers","members"):
                    moved = {convert(key):part.SerializeToString() for key,part in value.items()}
                    value.clear()
                    for key,data in moved.items():
                        value[key].ParseFromString(data)
            elif (name,k)==("MPBeacon","members"):
                members={convert(key):ready for key,ready in value.items()}
                value.clear()
                value.update(members)
            elif k=="furniture_player_map" or (name,k)==("VehicleInfo","seat_info"):
                for key in list(value):
                    value[key]=convert(value[key])
        elif field.message_type:
            if field.is_repeated:
                for part in value:
                    _translate(part,outward)
            else:
                _translate(value,outward)
        elif k in _PLAYER_FIELDS or (name in ("SceneSitVehicleReq","SceneSitVehicleRsp","VehicleInfo") and k=="chair_id") or ("Gomoku" in name and k in {"first_player_id","player1_id","player2_id","winner_id","next_player_id"}) or (name,k) in (("MPBeacon","owner_id"),("MPTeamMembers","leader"),("MPTeamInviteNotice","inviter")) or (k=="target_id" and name in ("MPTeamInviteReq","MPTeamKickMemberReq","MPTeamKickMemberRsp")):
            if field.type in (F.TYPE_UINT32,F.TYPE_UINT64,F.TYPE_INT64,F.TYPE_INT32):
                if field.is_repeated:
                    for i,v in enumerate(value):
                        value[i]=convert(v)
                else:
                    setattr(msg,k,convert(value))
            elif k=="friend_id" and value.isascii() and value.isdigit() and len(value)<=10:
                setattr(msg,k,str(convert(int(value))))
        elif name=="FriendSearchReq" and k=="search_args" and value.isascii() and value.isdigit() and len(value)<=10 and not outward:
            msg.search_args=str(local_uid(int(value)))


def outgoing(message):
    if not _legacy:
        return message
    copy=type(message)()
    copy.CopyFrom(message)
    _translate(copy,True)
    return copy


def incoming(msg_id,data):
    if not _legacy:
        return data
    kind=_TYPES.get(msg_id)
    if kind is None:
        return data
    msg=kind.FromString(data)
    _translate(msg,False)
    return msg.SerializeToString()

