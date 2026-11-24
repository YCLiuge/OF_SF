"""Public game UIDs. Old player rows keep their keys and all of their related save data."""
from collections import defaultdict
from functools import lru_cache

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
    conn.execute("""CREATE TABLE IF NOT EXISTS private_import_uid_reservations(
        uid INTEGER PRIMARY KEY, player_id INTEGER NOT NULL UNIQUE)""")
    old={pid:uid for pid,uid in conn.execute("SELECT player_id,uid FROM realm_public_uids")}
    high=_highest_local(conn)
    reserved=set(old.values())|{uid for uid, in conn.execute("SELECT uid FROM private_import_uid_reservations")}
    for (pid,) in conn.execute("SELECT player_id FROM players WHERE player_id>0 AND player_id<? UNION SELECT player_id FROM private_slots WHERE player_id>0 AND player_id<? ORDER BY player_id",(FIRST,FIRST)).fetchall():
        if pid not in old:
            high+=1
            while high in reserved:
                high+=1
            if high>=INVALID:
                raise ValueError("Private UID range is full")
            conn.execute("INSERT INTO realm_public_uids VALUES(?,?)",(pid,high))
            old[pid]=high
    refresh(conn)


def refresh(conn):
    # The caller holds the game lock. Publish the two lookups only after a successful commit.
    global _legacy, _reversed
    rows=dict(conn.execute("SELECT player_id,uid FROM realm_public_uids"))
    _legacy=rows
    _reversed={uid:pid for pid,uid in rows.items()}


def _highest_local(conn):
    return max(FIRST-1,conn.execute("SELECT max(v) FROM (SELECT coalesce(max(player_id),0) v FROM private_slots UNION ALL SELECT coalesce(max(player_id),0) FROM players)").fetchone()[0])


def next_uid(conn):
    candidate=_highest_local(conn)+1
    while conn.execute("""SELECT 1 FROM realm_public_uids WHERE uid=?
            UNION ALL SELECT 1 FROM private_import_uid_reservations WHERE uid=? LIMIT 1""",
            (candidate,candidate)).fetchone():
        candidate+=1
    return candidate


def public_uid(pid):
    return _legacy.get(pid,pid)


def local_uid(uid):
    if uid in _reversed:
        return _reversed[uid]
    return uid if uid == 0 or (FIRST <= uid < INVALID and uid not in _legacy) else INVALID


_INTEGER_TYPES={F.TYPE_UINT32,F.TYPE_UINT64,F.TYPE_INT64,F.TYPE_INT32}
_EXTRA_FIELDS={
    "MPBeacon":{"owner_id","members"},
    "MPTeamMembers":{"leader","members"},
    "MPTeamInviteNotice":{"inviter"},
    "MPTeamInviteReq":{"target_id"},
    "MPTeamKickMemberReq":{"target_id"},
    "MPTeamKickMemberRsp":{"target_id"},
    "SceneSitVehicleReq":{"chair_id"},
    "SceneSitVehicleRsp":{"chair_id"},
    "VehicleInfo":{"chair_id","seat_info"},
    "FriendSearchReq":{"search_args"},
}
_GOMOKU_FIELDS={"first_player_id","player1_id","player2_id","winner_id","next_player_id"}


def _public_field(name,field):
    return (field in _PLAYER_FIELDS or field in _EXTRA_FIELDS.get(name,()) or field=="furniture_player_map"
            or ("Gomoku" in name and field in _GOMOKU_FIELDS))


@lru_cache(maxsize=1)
def _plans():
    # Walk the schema once when a realm first needs public aliases. With large inventories and
    # movement updates, most message types and nested records never contain a player identifier.
    parents=defaultdict(list)
    fields=defaultdict(set)
    pending=list(pb.DESCRIPTOR.message_types_by_name.values())
    seen=set()
    while pending:
        desc=pending.pop()
        if desc in seen:
            continue
        seen.add(desc)
        for field in desc.fields:
            if _public_field(desc.name,field.name):
                fields[desc].add(field.name)
            child=field.message_type
            if child and child.GetOptions().map_entry:
                child=child.fields_by_name["value"].message_type
            if child:
                parents[child].append((desc,field.name))
                if child not in seen:
                    pending.append(child)
    changed=list(fields)
    while changed:
        child=changed.pop()
        for parent,field in parents[child]:
            if parent not in fields:
                changed.append(parent)
            fields[parent].add(field)
    return dict(fields)


def _translate(msg,outward):
    convert=public_uid if outward else local_uid
    name=msg.DESCRIPTOR.name
    tracked=_plans().get(msg.DESCRIPTOR)
    if not tracked:
        return
    for field,value in msg.ListFields():
        k=field.name
        if k not in tracked:
            continue
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
        elif (name,k)!=("FriendSearchReq","search_args"):
            if field.type in _INTEGER_TYPES:
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
    if not _legacy or message.DESCRIPTOR not in _plans():
        return message
    copy=type(message)()
    copy.CopyFrom(message)
    _translate(copy,True)
    return copy


def incoming(msg_id,data):
    if not _legacy:
        return data
    kind=_TYPES.get(msg_id)
    if kind is None or kind.DESCRIPTOR not in _plans():
        return data
    msg=kind.FromString(data)
    _translate(msg,False)
    return msg.SerializeToString()

