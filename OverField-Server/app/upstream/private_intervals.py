"""Native investigation rifts. All operations run within the gameplay session's SQLite transaction."""
import random
import time
from collections import Counter
from network.msg_id import MsgId
from proto import net_pb2 as pb
from server import scene_data
from utils import db
from utils.res_loader import res
from private_dungeons import grant


def now():
    return time.time_ns() // 1_000_000


def settings():
    return res.index("Explore", "explore_interval")


def setup():
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS private_interval_offers (
            player INTEGER PRIMARY KEY, kind INTEGER NOT NULL, expires INTEGER NOT NULL, area INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS private_interval_runs (
            owner INTEGER NOT NULL, created INTEGER NOT NULL, kind INTEGER NOT NULL, finish INTEGER NOT NULL,
            area INTEGER NOT NULL, claimed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner,created));
        CREATE TABLE IF NOT EXISTS private_interval_members (
            owner INTEGER NOT NULL, created INTEGER NOT NULL, player INTEGER NOT NULL, joined INTEGER NOT NULL,
            claimed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner,created,player));
    """)


def kv(player, key):
    row=db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='interval' AND k=?",(player,key)).fetchone()
    return row[0] if row else None


def mark(player, key):
    db.conn.execute("INSERT OR IGNORE INTO player_kv VALUES(?,'interval',?,1)",(player,key))


def migrate(player):
    blob=kv(player,"state")
    if not blob or kv(player,"legacy_imported"):
        return
    state=db._load_saved_data(blob)
    created=int(state["create_time"])
    db.conn.execute("INSERT OR IGNORE INTO private_interval_runs(owner,created,kind,finish,area) VALUES(?,?,?,?,?)",
                    (player,created,int(state["interval_id"]),int(state["finish_time"]),10101))
    for member in state.get("member",()):
        who=member["player_id"]
        if who==player and member.get("is_reward"):
            db.conn.execute("UPDATE private_interval_runs SET claimed=1 WHERE owner=? AND created=?",(player,created))
        elif who!=player:
            db.conn.execute("INSERT OR IGNORE INTO private_interval_members VALUES(?,?,?,?,?)",
                            (player,created,who,member.get("join_time",created),int(bool(member.get("is_reward")))))
    mark(player,"legacy_imported")
    mark(player,"intro_offered")


def active(player):
    return db.conn.execute("SELECT owner,created,kind,finish,area,claimed FROM private_interval_runs "
                           "WHERE owner=? AND claimed=0 ORDER BY created DESC LIMIT 1",(player,)).fetchone()


def offer(player):
    return db.conn.execute("SELECT kind,expires,area FROM private_interval_offers WHERE player=? AND expires>?",
                           (player,now())).fetchone()


def create_offer(session,explore_id=None):
    """Return the newly discovered template ID. A pending entrance or unclaimed run takes priority."""
    player=session.player_id
    migrate(player)
    if active(player) or offer(player):
        return 0
    areas=res.index("Explore", "explore_area")
    area=explore_id if explore_id in areas else min(areas)
    configs=settings()
    kind=101 if explore_id is None else random.choice(sorted(configs))
    expires=now()+configs[kind]["retention_time"]*1000
    db.conn.execute("INSERT OR REPLACE INTO private_interval_offers VALUES(?,?,?,?)",(player,kind,expires,area))
    mark(player,"intro_offered")
    return kind


def view(row):
    owner,created,kind,finish,_,claimed=row
    info=pb.IntervalInfo(player_id=owner,interval_id=kind,create_time=created,finish_time=finish)
    # PC InterValData.UpdateData derives the active state from a participant matching its captain.
    name,head=db.get_players_info(owner,"player_name,head")
    info.member.add(player_id=owner,nick_name=name,head=head,join_time=created,is_reward=bool(claimed))
    members=db.conn.execute("""SELECT m.player,p.player_name,p.head,m.joined,m.claimed FROM private_interval_members AS m
        JOIN players AS p ON p.player_id=m.player WHERE m.owner=? AND m.created=? ORDER BY m.joined,m.player""",(owner,created))
    for who,name,head,joined,claimed in members:
        info.member.add(player_id=who,nick_name=name,head=head,join_time=joined,is_reward=bool(claimed))
    return info


def self_info(session,req,rsp):
    player=session.player_id
    migrate(player)
    row=active(player)
    if row:
        rsp.is_start=True
        rsp.interval.CopyFrom(view(row))
        rsp.interval_id,rsp.end_time=row[2],row[3]
    else:
        if not kv(player,"intro_offered"):
            create_offer(session)
        ready=offer(player)
        if ready:
            rsp.interval_id,rsp.end_time=ready[:2]
    return pb.StatusCode_OK


def friend_info(session,req,rsp):
    player=session.player_id
    for friend,status in db.get_friend_info(player,None,"friend_id,friend_status"):
        if status==2:
            migrate(friend)
            row=active(friend)
            if row and row[3]>now():
                rsp.friend_infos.add().CopyFrom(view(row))
    joined=db.conn.execute("""SELECT r.owner,r.created,r.kind,r.finish,r.area,r.claimed FROM private_interval_members AS m
        JOIN private_interval_runs AS r ON r.owner=m.owner AND r.created=m.created
        WHERE m.player=? AND m.claimed=0 ORDER BY r.created DESC""",(player,))
    for row in joined:
        rsp.join_infos.add().CopyFrom(view(row))
    return pb.StatusCode_OK


def pay(session,item_id,count):
    raw=db.get_item_detail(session.player_id,item_id)
    if not raw: return False
    total=pb.ItemDetail.FromString(raw)
    if total.main_item.WhichOneof("item")!="base_item" or total.main_item.base_item.num<count:
        return False
    total.main_item.base_item.num-=count
    db.set_item_detail(session.player_id,total.SerializeToString(),item_id)
    session.send(MsgId.PackNotice,pb.PackNotice(status=pb.StatusCode_OK,items=[total]),0)
    return True


def start(session,req,rsp):
    player=session.player_id
    migrate(player)
    row=active(player)
    if row:
        rsp.explore_id,rsp.finish_time=row[2],row[3]
        return pb.StatusCode_OK
    ready=offer(player)
    if not ready: return pb.StatusCode_INTERVAL_NOT_EXISTS
    kind,_,area=ready
    cfg=settings()[kind]
    if not pay(session,cfg["need_item_i_d"],cfg["captain_count"]):
        return pb.StatusCode_ITEM_NOT_ENOUGH
    # The frontend uses IntervalInfo.create_time as its join/claim token.
    latest=db.conn.execute("SELECT COALESCE(MAX(created),0) FROM private_interval_runs WHERE owner=?",(player,)).fetchone()[0]
    created=max(now(),latest+1)
    finish=created+cfg["time"]*1000
    db.conn.execute("INSERT INTO private_interval_runs VALUES(?,?,?,?,?,0)",(player,created,kind,finish,area))
    db.conn.execute("DELETE FROM private_interval_offers WHERE player=?",(player,))
    rsp.explore_id,rsp.finish_time=kind,finish
    return pb.StatusCode_OK


def target(owner,token):
    if not token or not token.isascii() or not token.isdigit() or len(token)>18:
        return None
    return db.conn.execute("SELECT owner,created,kind,finish,area,claimed FROM private_interval_runs WHERE owner=? AND created=?",
                           (owner,int(token))).fetchone()


def changed(owner,created,kind,end):
    db.conn.execute("UPDATE private_interval_runs SET finish=? WHERE owner=? AND created=?",(end,owner,created))
    notice=pb.IntervalChangeNotice(status=pb.StatusCode_OK, player_id=owner,interval_id=kind,finish_time=end)
    watchers={m[0] for m in db.conn.execute("SELECT player FROM private_interval_members WHERE owner=? AND created=?",(owner,created))}
    watchers.add(owner)
    for player in watchers:
        member = scene_data.online_session(player)
        if member is not None:
            member.send(MsgId.IntervalChangeNotice,notice,0)


def join(session,req,rsp):
    player,owner=session.player_id,req.friend_id
    rsp.friend_id=str(owner)
    if owner==player or not owner or db.get_friend_info(player,owner,"friend_status")[0]!=2:
        return pb.StatusCode_FRIEND_NOT_EXIST
    migrate(owner)
    row=target(owner,req.interval_param)
    if not row: return pb.StatusCode_INTERVAL_NOT_EXISTS
    _,created,kind,finish,_,claimed=row
    rsp.explore_id,rsp.finish_time=kind,finish
    already=db.conn.execute("SELECT 1 FROM private_interval_members WHERE owner=? AND created=? AND player=?",
                           (owner,created,player)).fetchone()
    if already: return pb.StatusCode_OK
    if claimed or finish<=now(): return pb.StatusCode_INTERVAL_FINISH
    cfg=settings().get(kind)
    if not cfg:return pb.StatusCode_CFG_NOT_FOUND
    if not pay(session,cfg["need_item_i_d"],cfg["member_count"]):return pb.StatusCode_ITEM_NOT_ENOUGH
    joined=now()
    db.conn.execute("INSERT INTO private_interval_members VALUES(?,?,?,?,0)",(owner,created,player,joined))
    minutes=int(next(r["values"][0] for r in res["GlobalConfig"]["config"]["datas"] if r.get("key")=="Explore_InterVal_ReduceTime"))
    rsp.finish_time=max(joined,finish-minutes*60_000)
    changed(owner,created,kind,rsp.finish_time)
    return pb.StatusCode_OK


def quick(session,req,rsp):
    player=session.player_id
    row=active(player)
    if not row:return pb.StatusCode_INTERVAL_NOT_EXISTS
    _,created,kind,finish,_,_=row
    rsp.explore_id,rsp.finish_time=kind,finish
    remaining=max(0,finish-now())
    if not remaining:return pb.StatusCode_INTERVAL_FINISH
    cfg=settings().get(kind)
    if not cfg:return pb.StatusCode_CFG_NOT_FOUND
    # National PC UI calculates whole seconds, rounds remaining minutes upward, then multiplies by the displayed cost.
    seconds=remaining//1000
    cost=((seconds+59)//60)*cfg["quick_need_item_count"]
    if cost and not pay(session,cfg["quick_need_item"],cost):return pb.StatusCode_ITEM_NOT_ENOUGH
    rsp.finish_time=now()
    changed(player,created,kind,rsp.finish_time)
    return pb.StatusCode_OK


def reward(session,req,rsp):
    player,owner=session.player_id,req.friend_id
    rsp.friend_id=owner
    row=target(owner,req.interval_param)
    if not row:return pb.StatusCode_INTERVAL_NOT_EXISTS
    _,created,kind,finish,area,claimed=row
    rsp.explore_id=kind
    if player!=owner:
        member=db.conn.execute("SELECT claimed FROM private_interval_members WHERE owner=? AND created=? AND player=?",
                               (owner,created,player)).fetchone()
        if not member:return pb.StatusCode_BAD_REQ
        claimed=member[0]
    if claimed:return pb.StatusCode_REPEAT_GET_REWARD
    if finish>now():return pb.StatusCode_INTERVAL_NOT_FINISH
    # The shipped client gives no dedicated rift reward table. The private realm issues local area materials.
    cfg=res.index("Explore", "explore_area").get(area)
    if not cfg:return pb.StatusCode_CFG_NOT_FOUND
    amount=2 if player==owner else 1
    items=Counter()
    for item in cfg["explore_area_item"]:
        items[item["rewar_item_i_d"]]+=amount
    grant(session,items,rsp.rewards)
    if player==owner:
        db.conn.execute("UPDATE private_interval_runs SET claimed=1 WHERE owner=? AND created=?",(owner,created))
    else:
        db.conn.execute("UPDATE private_interval_members SET claimed=1 WHERE owner=? AND created=? AND player=?",(owner,created,player))
    return pb.StatusCode_OK
