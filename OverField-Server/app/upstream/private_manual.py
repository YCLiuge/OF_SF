"""PC 1.1.12 handbook discoveries, per-role receipts and native dungeon objective counts."""
import time

from private_dungeons import config as dungeon_config, grant, load, reward_items, tasks
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res


def catalog():
    flags = {f["i_d"]: f for f in res["Flag"]["flag"]["datas"]}
    result = {}
    for sheet in ("manual", "manual_dungeon"):
        for row in res["Manual"][sheet]["datas"]:
            for point in row[sheet + "_items"]:
                fid = point["flag_i_d"]
                flag = flags[fid]
                # ShowSceneDungeon uses the linked dungeon objective table on the secret-dungeon page.
                dungeon = flag["flag_group_info"][0].get("relation_i_d") if sheet == "manual_dungeon" else None
                result[fid] = (flag["scene_i_d"], point["un_lock_reward_i_d"], dungeon)
    return result


def discoveries(pid):
    row = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='manual_flags' AND k='ids'",
                          (pid,)).fetchone()
    raw = row[0] if row else ""
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="replace")
    return {int(part) for part in str(raw).split(",") if len(part) <= 10 and part.isascii() and part.isdigit()}


def receipts(pid):
    rows = db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system='manual_flag'", (pid,))
    return {int(key) for key, in rows if len(key) <= 10 and key.isascii() and key.isdigit()}


def set_flag(pid, fid, entry, claimed, target):
    target.flag_id = fid
    target.status = pb.Reward if fid in claimed else pb.NotReward
    dungeon = entry[2]
    if dungeon and dungeon_config(dungeon):
        data = load(pid, dungeon)
        objectives = tasks(pid, data)
        if data.all_task_finished or data.task_finish_reward == pb.Reward:
            target.dungeon_task_finish_num = len(objectives)
        else:
            target.dungeon_task_finish_num = sum(count >= goal for _, count, goal in objectives)


def list_flags(session, rsp):
    pid, flags = session.player_id, catalog()
    claimed = receipts(pid)
    for fid in sorted((discoveries(pid) | claimed) & flags.keys()):
        set_flag(pid, fid, flags[fid], claimed, rsp.flags.add())
    return pb.StatusCode_OK


def unlock(session, fid, rsp):
    flags = catalog()
    if fid not in flags:
        return pb.StatusCode_CFG_NOT_FOUND
    pid, known, claimed = session.player_id, discoveries(session.player_id), receipts(session.player_id)
    if fid not in known and fid not in claimed:
        if session.dungeon[0] or session.scene_id != flags[fid][0]:
            return pb.StatusCode_MANUAL_NOT_UNLOCK
        known.add(fid)
        encoded = ",".join(str(value) for value in sorted(known)).encode("ascii")
        db.conn.execute("INSERT OR REPLACE INTO player_kv(player_id,ns,k,v) VALUES(?,'manual_flags','ids',?)",
                        (pid, encoded))
    set_flag(pid, fid, flags[fid], claimed, rsp.flag)
    return pb.StatusCode_OK


def reward(session, fid, rsp):
    pid = session.player_id
    entry = catalog().get(fid)
    if entry is None:
        return pb.StatusCode_CFG_NOT_FOUND
    claimed = receipts(pid)
    if fid in claimed:
        set_flag(pid, fid, entry, claimed, rsp.flag)
        return pb.StatusCode_MANUAL_REWARDED
    if fid not in discoveries(pid):
        return pb.StatusCode_MANUAL_NOT_UNLOCK
    amounts = reward_items(pid, entry[1])
    if not amounts:
        return pb.StatusCode_CFG_NOT_FOUND
    # LocalGameService serializes the insertion and inventory update in one transaction.
    db.conn.execute("INSERT INTO claimed_rewards(player_id,system,key,claim_time) VALUES(?,'manual_flag',?,?)",
                    (pid, str(fid), int(time.time())))
    grant(session, amounts, rsp.items)
    set_flag(pid, fid, entry, claimed | {fid}, rsp.flag)
    return pb.StatusCode_OK
