"""Verify all published gallery claims, actual currency changes and multi-star material consumption."""
import argparse
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_dungeons import _table, progress
from private_shop import balance
from test_game_realm import join, login
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def run(root, resources):
    users=LocalIdentity(root/"identity.sqlite3")
    accounts=[users.create_profile("映像甲"),users.create_profile("映像乙")]
    host=LocalGameService(users,root,resources,port=0)
    def enter(n):
        sock,_,pid=login(host.port,users.issue_ticket(*accounts[n]))
        join(sock)
        return sock,pid
    def make(pid,tid,stars=1,wearer=0):
        obj=make_item(tid,1,pid)
        poster=obj.main_item.poster
        poster.star,poster.wearer_id=stars,wearer
        key=poster.instance_id
        db.set_item_detail(pid,obj.SerializeToString(),instance_id=key)
        return key
    def listing(sock):
        rows=call(sock,"PosterIllustrationList",10)
        assert rows.status==pb.StatusCode_OK
        return {r.poster_illustration_id:r.status for r in rows.poster_illustrations}
    def claim(sock,tid):
        return call(sock,"PosterIllustrationReward",tid,poster_illustration_id=tid)
    def up(sock,sequence,key,*materials):
        return call(sock,"PosterStarUp",sequence,poster_instance_id=key,cost_poster_instance_id=list(materials))
    def stars(pid,iid):
        raw=db.get_item_detail(pid,instance_id=iid)
        return pb.ItemDetail.FromString(raw).main_item.poster.star if raw else 0
    def amount(pid,tid):
        item=balance(pid,tid)
        return item.main_item.base_item.num if item else 0
    host.start()
    try:
        a,pid=enter(0)
        b,peer=enter(1)
        with a,b:
            visible={i for i,r in _table("Poster","poster_illustration").items() if r.get("is_show")}
            assert len(visible)==112 and len(_table("Poster","poster"))==171
            assert not listing(a) and claim(a,10010).status==pb.StatusCode_POSTER_ILLUSTRATION_NOT_ACTIVE
            with host.lock,db.conn:
                cards={r["illustration_i_d"]:make(pid,r["item_i_d"]) for r in _table("Poster","poster").values()
                       if r.get("illustration_i_d") in visible}
                target=cards[10010]
                two=make(pid,10010,2)
                three=make(pid,10010,3)
                worn=make(pid,10010,1,101001)
                preset_copy=make(pid,10010)
                one_star=make(pid,10020)
                character=pb.Character.FromString(db.get_characters(pid)[0])
                character.equipment_presets[0].posters.add(poster_id=target)
                character.equipment_presets[0].posters.add(poster_id=preset_copy,poster_index=1)
                db.set_character(pid,character.character_id,character.SerializeToString())
            assert set(listing(a))==visible and set(listing(a).values())=={pb.NotReward}
            assert claim(a,10020).status==pb.StatusCode_CFG_NOT_FOUND
            assert listing(b)=={} and claim(b,10010).status==pb.StatusCode_POSTER_ILLUSTRATION_NOT_ACTIVE
            first={tid:amount(pid,tid) for tid in (111,112)}
            total=Counter()
            for gid in sorted(visible):
                reply=claim(a,gid)
                assert reply.status==pb.StatusCode_OK and reply.poster_illustration.status==pb.Reward
                quality=_table("Item","item")[gid]["quality"]
                currency,units=(111,20) if quality==4 else (112,15)
                assert [(i.main_item.item_id,i.main_item.base_item.num) for i in reply.items]==[(currency,units)]
                total[currency]+=units
            assert set(listing(a).values())=={pb.Reward}
            assert claim(a,10010).status==pb.StatusCode_POSTER_ILLUSTRATION_REWARDED
            assert all(amount(pid,tid)==first[tid]+units for tid,units in total.items())
            assert up(b,20,target,two).status==pb.StatusCode_BAD_REQ
            assert up(a,21,target,target).status==pb.StatusCode_BAD_REQ
            assert up(a,22,target,two,two).status==pb.StatusCode_BAD_REQ
            for seq,material in enumerate((worn,preset_copy,cards[10030],987654321),23):
                assert up(a,seq,target,two,material).status==pb.StatusCode_BAD_REQ
                assert stars(pid,target)==1 and stars(pid,two)==2
            assert up(a,28,one_star,worn).status==pb.StatusCode_POSTER_MAX_STAR
            reply=up(a,29,target,two)
            assert reply.status==pb.StatusCode_OK and reply.star==3 and list(reply.cost_poster_instance_id)==[two]
            assert [c.character_id for c in reply.character]==[character.character_id]
            assert reply==up(a,29,target,two) and not stars(pid,two)
            assert up(a,30,target,three).star==5 and not stars(pid,three)
            assert up(a,31,target,worn).status==pb.StatusCode_POSTER_MAX_STAR and stars(pid,worn)==1
            assert not listing(b)
            assert [progress(pid,k) for k in (60000245,60000246,80100702,80100704)]==[2,3,1,5]
            assert progress(peer,60000245)==progress(peer,60000246)==0
    finally:
        host.stop()
    host.start()
    try:
        a,again=enter(0)
        with a:
            assert again==pid and stars(pid,target)==5 and not stars(pid,three)
            assert len(listing(a))==112 and set(listing(a).values())=={pb.Reward}
            assert claim(a,10010).status==pb.StatusCode_POSTER_ILLUSTRATION_REWARDED
            assert all(amount(pid,tid)==first[tid]+units for tid,units in total.items())
    finally:
        host.stop()
    print("PASS 112 native poster gallery awards with real receipts, visible/owned gating, multi-star materials, protected presets, repeat and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:
        run(Path(state),parser.parse_args().resources)
