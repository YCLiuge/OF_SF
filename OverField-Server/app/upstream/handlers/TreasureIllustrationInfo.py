"""National client 4523. Keep earned progress and previously claimed rewards separate."""
from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res


def read_illustrations(player_id):
    result = pb.TreasureIllustrationInfoRsp(status=pb.StatusCode_OK)
    tables = {row[0] for row in db.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('claimed_rewards','player_kv')")}
    claims = set()
    if "claimed_rewards" in tables:
        claims = set(db.db.execute("SELECT system,key FROM claimed_rewards WHERE player_id=? "
                                   "AND system IN ('treasure_illustration','treasure_achieve')", (player_id,)))
    dig_counts = {}
    if "player_kv" in tables:
        dig_counts = dict(db.db.execute("SELECT k,v FROM player_kv WHERE player_id=? AND ns='archaeology'", (player_id,)))
    reward_lists = {
        pb.EArchaeologyType_Book: result.book_reward,
        pb.EArchaeologyType_Utensil: result.utensil_reward,
        pb.EArchaeologyType_TreasureMap: result.treasure_reward,
        pb.EArchaeologyType_Shovel: result.shovel_reward,
    }
    for category, target in reward_lists.items():
        prefix = str(category) + ":"
        ids = {int(key[len(prefix):]) for system,key in claims if system == "treasure_illustration"
               and key.startswith(prefix) and key[len(prefix):].isascii() and key[len(prefix):].isdigit()}
        target.extend(sorted(entry for entry in ids if 0 < entry < 2**32))
    groups = res["ArchaeologyAchieve"]["archaeology_achieve"]["datas"]
    for row in sorted(groups, key=lambda r: r.get("new_archaeology_type", 0)):
        group = result.achieves.add(achieve_type=row.get("new_archaeology_type", 0))
        for item in row["items"]:
            aid = item["achieve_i_d"]
            entry = group.treasure_achieve_.add(achieve_id=aid)
            saved = db.get_achieve(player_id, aid)
            stored = pb.Achieve.FromString(saved).count if saved else 0
            entry.count = max(stored, int(dig_counts.get(str(item["reward_i_d"]), 0)))
            if ("treasure_achieve", str(aid)) in claims:
                group.rewarded_treasure_achieve_id_lst.append(aid)
    return result


@packet_handler(MsgId.TreasureIllustrationInfoReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        pb.TreasureIllustrationInfoReq.FromString(data)
        session.send(MsgId.TreasureIllustrationInfoRsp, read_illustrations(session.player_id), packet_id)

