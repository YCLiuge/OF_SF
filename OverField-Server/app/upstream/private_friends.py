"""The realm's native friend requests, blocked relationships and private-role search."""
from network.msg_id import MsgId
from proto import net_pb2 as pb
from server.scene_data import online_session
from social_query import NAME_KEY, name_key, search_key
import player_name_index
from utils import db
from utils.pb_create import make_PlayerBriefInfo
from utils.res_loader import res


def setup():
    from private_wishes import setup as setup_wishes
    setup_wishes()
    db.conn.create_function("private_name_key",1,name_key,deterministic=True)
    player_name_index.setup(db.conn)


def peer_exists(player):
    return db.conn.execute("SELECT 1 FROM private_slots JOIN players USING(player_id) WHERE player_id=?",(player,)).fetchone()


def state(player,other):
    return db.get_friend_info(player,other,"friend_status")[0] or pb.FriendStatus_NONE


def set_state(player,other,new_state):
    from private_wishes import friendship_changed
    friendship_changed(player, other, new_state)
    if new_state==pb.FriendStatus_NONE:
        db.conn.execute("DELETE FROM friend WHERE player_id=? AND friend_id=?",(player,other))
    else:
        db.conn.execute("""INSERT INTO friend(player_id,friend_id,friend_status) VALUES(?,?,?)
            ON CONFLICT(player_id,friend_id) DO UPDATE SET friend_status=excluded.friend_status""",(player,other,new_state))


def at_limit(player,status):
    key="Player_Friend_Count" if status==pb.FriendStatus_FRIEND else "Player_Friend_Apply_Count"
    cap=int(next(r["values"][0] for r in res["GlobalConfig"]["config"]["datas"] if r.get("key")==key))
    used=db.conn.execute("SELECT COUNT(*) FROM friend WHERE player_id=? AND friend_status=?",(player,status)).fetchone()[0]
    return used>=cap


def notify(player,other,kind):
    online = online_session(player)
    if online:
        online.send(MsgId.FriendHandleNotice,pb.FriendHandleNotice(
            status=pb.StatusCode_OK,type=kind,target_player_id=other),0)


def search(session,req,rsp):
    key=req.search_args if NAME_KEY.fullmatch(req.search_args) else search_key(req.search_args)
    if not key:
        return pb.StatusCode_FRIEND_SEARCH_FAIL
    if key.isdigit():
        pid=int(key)
        if not peer_exists(pid):
            return pb.StatusCode_FRIEND_SEARCH_FAIL
    else:
        pid=player_name_index.find(db.conn,bytes.fromhex(key.removeprefix("~ofn1-")),friends=True)
        if pid is None:
            return pb.StatusCode_FRIEND_SEARCH_FAIL
    make_PlayerBriefInfo(pid,rsp.data)
    rsp.friend_status=state(session.player_id,pid)
    return pb.StatusCode_OK


def add(session,req,rsp):
    player,other=session.player_id,req.player_id
    if player==other or not peer_exists(other):
        return pb.StatusCode_FRIEND_ADD_FAIL
    pair={state(player,other),state(other,player)}
    if pb.FriendStatus_BLACK in pair:
        return pb.StatusCode_FRIEND_BLACK
    if pb.FriendStatus_FRIEND in pair:
        return pb.StatusCode_FRIEND_EXIST
    if pb.FriendStatus_APPLY in pair:
        return pb.StatusCode_FRIEND_APPLY_EXIST
    if at_limit(player,pb.FriendStatus_FRIEND):
        return pb.StatusCode_FRIEND_LIMIT
    if at_limit(other,pb.FriendStatus_FRIEND):
        return pb.StatusCode_FRIEND_OTHER_LIMIT
    if at_limit(other,pb.FriendStatus_APPLY):
        return pb.StatusCode_FRIEND_APPLY_LIMIT
    set_state(other,player,pb.FriendStatus_APPLY)
    notify(other,player,pb.FriendHandleType_APPLY)
    return pb.StatusCode_OK


def handle(session,req,rsp):
    player,other=session.player_id,req.player_id
    if player==other or not peer_exists(other) or state(player,other)!=pb.FriendStatus_APPLY:
        return pb.StatusCode_FRIEND_NOT_APPLY
    if not req.is_agree:
        set_state(player,other,pb.FriendStatus_NONE)
        notify(player,other,pb.FriendHandleType_DEL)
        return pb.StatusCode_OK
    if state(other,player)==pb.FriendStatus_BLACK:
        return pb.StatusCode_FRIEND_BLACK
    if at_limit(player,pb.FriendStatus_FRIEND):
        return pb.StatusCode_FRIEND_LIMIT
    if at_limit(other,pb.FriendStatus_FRIEND):
        return pb.StatusCode_FRIEND_OTHER_LIMIT
    for side,peer in ((player,other),(other,player)):
        set_state(side,peer,pb.FriendStatus_FRIEND)
        notify(side,peer,pb.FriendHandleType_ADD)
        from server.scene_data import online_session
        from private_bless_tasks import record as prayer
        target=session if side==player else online_session(side)
        if target:prayer(target,177)
    return pb.StatusCode_OK


def delete(session,req,rsp):
    player,other=session.player_id,req.player_id
    if player==other or not other or state(player,other)!=pb.FriendStatus_FRIEND:
        return pb.StatusCode_FRIEND_NOT_EXIST
    for side,peer in ((player,other),(other,player)):
        if state(side,peer)==pb.FriendStatus_FRIEND:
            set_state(side,peer,pb.FriendStatus_NONE)
            notify(side,peer,pb.FriendHandleType_DEL)
    return pb.StatusCode_OK


def block(session,req,rsp):
    player,other=session.player_id,req.player_id
    if other==player or not peer_exists(other):
        return pb.StatusCode_BAD_REQ
    if req.is_remove:
        if state(player,other)==pb.FriendStatus_BLACK:
            set_state(player,other,pb.FriendStatus_NONE)
        return pb.StatusCode_OK
    if state(other,player) in (pb.FriendStatus_APPLY,pb.FriendStatus_FRIEND):
        set_state(other,player,pb.FriendStatus_NONE)
        notify(other,player,pb.FriendHandleType_DEL)
    set_state(player,other,pb.FriendStatus_BLACK)
    notify(player,other,pb.FriendHandleType_DEL)
    return pb.StatusCode_OK
