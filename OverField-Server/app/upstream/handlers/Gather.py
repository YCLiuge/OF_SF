from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId


from proto.net_pb2 import GatherReq, GatherRsp, StatusCode
from private_inventory import receive
from utils.res_loader import res
from utils.pb_create import make_item


@packet_handler(MsgId.GatherReq)
class Handler(PacketHandler):
    def handle(self, session, data: bytes, packet_id: int):
        req = GatherReq()
        req.ParseFromString(data)

        rsp = GatherRsp()
        rsp.status = StatusCode.StatusCode_OK
        rsp.index = req.gather_item.index
        rsp.item_level = 1

        for data in res.get("Gather", {}).get("gather", {}).get("datas", []):
            for info in data["gather_group_info"]:
                if info["reward"] == req.gather_item.reward:
                    rsp.group_gather_limit.gather_group_id = data["i_d"]
                    gather_limit = rsp.group_gather_limit.gather_limit
                    gather_limit.gather_type = info["new_weapon_type"]  # ?
                    gather_limit.gather_num = 5  # TODO 采集数量限制
                    gather_limit.gather_limit_num = 1
                    rsp.scene_gather_limit.scene_id = session.scene_id
                    gather_limit_s = rsp.scene_gather_limit.gather_limits.add()
                    gather_limit_s.CopyFrom(gather_limit)
                    for gather_reward in (
                        res.get("Gather", {}).get("gather_reward", {}).get("datas", [])
                    ):
                        if gather_reward["i_d"] == info["reward"]:
                            options=gather_reward.get("gather_reward_group_info",[])
                            index=int(req.gather_item.is_lucky)
                            if index>=len(options):
                                break
                            item_t=options[index]
                            item = make_item(item_t["item_i_d"],item_t["count"],session.player_id)
                            rsp.status = receive(session,[item],rsp.items)
                            if rsp.status==StatusCode.StatusCode_OK and info.get("new_gather_type")==5 and info.get("new_weapon_type")==15:
                                from private_bless_tasks import record as prayer
                                prayer(session,76)
                            session.send(
                                MsgId.GatherRsp, rsp, packet_id
                            )  # TODO 任务进度通知
                            return
        rsp.status = StatusCode.StatusCode_BAD_REQ
        session.send(MsgId.GatherRsp,rsp,packet_id)
