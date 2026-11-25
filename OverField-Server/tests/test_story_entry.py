"""Play all nine Chapter 00 stories through the native protocol on one disposable role."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from test_game_realm import join_after_intro, login, next_msg


def run(root, resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    profile=identity.create_profile("芳汀剧情")
    game=LocalGameService(identity,root,resources,port=0)
    game.start()
    from itertools import count
    sequence=count(100)
    def request(sock,name,wire_id=None,**fields):
        wire=next(sequence) if wire_id is None else wire_id
        raw=getattr(pb,name+"Req")(**fields).SerializeToString()
        sock.sendall(encode(getattr(MsgId,name+"Req"),raw,wire))
        for _ in range(256):
            hdr,raw=receive(sock)
            if hdr.msg_id==getattr(MsgId,name+"Rsp") and hdr.packet_id==wire:
                return getattr(pb,name+"Rsp").FromString(raw)
        raise AssertionError(name)
    def movement(sock,x,z,y=0):
        record=pb.PlayerSceneRecordReq()
        point=record.data.char_recorder_data_lst.add(char_id=101001).pos
        point.x,point.y,point.z=x,y,z
        sock.sendall(encode(MsgId.PlayerSceneRecordReq, record.SerializeToString(),30))
        next_msg(sock,MsgId.PlayerSceneSyncDataNotice,pb.PlayerSceneSyncDataNotice)
    def connect():
        sock,*_=login(game.port,identity.issue_ticket(*profile))
        data,_=join_after_intro(sock)
        return sock,{q.quest_id:q.status for q in data.quest_detail.quests}
    def accept(sock,story):
        return request(sock,"AcceptQuest",quest_type=pb.EQuestType_MainQuest,quest_id=story)
    def interact(sock,resource):
        return request(sock,"NpcTalk",id=resource,talk_type=pb.NpcTalkReq.NpcTalkType_Mechanism)
    try:
        sock,_=connect()
        with sock:
            assert accept(sock,1000002).status==pb.StatusCode_BAD_REQ
            assert accept(sock,1000001).quest.quest_id==100011
            movement(sock,1992,-2666)
            assert request(sock,"NpcTalk",id=11000111).status==pb.StatusCode_OK
            movement(sock,2570,-2187)
            assert request(sock,"NpcTalk",id=11000121).status==pb.StatusCode_OK
            assert request(sock,"Gacha",gacha_id=2000,is_single=True).status==pb.StatusCode_OK
            first=request(sock,"ClaimQuestReward",quest_id=1000001,quest_type=pb.EQuestType_MainQuest)
            assert first.status==pb.StatusCode_OK and first.quest.quest_id==100013 and first.items
            assert request(sock,"ClaimQuestReward",quest_id=1000001,quest_type=pb.EQuestType_MainQuest).status==pb.StatusCode_REPEAT_GET_REWARD
            assert accept(sock,1000002).quest.quest_id==100021
            assert interact(sock,1000211).status==pb.StatusCode_BAD_REQ
            movement(sock,1503,-1787)
            assert request(sock,"NpcTalk",id=11000211).status==pb.StatusCode_BAD_REQ
            assert interact(sock,1000211).status==pb.StatusCode_OK
            assert interact(sock,1000211).status==pb.StatusCode_OK
            assert accept(sock,1000002).quest.quest_id==100022
            assert interact(sock,1000221).status==pb.StatusCode_BAD_REQ
            assert request(sock,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            assert interact(sock,1000221).status==pb.StatusCode_OK
            assert interact(sock,1000221).status==pb.StatusCode_OK
            assert request(sock,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            assert accept(sock,1000002).quest.quest_id==100023
        game.stop()
        game.start()
        sock,quests=connect()
        with sock:
            assert {k:quests[k] for k in (100021,100022,100023)}=={100021:3,100022:3,100023:1}
            assert 100024 not in quests
            assert request(sock,"AreaUnlock",area_id=2001).status==pb.StatusCode_BAD_REQ
            assert request(sock,"AreaUnlock",area_id=1002).status==pb.StatusCode_OK
            assert interact(sock,1000241).status==pb.StatusCode_BAD_REQ
            assert request(sock,"AreaClose",area_id=1001).area.area_state==pb.AreaData.Close
            assert accept(sock,1000002).quest.quest_id==100023
            activated=request(sock,"AreaUnlock",area_id=1001)
            assert activated.status==pb.StatusCode_OK and activated.area.area_state==pb.AreaData.Unlock
            assert {i.item_id:i.num for i in activated.area.items}=={1:300,102:10,101:1000}
            assert accept(sock,1000002).quest.quest_id==100024
            assert not request(sock,"AreaUnlock",area_id=1001).area.items
            assert request(sock,"AreaClose",area_id=1001).area.area_state==pb.AreaData.Close
            assert not request(sock,"AreaUnlock",area_id=1001).area.items
            movement(sock,-3515,1483)
            assert interact(sock,1000251).status==pb.StatusCode_BAD_REQ
            assert interact(sock,1000241).status==pb.StatusCode_BAD_REQ
            movement(sock,-3918,3442)
            assert request(sock,"NpcTalk",id=1000241).status==pb.StatusCode_BAD_REQ
            assert interact(sock,1000241).status==pb.StatusCode_OK
            assert interact(sock,1000241).status==pb.StatusCode_OK
            assert accept(sock,1000002).quest.quest_id==100025
            assert interact(sock,1000251).status==pb.StatusCode_BAD_REQ
            movement(sock,-3515,1483)
            assert interact(sock,1000251).status==pb.StatusCode_OK
            assert interact(sock,1000251).status==pb.StatusCode_OK
            assert accept(sock,1000002).quest.quest_id==100026
            assert request(sock,"NpcTalk",id=11000261).status==pb.StatusCode_BAD_REQ
            assert request(sock,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            assert accept(sock,1000002).quest.status==pb.QuestStatus_InProgress
            assert request(sock,"ChangeSceneChannel",scene_id=9999).status==pb.StatusCode_OK
            assert accept(sock,1000002).quest.status==pb.QuestStatus_Finish
            award=request(sock,"ClaimQuestReward",quest_id=1000002,quest_type=pb.EQuestType_MainQuest)
            assert award.status==pb.StatusCode_OK and award.quest.quest_id==100026
            assert {i.main_item.item_id:i.main_item.base_item.num for i in award.items}=={1:300,101:325,102:50,190:5}
            assert request(sock,"ClaimQuestReward",quest_id=1000002,quest_type=pb.EQuestType_MainQuest).status==pb.StatusCode_REPEAT_GET_REWARD
            assert request(sock,"AreaUnlock",area_id=1001).status==pb.StatusCode_BAD_REQ
            assert accept(sock,1000003).quest.quest_id==100031
            assert request(sock,"NpcTalk",id=11000311).status==pb.StatusCode_BAD_REQ
            movement(sock,-2538,-2614,278)
            assert request(sock,"NpcTalk",id=11000311).status==pb.StatusCode_OK
            assert accept(sock,1000003).quest.quest_id==100032
            assert request(sock,"AbilityBadgePageBoxActive",page=1,box_id=31).status==pb.StatusCode_OK
            assert accept(sock,1000003).quest.quest_id==100033
            assert interact(sock,1000341).status==pb.StatusCode_BAD_REQ
            assert request(sock,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            assert accept(sock,1000003).quest.quest_id==100034
            assert interact(sock,1000351).status==pb.StatusCode_BAD_REQ
            movement(sock,-517,3179,0)
            assert interact(sock,1000341).status==pb.StatusCode_BAD_REQ
            movement(sock,-517,3179,911)
            assert interact(sock,1000341).status==pb.StatusCode_OK
            assert accept(sock,1000003).quest.quest_id==100035
            assert request(sock,"NpcTalk",id=1000351).status==pb.StatusCode_BAD_REQ
            assert interact(sock,1000351).status==pb.StatusCode_OK
            assert accept(sock,1000003).quest.status==pb.QuestStatus_Finish
            prize=request(sock,"ClaimQuestReward",quest_id=1000003,quest_type=pb.EQuestType_MainQuest)
            assert prize.status==pb.StatusCode_OK and prize.quest.quest_id==100035 and prize.items
            from utils import db
            pid = db.conn.execute("SELECT player_id FROM private_slots WHERE slot=?",(profile[0],)).fetchone()[0]
            def stack(template):
                raw = db.get_item_detail(pid,template)
                return pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0

            def copies(template):
                return sum(pb.ItemDetail.FromString(b).main_item.item_id==template
                           for b in db.get_item_detail(pid,table="items_s"))
            def state(battle, value, wire_id=None):
                return request(sock,"FlagBattleStateUpdate",wire_id=wire_id,battle_id=battle,battle_state=value)
            def win(battle):
                assert state(battle,pb.Finish).status==pb.StatusCode_BAD_REQ
                assert state(battle,pb.Start).battle_data.state==pb.Start
                assert state(battle,pb.Fail).battle_data.finish_times==0
                assert state(battle,pb.Finish).status==pb.StatusCode_BAD_REQ
                assert state(battle,pb.Start).status==pb.StatusCode_OK
                receipt=next(sequence)
                result=state(battle,pb.Finish,receipt)
                assert result.status==pb.StatusCode_OK and result.battle_data.finish_times==1
                assert state(battle,pb.Finish,receipt)==result
            def reward(story,last):
                got=request(sock,"ClaimQuestReward",quest_id=story,quest_type=pb.EQuestType_MainQuest)
                assert got.quest.quest_id==last and got.status==pb.StatusCode_OK and got.items
                assert request(sock,"ClaimQuestReward",quest_id=story,quest_type=pb.EQuestType_MainQuest).status==pb.StatusCode_REPEAT_GET_REWARD

            assert accept(sock,1000004).quest.quest_id==100041
            assert interact(sock,1000431).status==pb.StatusCode_BAD_REQ
            assert interact(sock,1000411).status==pb.StatusCode_OK
            assert accept(sock,1000004).quest.quest_id==100042
            assert interact(sock,1000421).status==pb.StatusCode_BAD_REQ
            movement(sock,-4390,3049,268)
            assert interact(sock,1000421).status==pb.StatusCode_OK
            assert accept(sock,1000004).quest.quest_id==100043
            assert interact(sock,1000431).status==pb.StatusCode_BAD_REQ
            assert interact(sock,1000451).status==pb.StatusCode_OK
            movement(sock,-3716,573,-19)
            assert interact(sock,1000431).status==pb.StatusCode_OK
            movement(sock,-3066,201,-19)
            assert interact(sock,1000441).status==pb.StatusCode_OK
            assert interact(sock,1000441).status==pb.StatusCode_OK
            reward(1000004,100045)
            assert accept(sock,1000005).quest.quest_id==100051
            assert request(sock,"NpcTalk",id=11000511).status==pb.StatusCode_BAD_REQ
            request(sock,"ChangeSceneChannel",scene_id=9999)
            movement(sock,-2538,-2614,278)
            items=(100000038,100000026,100000039)
            old=[stack(i) for i in items]
            assert request(sock,"NpcTalk",id=11000511).status==pb.StatusCode_OK
            assert request(sock,"NpcTalk",id=11000511).status==pb.StatusCode_OK
            assert [stack(i)-v for i,v in zip(items,old)]==[1,1,1]
            assert accept(sock,1000005).quest.quest_id==100052
            request(sock,"ChangeSceneChannel",scene_id=1)
            assert accept(sock,1000005).quest.quest_id==100053
            movement(sock,-1997,-3775,-18)
            assert interact(sock,1000532).status==pb.StatusCode_BAD_REQ
            win(110210)
            assert accept(sock,1000005).quest.quest_id==100053
            assert interact(sock,1000532).status==pb.StatusCode_OK
            assert accept(sock,1000005).quest.quest_id==100054
            movement(sock,-4825,-7538,955)
            assert interact(sock,1000541).status==pb.StatusCode_OK
            request(sock,"ChangeSceneChannel",scene_id=9999)
            movement(sock,-2538,-2614,278)
            assert request(sock,"NpcTalk",id=11000551).status==pb.StatusCode_OK
            reward(1000005,100055)

            assert accept(sock,1000006).quest.quest_id==100061
            movement(sock,-2801,-2291,240)
            assert request(sock,"NpcTalk",id=11000611).status==pb.StatusCode_OK
            request(sock,"ChangeSceneChannel",scene_id=1)
            assert accept(sock,1000006).quest.quest_id==100063
            movement(sock,4047,-2115,6)
            drinks=stack(190)
            assert interact(sock,1000631).status==pb.StatusCode_OK
            assert stack(190)==drinks+10
            count=copies(2113001)
            assert accept(sock,1000006).quest.quest_id==100064
            win(110300)
            assert copies(2113001)==count+1
            request(sock,"ChangeSceneChannel",scene_id=9999)
            assert accept(sock,1000006).quest.quest_id==100066
            movement(sock,-2801,-2291,240)
            assert request(sock,"NpcTalk",id=11000661).status==pb.StatusCode_OK
            reward(1000006,100066)

            def talk_at(talk_id,x,z,y=0):
                movement(sock,x,z,y)
                rsp=request(sock,"NpcTalk",id=talk_id)
                assert rsp.status==pb.StatusCode_OK,(talk_id,rsp.status)
            def scene(sid):
                assert request(sock,"ChangeSceneChannel",scene_id=sid).status==pb.StatusCode_OK
            def objective(story, expected):
                actual=accept(sock,story).quest.quest_id
                assert actual==expected,(story,expected,actual)
            objective(1000007,100071)
            talk_at(11000711,-2538,-2614,278)
            scene(1)
            objective(1000007,100073)
            for marker,x,z,y in ((1000731,4540,-7383,702),(1000732,-107,7776,549),(1000733,-7066,7312,709)):
                objective(1000007,100073)
                movement(sock,x,z,y)
                assert interact(sock,marker).status==pb.StatusCode_OK
                assert interact(sock,marker).status==pb.StatusCode_OK
            objective(1000007,100074)
            photos=range(10701005,10701009)
            before={p:stack(p) for p in photos}
            for repeat in range(2):
                rsp=request(sock,"FlagBattleStateUpdate",battle_id=1002,battle_state=pb.Start,mission_type=pb.EMissionType_ScenicSpot)
                assert rsp.status==pb.StatusCode_OK and rsp.battle_data.finish_times==1
                assert all(stack(p)==before[p]+1 for p in photos)
            objective(1000007,100075)
            assert interact(sock,1000751).status==pb.StatusCode_BAD_REQ
            movement(sock,-682,-2998,4259)
            assert interact(sock,1000751).status==pb.StatusCode_OK
            reward(1000007,100075)

            objective(1000008,100081)
            scene(9999)
            talk_at(11000811,-2801,-2291,240)
            talk_at(11000821,1992,-2666)
            objective(1000008,100083)
            movement(sock,4289,1166,-586)
            assert interact(sock,1000831).status==pb.StatusCode_OK
            talk_at(11000841,2570,-2187)
            before=stack(5012011)
            talk_at(11000851,140,4044)
            assert stack(5012011)==before+5
            objective(1000008,100086)
            assert request(sock,"ShopBuy",shop_id=11000,grid_id=4,buy_times=1,item_id=5011004).status==pb.StatusCode_OK
            ingredients=stack(5011004)
            from unittest.mock import patch
            with patch("handlers.CookingFood.random.random",return_value=0):
                cooked=request(sock,"CookingFood",food_id=5012001)
            assert cooked.status==pb.StatusCode_OK and cooked.life_common_result.is_success
            assert stack(5011004)==ingredients-2
            talk_at(11000871,140,4044)
            talk_at(11000881,-2538,-2614,278)
            objective(1000008,100089)
            movement(sock,2654,5203,-594)
            firework_reward=stack(100000022)
            for marker in (1000891,1000901,1000981):
                assert interact(sock,marker).status==pb.StatusCode_OK,marker
            assert stack(100000022)==firework_reward+1
            reward(1000008,100098)

            objective(1000009,100091)
            talk_at(11000911,-2538,-2614,278)
            scene(1)
            objective(1000009,100093)
            from private_map_progress import catalog,points
            crystals=sorted(k for k,locations in catalog()[1].items() if locations.get(1)==3)
            for crystal in crystals:
                if points(pid,1)>=46:
                    break
                assert request(sock,"Collecting",item_id=crystal).status==pb.StatusCode_OK
            assert points(pid,1)==46
            objective(1000009,100094)
            scene(9999)
            chapter_end=stack(100000031)
            talk_at(11000941,-2538,-2614,278)
            assert stack(100000031)==chapter_end+1
            reward(1000009,100094)

        game.stop()
        game.start()
        sock,quests=connect()
        with sock:
            assert all(quests.get(q)==pb.QuestStatus_Finish for q in range(100021,100027))
            assert all(quests.get(q)==pb.QuestStatus_Finish for q in range(100031,100036))
            for story,start,end in ((1000004,100041,100045),(1000005,100051,100055),(1000006,100061,100066),
                                    (1000007,100071,100075),(1000008,100081,100090),(1000009,100091,100094)):
                assert quests[100098]==pb.QuestStatus_Finish
                assert all(quests.get(q)==pb.QuestStatus_Finish for q in range(start,end+1))
                assert request(sock,"ClaimQuestReward",quest_id=story,quest_type=pb.EQuestType_MainQuest).status==pb.StatusCode_REPEAT_GET_REWARD
            assert request(sock,"ClaimQuestReward",quest_id=1000003,quest_type=pb.EQuestType_MainQuest).status==pb.StatusCode_REPEAT_GET_REWARD
            assert request(sock,"ClaimQuestReward",quest_id=1000002,quest_type=pb.EQuestType_MainQuest).status==pb.StatusCode_REPEAT_GET_REWARD
            assert request(sock,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            scene=next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice)
            assert {f.battle_id:f.finish_times for f in scene.data.flags}=={110210:1,110300:1,1002:1}
            device=next(a for a in scene.data.areas if a.area_id==1001)
            assert device.level==1 and device.area_state==pb.AreaData.Unlock
            assert not request(sock,"AreaUnlock",area_id=1001).area.items
        previous = identity.create_profile("旧档芳汀")
        legacy,*_ = login(game.port,identity.issue_ticket(*previous))
        with legacy:
            _,initial=join_after_intro(legacy)
            assert initial.data.scene_id==9999
            assert request(legacy,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            scene=next_msg(legacy,MsgId.SceneDataNotice,pb.SceneDataNotice)
            assert not any(a.area_id==1001 for a in scene.data.areas)
            assert accept(legacy,1000002).status==pb.StatusCode_BAD_REQ
            first=request(legacy,"AreaUnlock",area_id=1001)
            assert first.status==pb.StatusCode_OK and len(first.area.items)==3
            assert request(legacy,"AreaClose",area_id=1001).area.area_state==pb.AreaData.Close
            assert request(legacy,"ChangeSceneChannel",scene_id=9999).status==pb.StatusCode_OK
            assert accept(legacy,1000001).quest.quest_id==100011
            movement(legacy,1992,-2666)
            assert request(legacy,"NpcTalk",id=11000111).status==pb.StatusCode_OK
            movement(legacy,2570,-2187)
            assert request(legacy,"NpcTalk",id=11000121).status==pb.StatusCode_OK
            assert request(legacy,"Gacha",gacha_id=2000,is_single=True).status==pb.StatusCode_OK
            assert accept(legacy,1000002).quest.quest_id==100021
            movement(legacy,1503,-1787)
            assert interact(legacy,1000211).status==pb.StatusCode_OK
            assert request(legacy,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            assert interact(legacy,1000221).status==pb.StatusCode_OK
            assert accept(legacy,1000002).quest.quest_id==100024
            assert not request(legacy,"AreaUnlock",area_id=1001).area.items
    finally:
        game.stop()
    print("PASS all nine consecutive prologue stories, all objectives, immediate item and equipment grants, chapter claims, movement, replay and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:run(Path(state),parser.parse_args().resources)
