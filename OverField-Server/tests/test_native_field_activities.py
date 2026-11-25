"""National outdoor puzzles, encounter state/treasure and tutorial voice regions through native TCP."""
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from realm_rules import configure
from private_shop import CN
from test_game_realm import join, login, next_msg
from test_native_gacha import call
from utils import db

OK=pb.StatusCode_OK


def run(root,resources):
    ident=LocalIdentity(root/"identity.sqlite3")
    accounts=[ident.create_profile(v) for v in ("野外甲","野外乙")]
    configure(root,{"monster_gift_percent":100})
    host=LocalGameService(ident,root,resources,port=0)
    def connect(i):
        sock,_,pid=login(host.port,ident.issue_ticket(*accounts[i]))
        main,notice=join(sock)
        return sock,pid,notice.data
    def travel(sock,scene,seq,channel=0):
        answer=call(sock,"ChangeSceneChannel",seq,scene_id=scene,channel_label=channel)
        assert answer.status==OK,answer
        return next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice).data
    def puzzle(sock, seq, phase, win=False, key=100001):
        return call(sock,"RiddleStateUpdate",seq,riddle_id=key,battle_state=phase,is_win=win)
    def encounter(sock,seq,phase,key=1073):
        return call(sock,"BattleEncounterStateUpdate",seq,encounter_id=key,battle_state=phase)
    def query(sock,seq,key=1073):
        return call(sock,"BattleEncounterInfo",seq,encounter_ids=[key])
    def open_box(sock,seq,index):
        return call(sock,"TreasureBoxOpen",seq,box_type=1,treasure_box_index=index,loc_type=0)
    def pickup(sock,seq,index):
        return call(sock,"TreasureBoxPickup",seq,box_type=1,box_index=index,pick_index=-1,loc_type=0)
    def exp(player):
        return pb.Character.FromString(db.get_characters(player,101001)[0]).exp
    def value(player,key):
        i=db.get_item_detail(player,key)
        return pb.ItemDetail.FromString(i).main_item.base_item.num if i else 0
    host.start()
    first_day=datetime(2027,1,23,12,tzinfo=CN)
    try:
        a,pid,where=connect(0)
        b,other,_=connect(1)
        with a,b,patch("private_field_activities.now",return_value=first_day):
            assert where.scene_id==100
            assert call(a,"CollectVoiceRegion",10,voice_id=101).status==OK
            assert call(a,"CollectVoiceRegion",11,voice_id=101).status==OK
            assert call(a,"CollectVoiceRegion",12,voice_id=1001).status!=OK
            assert list(travel(a,100,13).region_voices)==[101]
            assert not travel(b,100,14).region_voices
            assert not travel(a,100,209,channel=2).region_voices
            assert not travel(a,1,15).region_voices
            assert call(a,"CollectVoiceRegion",16,voice_id=101).status!=OK
            assert not travel(a,100,17).region_voices
            travel(a,1,18)
            travel(b,1,19)
            assert puzzle(a,20,pb.Finish,True).status!=OK
            assert puzzle(a,21,pb.Start,False,602001).status!=OK
            assert puzzle(a,22,pb.Start).status==OK
            assert puzzle(a,23,pb.Reset).status==OK
            assert puzzle(a,24,pb.Close).status==OK
            assert puzzle(a,25,pb.Finish,True).status!=OK
            assert puzzle(a,26,pb.Start).status==OK
            with host.lock:
                before=value(pid,192)
            first=puzzle(a,27,pb.Finish,True)
            assert first.status==OK and first.riddle_data.is_win and first.dynamic_treasure_box_base_info.box_id==2001,first
            chest=first.dynamic_treasure_box_base_info.index
            assert puzzle(a,27,pb.Finish,True)==first and puzzle(a,28,pb.Finish,True)==first
            assert open_box(b,29,chest).status!=OK
            with host.lock:
                assert value(pid,192)==before
            opened=open_box(a,30,chest)
            assert opened.status==OK and [(i.main_item.item_id,i.main_item.base_item.num) for i in opened.items]==[(192,10)]
            award=pickup(a,31,chest)
            assert award.status==OK and len(award.items)==1
            assert not pickup(a,32,chest).items
            with host.lock:
                assert value(pid,192)==before+10
            assert puzzle(a,33,pb.Start).status==OK
            repeat=puzzle(a,34,pb.Finish,True)
            assert repeat.dynamic_treasure_box_base_info.box_id==2002 and repeat.dynamic_treasure_box_base_info.index!=chest
            assert query(a,36,4070).status!=OK
            assert query(a,37,2001).status!=OK
            assert query(a,38).encounters[0].state==pb.Start
            assert encounter(a,39,pb.Finish).status!=OK
            assert encounter(a,40,pb.Start).status==OK
            assert encounter(a,41,pb.Fail).encounter.state==pb.Close
            assert encounter(a,42,pb.Start).status!=OK
            with patch("private_field_activities.now",return_value=first_day+timedelta(minutes=11)):
                assert encounter(a,43,pb.Start).status==OK
                with host.lock:
                    previous=exp(pid)
                won=encounter(a,44,pb.Finish)
                assert won.status==OK and won.dynamic_treasure_box_base_info.box_id==300000,won
                battle_chest=won.dynamic_treasure_box_base_info.index
                assert encounter(a,44,pb.Finish)==won and encounter(a,45,pb.Finish)==won
                assert query(a,46).encounters[0]==won.encounter
                assert query(b,47).encounters[0].state==pb.Start
                assert open_box(a,48,battle_chest).items
                with host.lock:
                    assert exp(pid)==previous+137
            travel(a,2,50)
            recovered=[m for m in call(a,"GetMails",201).mails if m.model_id==3]
            assert len(recovered)==1 and recovered[0].title=="挑战遗失物"
            assert [(i.item_id,i.num) for i in recovered[0].items]==[(192,10)]
            assert not [m for m in call(b,"GetMails",202).mails if m.model_id==3]
            result=call(a,"OperateMails",203,operate_type=pb.MailOperateType_CLAIM,mail_id=recovered[0].mail_id)
            assert result.status==OK
            assert call(a,"OperateMails",204,operate_type=pb.MailOperateType_CLAIM,mail_id=recovered[0].mail_id).status==pb.StatusCode_MAIL_NO_REWARD
            with host.lock:
                assert value(pid,192)==before+20
            assert puzzle(a,51,pb.Start).status!=OK
            assert open_box(a,52,battle_chest).status!=OK
            assert encounter(a,53,pb.Start).status!=OK
            assert puzzle(a,54,pb.Start,False,602001).status==OK
            assert puzzle(a,55,pb.Finish,True,602001).status==OK
            world=travel(a,1,56)
            assert {r.riddle_id for r in world.riddles}=={100001} and world.riddles[0].is_win
            assert len(world.encounter_data)==1 and world.encounter_data[0].battle_id==1073
            assert query(a,57,999).status!=OK
            assert encounter(a,154,pb.Start,1078).status==OK
            assert encounter(a,155,pb.Finish,1078).status==OK
            with host.lock:
                assert pb.Achieve.FromString(db.get_achieve(pid,11051231)).count==1
            assert puzzle(a,212,pb.Start).status==OK
            assert not puzzle(a,213,pb.Finish).riddle_data.is_win
            assert puzzle(a,214,pb.Finish,True).status!=OK
            view=travel(a,1,215)
            assert not next(r for r in view.riddles if r.riddle_id==100001).is_win
            assert puzzle(a,157,pb.Start,False,100002).status==OK
            travel(a,4,158)
            sample=call(a,"BattleEncounterInfo",159,encounter_ids=[4001,4070])
            assert [(i.battle_id,i.state) for i in sample.encounters]==[(4001,pb.Start),(4070,pb.Close)]
            travel(a,1,160)
            assert puzzle(a,161,pb.Finish,True,100002).status!=OK
            for i in range(7):
                assert puzzle(a,60+i*2,pb.Start).status==OK
                assert puzzle(a,61+i*2,pb.Finish,True).dynamic_treasure_box_base_info.box_id==2002
            assert puzzle(a,80,pb.Start).status==OK
            assert not puzzle(a,81,pb.Finish,True).HasField("dynamic_treasure_box_base_info")
            with host.lock:
                assert pb.Achieve.FromString(db.get_achieve(pid,50101130)).count==3
                assert pb.Achieve.FromString(db.get_achieve(pid,60020101)).count==10
                assert pb.Achieve.FromString(db.get_achieve(pid,50101160) or b"").count==0
                assert pb.Achieve.FromString(db.get_achieve(other,50101130) or b"").count==0
        host.stop()
        host.start()
        a,same,snapshot=connect(0)
        with a,patch("private_field_activities.now",return_value=first_day+timedelta(days=1)):
            assert same==pid
            world=travel(a,1,90)
            assert world.riddles[0].is_win and world.encounter_data[0].state==pb.Start
            assert not open_box(a,91,repeat.dynamic_treasure_box_base_info.index).items
            recovered=[m for m in call(a,"GetMails",206).mails if m.model_id==3]
            assert sorted(m.items[0].num for m in recovered)==[10,10,70]
            assert call(a,"OperateMails",207,operate_type=pb.MailOperateType_CLAIM,mail_id=max(m.mail_id for m in recovered)).status==OK
            with host.lock:
                assert value(pid,192)==before+90
            assert puzzle(a,92,pb.Start).status==OK
            assert puzzle(a,93,pb.Finish,True).dynamic_treasure_box_base_info.box_id==2002
            assert encounter(a,94,pb.Start).status==OK
            with patch("private_field_activities.now",return_value=first_day+timedelta(days=1,minutes=6)):
                assert encounter(a,95,pb.Finish).status!=OK
    finally:
        host.stop()
    print("PASS: original puzzle/encounter/region-voice packets, scene ownership, chest opening and pickup, first/repeat rewards, failure/cooldown, achievements and restart.")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),p.parse_args().resources.resolve())
