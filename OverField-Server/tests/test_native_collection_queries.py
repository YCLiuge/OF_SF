"""Native ownership history, moon rewards and scene-specific emotion interactions."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from local_game_service import LocalGameService,pb
from local_identity import LocalIdentity
from test_game_realm import join,login
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def run(root,resources):
    ident=LocalIdentity(root/"identity.sqlite3")
    realm=LocalGameService(ident,root,resources,port=0)
    realm.start()
    def connect(profile):
        s,_,p=login(realm.port,ident.issue_ticket(*profile))
        join(s)
        return s,p
    def req(s,n,**values):
        return call(s,n,39,**values)
    try:
        mine=ident.create_profile("私服月亮")
        sock,pid=connect(mine)
        with sock:
            assert req(sock,"CollectMoon",moon_id=11001001).status==pb.StatusCode_BAD_REQ
            assert req(sock,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            assert not req(sock,"GetCollectMoonInfo",scene_id=1).collected_moon_ids
            assert req(sock,"CollectMoon",moon_id=10801001).status==pb.StatusCode_BAD_REQ
            assert req(sock,"CollectMoon",moon_id=11002001).status==pb.StatusCode_BAD_REQ
            first=req(sock,"CollectMoon",moon_id=11001001)
            assert first.status==pb.StatusCode_OK and first.moon_id==11001001
            assert [(i.main_item.item_id,i.main_item.base_item.num) for i in first.rewards]==[(124,5)]
            assert req(sock,"CollectMoon",moon_id=11001001).status==pb.StatusCode_BAD_REQ
            assert req(sock,"EmotionMoonInteract",emotion_moon_id=11001001).status==pb.StatusCode_BAD_REQ
            for _ in range(2):
                e=req(sock,"EmotionMoonInteract",emotion_moon_id=11101001)
                assert e.status==pb.StatusCode_OK and e.info.interacted and e.info.moon_id==11101001
            with realm.lock,db.conn:
                # A migrated role can retain identifiers in the original of-ps CSV record.
                db.conn.execute("INSERT INTO player_kv VALUES(?,'collected_moons','ids',?)",(pid,b'11001002,11002002'))
            assert req(sock,"CollectMoon",moon_id=11001002).status==pb.StatusCode_BAD_REQ
            info=req(sock,"GetCollectMoonInfo",scene_id=1)
            assert list(info.collected_moon_ids)==[11001001,11001002] and [(m.moon_id,m.interacted) for m in info.emotion_moons]==[(11101001,True)]
            info=req(sock,"GetCollectMoonInfo",scene_id=2)
            assert list(info.collected_moon_ids)==[11002002] and not info.emotion_moons
            items=set(req(sock,"GetCollectItemIds").item_ids)
            assert 101001 in items and 124 in items and not {11001001,11101001}.intersection(items)
            with realm.lock,db.conn:
                db.set_item_detail(pid,make_item(5012001,1,pid).SerializeToString(),5012001)
                db.set_item_detail(pid,make_item(5012001,0,pid).SerializeToString(),5012001)
            assert 5012001 in req(sock,"GetCollectItemIds").item_ids
        peer=ident.create_profile("独立月亮")
        sock,other=connect(peer)
        with sock:
            assert not req(sock,"GetCollectMoonInfo",scene_id=1).collected_moon_ids
            assert not req(sock,"GetCollectMoonInfo",scene_id=1).emotion_moons
            assert 5012001 not in req(sock,"GetCollectItemIds").item_ids
        realm.stop()
        realm.start()
        sock,_=connect(mine)
        with sock:
            assert list(req(sock,"GetCollectMoonInfo",scene_id=1).collected_moon_ids)==[11001001,11001002]
            assert 5012001 in req(sock,"GetCollectItemIds").item_ids
    finally:
        realm.stop()
    print("PASS collected item history, moon reward once, emotion interaction, imported record, per-world and per-role isolation.")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:
        run(Path(state),parser.parse_args().resources)
