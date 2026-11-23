from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res


@packet_handler(MsgId.PosterIllustrationListReq)
class Handler(PacketHandler):
    def handle(self, session, data, packet_id):
        pb.PosterIllustrationListReq.FromString(data)
        rows = res["Poster"]["poster"]["datas"]
        illustrations = {r["item_i_d"]: r["illustration_i_d"] for r in rows if r.get("illustration_i_d")}
        owned = set()
        for blob in db.get_item_detail(session.player_id, table="items_s"):
            item = pb.ItemDetail.FromString(blob).main_item
            if item.WhichOneof("item") == "poster":
                owned.add(illustrations.get(item.poster.poster_id))
        rsp = pb.PosterIllustrationListRsp(status=pb.StatusCode_OK)
        for row in res["Poster"]["poster_illustration"]["datas"]:
            if row.get("is_show") and row["i_d"] in owned:
                rsp.poster_illustrations.add(poster_illustration_id=row["i_d"], status=pb.Reward)
        session.send(MsgId.PosterIllustrationListRsp, rsp, packet_id)
