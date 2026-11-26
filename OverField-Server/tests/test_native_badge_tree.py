"""Star-tree requests over the retail wire, using only private test characters."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from private_badge_config import ABILITY_BY_ITEM, NODE_REWARDS
from private_dungeons import _table
from private_shop import balance
from test_game_realm import login, next_msg
from test_observed_initialization import call
from utils import db
from utils.pb_create import make_item, make_Quest


def run(state, resources):
    identity = LocalIdentity(state/"identity.sqlite3")
    slots = [identity.create_profile(k) for k in ("星云甲", "星云乙")]
    game = LocalGameService(identity,state,resources,port=0)

    def connect(index, earned=False):
        sock, _, pid = login(game.port, identity.issue_ticket(*slots[index]))
        main = call(sock, "PlayerMainData")
        assert main.status == pb.StatusCode_OK and (100000008 in main.unlock_functions) == earned
        markers = []
        while True:
            head, body = receive(sock)
            if head.msg_id == MsgId.SceneDataNotice:
                break
            if head.msg_id == MsgId.PackNotice:
                markers.extend(item.main_item.base_item.num for item in pb.PackNotice.FromString(body).items
                               if item.main_item.item_id == 100000008)
        assert markers == ([1] if earned else []), markers
        return sock, pid

    def amount(pid, item):
        with game.lock:
            held = balance(pid,item)
            return held.main_item.base_item.num if held else 0

    def activate(sock,page,box):
        return call(sock,"AbilityBadgePageBoxActive",page=page,box_id=box)

    def claim(sock, ids):
        return call(sock,"AbilityBadgeAchieveReward",achieve_ids=ids)

    def starter(sock, typ=pb.NpcTalkReq.NpcTalkType_Npc):
        return call(sock,"NpcTalk",talk_type=typ,id=11000311)

    def fund(pid,item,quantity):
        detail=make_item(item,quantity,pid)
        db.set_item_detail(pid,detail.SerializeToString(),item)

    game.start()
    try:
        a,pid = connect(0)
        b,bid = connect(1)
        with a,b:
            pages = {1, 2, 3, 4, 5, 1001, 1002, 1003}
            assert amount(pid, 100000008) == 0  # The tutorial can still give its one-time reward.
            for scene in (1, 2, 3, 4, 5, 1):
                assert call(a, "ChangeSceneChannel", scene_id=scene).status == pb.StatusCode_OK
                full = call(a, "AbilityBadgeList")
                assert {page.page for page in full.ability_badge_pages} == pages
                assert not any(page.active_box_ids or page.is_reward for page in full.ability_badge_pages)
                single = call(a, "AbilityBadgeList", scene_id=scene).ability_badge_pages
                assert [page.page for page in single] == [scene] and not single[0].active_box_ids
            assert not call(a, "AbilityBadgeList", scene_id=9999).ability_badge_pages
            assert starter(a).status == pb.StatusCode_BAD_REQ
            with game.lock, db.conn:
                q=pb.Quest()
                make_Quest(pid,100031,q)
                db.set_quest(pid,100031,q.SerializeToString())
                fund(pid,301,5)
            assert call(a,"ChangeSceneChannel",scene_id=9999).status == pb.StatusCode_OK
            assert starter(a).status == pb.StatusCode_BAD_REQ
            move=pb.PlayerSceneRecordReq()
            spot=move.data.char_recorder_data_lst.add(char_id=101001).pos
            spot.x,spot.y,spot.z=-2538,278,-2614
            a.sendall(encode(MsgId.PlayerSceneRecordReq,move.SerializeToString(),56))
            next_msg(a,MsgId.PlayerSceneSyncDataNotice,pb.PlayerSceneSyncDataNotice)
            assert starter(a,pb.NpcTalkReq.NpcTalkType_Mechanism).status == pb.StatusCode_BAD_REQ
            assert starter(a).status == pb.StatusCode_OK and amount(pid,301)==6
            gained = next_msg(a, MsgId.PlayerUnlockFunctionNotice, pb.PlayerUnlockFunctionNotice)
            assert list(gained.unlock_functions) == [100000008] and amount(pid,100000008) == 1
            assert starter(a).status == pb.StatusCode_OK and amount(pid,301)==6
            assert activate(a,1,2).status == pb.StatusCode_ABILITY_BADGE_ACTIVE_PRE_CONDITION_NOT_FINISH
            assert activate(a,1,1).status == pb.StatusCode_ABILITY_BADGE_ACTIVE_PRE_CONDITION_NOT_FINISH
            assert activate(a,1,3).status == pb.StatusCode_CFG_NOT_FOUND  # Decorative grid cell.
            assert call(a,"AbilityBadgePageReward",page=1).status==pb.StatusCode_ABILITY_BADGE_PAGE_BOX_ACTIVE_LIMIT
            assert activate(b,1,31).status == pb.StatusCode_ABILITY_BADGE_NOT_UNLOCK
            node=activate(a,1,31)
            assert node.status==pb.StatusCode_OK and [i.main_item.item_id for i in node.items]==[101000000]
            left=amount(pid,301)
            assert left==5 and not activate(a,1,31).items and amount(pid,301)==left
            assert activate(a,1,1).status==pb.StatusCode_OK
            assert claim(a,[50101010]).status==pb.StatusCode_ABILITY_BADGE_ACHIEVE_NOT_COMPLETE
            assert claim(a,[0]).status==pb.StatusCode_CFG_NOT_FOUND
            with game.lock,db.conn:
                target=_table("Achieve","achieve")[50101010]["count_param"]
                db.set_achieve(pid,50101010,pb.Achieve(achieve_id=50101010,count=target).SerializeToString())
            left=amount(pid,301)
            batch=claim(a,[50101010,50101010,50101011])
            assert batch.status==pb.StatusCode_OK and list(batch.achieve_ids)==[50101010] and amount(pid,301)==left+1
            assert claim(a,[50101010]).status==pb.StatusCode_ABILITY_BADGE_ACHIEVE_REWARDED
            assert claim(b,[50101010]).status==pb.StatusCode_ABILITY_BADGE_ACHIEVE_NOT_COMPLETE

            areas=_table("MoonStone","stone_area_data","scene_i_d")
            assert len(areas)==8 and sum(len(v) for v in NODE_REWARDS.values())==198
            with game.lock,db.conn:
                db.set_players_info(pid,"level",10)
                for row in areas.values():fund(pid,row["item_i_d"],75)
            for page,area in areas.items():
                valid=set(NODE_REWARDS[page])
                boxes=_table("MoonStone","stone_unlock_data")[area["unlock_i_d"]]["stone_unlock_data_group_info"]
                assert valid=={box["box_i_d"] for box in boxes if box.get("item_count")}
                left=valid.copy()
                while left:
                    opened=set()
                    for box in sorted(left):
                        result=activate(a,page,box)
                        if result.status==pb.StatusCode_OK:opened.add(box)
                        else:assert result.status==pb.StatusCode_ABILITY_BADGE_ACTIVE_PRE_CONDITION_NOT_FINISH,(page,box,result.status)
                    assert opened,(page,left)
                    left-=opened
                ticket=amount(pid,110)
                assert call(a,"AbilityBadgePageReward",page=page).status==pb.StatusCode_OK and amount(pid,110)==ticket+10
                assert call(a,"AbilityBadgePageReward",page=page).status==pb.StatusCode_ABILITY_BADGE_PAGE_REWARDED
                state=call(a,"AbilityBadgeList",scene_id=page)
                assert state.ability_badge_pages[0].is_reward and set(state.ability_badge_pages[0].active_box_ids)==valid
            assert amount(pid,120000004)>0
            assert {info.ability_id for info in call(a,"PlayerAbilityList").ability_list}==set(ABILITY_BY_ITEM.values())
            assert not call(b,"PlayerAbilityList").ability_list and not call(b,"AbilityBadgeList",scene_id=1).ability_badge_pages[0].is_reward
            # Historical saves may hold a token before the associated ability row exists.
            items = list(ABILITY_BY_ITEM)[:3]
            existing, missing, empty = items
            with game.lock, db.conn:
                for item in items:
                    fund(bid, item, int(item != empty))
                db.conn.execute("INSERT INTO player_kv VALUES(?,'ability',?,1)", (bid, str(ABILITY_BY_ITEM[existing])))
                # These bytes are outside the client's tree catalog but inside its numeric range.
                db.set_achieve(bid, 50001009, b"\xff")
            assert call(b,"AbilityBadgeList").status == pb.StatusCode_OK
            recovered = call(b, "PlayerAbilityList")
            assert {v.ability_id: v.level for v in recovered.ability_list} == {
                ABILITY_BY_ITEM[existing]: 1, ABILITY_BY_ITEM[missing]: 0}
            statements = []
            with game.lock:
                db.conn.set_trace_callback(statements.append)
            try:
                assert call(b, "PlayerAbilityList") == recovered
            finally:
                with game.lock:
                    db.conn.set_trace_callback(None)
            assert not any(s.startswith(("INSERT", "UPDATE", "DELETE")) for s in statements)
    finally:
        game.stop()
    game.start()
    try:
        a,pid=connect(0, earned=True)
        with a:
            view=call(a,"AbilityBadgeList",scene_id=0)
            assert len(view.ability_badge_pages)==8 and all(page.is_reward for page in view.ability_badge_pages)
            assert 50101010 in view.ability_badge_reward_achieve_ids and len(call(a,"PlayerAbilityList").ability_list)==75
            assert claim(a,[50101010]).status==pb.StatusCode_ABILITY_BADGE_ACHIEVE_REWARDED
            assert activate(a,1,31).status==pb.StatusCode_OK
        # Previously all functions were written to every player, even before the first map.
        with game.lock, db.conn:
            db.set_players_info(bid, "unlock_functions", [100000008, 915])
            db.set_ability_tree(bid, 1, [99999])
        b,_=connect(1)
        b.close()
        # A restored, valid node alone is sufficient evidence for a historical partial save.
        with game.lock, db.conn:
            db.set_ability_tree(bid, 1, [31])
        b,_=connect(1, earned=True)
        with b:
            assert list(call(b,"AbilityBadgeList",scene_id=1).ability_badge_pages[0].active_box_ids) == [31]
        with game.lock, db.conn:
            db.set_ability_tree(bid, 1, [])
            q = pb.Quest(quest_id=100031, status=pb.QuestStatus_Finish)
            db.set_quest(bid, 100031, q.SerializeToString())
        b,_=connect(1, earned=True)
        b.close()
    finally:
        game.stop()
    print("PASS native star tree: 8 pages, all 198 configured nodes, 75 earned abilities, prerequisites, balances, one-time NPC, claims, role isolation and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:run(Path(tmp),parser.parse_args().resources)
