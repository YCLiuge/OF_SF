"""Native quest journal's one-time home-scene starter grant (GamePlayReward 1003)."""
from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_Quest
from utils.res_loader import res
from private_dungeons import grant,reward_items
from private_first_quest import STEPS,save,saved

JOURNAL=100000021


def start(session,req,rsp):
    if session.scene_id!=9999 or session.dungeon[0] or req.num!=1:
        return pb.StatusCode_BAD_REQ
    return grant_starter(session,rsp.items)


def grant_starter(session, items):
    player=session.player_id
    params={r["key"]:int(r["values"][0]) for r in res["GlobalConfig"]["config"]["datas"]
            if r.get("key") in ("InitialQuest","InitialReward")}
    if params.get("InitialQuest")!=STEPS[0] or not params.get("InitialReward"):
        return pb.StatusCode_CFG_NOT_FOUND
    claimed=db.conn.execute("SELECT 1 FROM claimed_rewards WHERE player_id=? AND system='story_start' AND key='1003'",(player,)).fetchone()
    raw=db.get_item_detail(player,JOURNAL)
    journal=bool(raw and pb.ItemDetail.FromString(raw).main_item.base_item.num)
    if not claimed and not journal:
        configured=reward_items(player,params["InitialReward"])
        if configured.get(JOURNAL)!=1:
            return pb.StatusCode_CFG_NOT_FOUND
        # Preserve items already supplied by previous private-server builds on older roles.
        missing={}
        for item,target in configured.items():
            raw=db.get_item_detail(player,item)
            owned=pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0
            if target>owned:
                missing[item]=target-owned
        if missing:
            grant(session,missing,items)
    if not claimed:
        db.conn.execute("INSERT INTO claimed_rewards(player_id,system,key) VALUES(?,'story_start','1003')",(player,))
    # The client issues AcceptQuest for this step in the same frame as 1003.
    # Preserve a usable journal even if the transport closes between the two requests.
    if not any(saved(player,step) for step in STEPS):
        quest=make_Quest(player,STEPS[0])
        save(session,quest)
        session.send(MsgId.QuestNotice,pb.QuestNotice(status=pb.StatusCode_OK,quests=[quest]),0)
    return pb.StatusCode_OK
