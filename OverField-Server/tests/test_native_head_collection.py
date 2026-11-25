"""Check the complete retail avatar directory on fresh, upgraded and imported native roles."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from local_game_service import MsgId,pb
from local_game_wire import encode,receive
from private_entitlements import HEAD_VERSION,VERSION
from save_import_data import project
from test_game_realm import login
from test_native_gacha import call
from test_native_save_import import world,synthetic
from test_private_entitlements import item,set_amount
from test_private_profiles import request
from utils import db as game_db


def snapshot(port,ticket,expected):
    sock,_,uid=login(port,ticket)
    try:
        sock.sendall(encode(MsgId.PlayerMainDataReq,pb.PlayerMainDataReq().SerializeToString(),3))
        selected=None
        stickers={}
        for _ in range(300):
            head,data=receive(sock)
            if head.msg_id==MsgId.PlayerMainDataRsp:
                main=pb.PlayerMainDataRsp.FromString(data)
                assert main.player_id==uid
                selected=main.head
            elif head.msg_id==MsgId.PackNotice:
                for detail in pb.PackNotice.FromString(data).items:
                    value=detail.main_item
                    if value.item_tag==pb.EBagItemTag_Head:
                        assert value.item_id not in stickers and value.base_item.item_id==value.item_id
                        stickers[value.item_id]=value.base_item.num
            elif head.msg_id==MsgId.SceneDataNotice:
                break
        else:
            raise AssertionError("Missing native player/pack snapshot")
        assert selected in expected and all(stickers.get(key,0)>=1 for key in expected)
        for packet in (20,21):
            listing=call(sock,"UnlockHeadList",packet)
            assert listing.status==pb.StatusCode_OK and len(listing.heads)==len(expected)
            assert set(listing.heads)==expected
        return sock,uid,selected,stickers,main.player_label
    except BaseException:
        sock.close()
        raise


def run(root,resources):
    directory=json.loads((resources/"data/Head.json").read_bytes())["head"]["datas"]
    expected={r["head_i_d"] for r in directory}
    assert len(directory)==len(expected)==207 and sum(bool(r.get("is_show")) for r in directory)==40
    assert {41101,41102,41103,41104,9005,9404}<=expected
    accounts=({"username":"head.fixture.one","password":"test-only-avatar-01"},
              {"username":"head.fixture.archive","password":"test-only-avatar-02"})
    def account(action,fields,status=200):
        code,data=request(port,"POST","/local/v1/accounts/"+action,fields)
        assert code==status,(code,data.get("error"))
        return data
    with world(root,resources) as (port,host):
        first=account("register",{**accounts[0],"nickname":"头像甲"},201)
        sock,uid,selected,stickers,_=snapshot(host.port,first["ticket"],expected)
        with sock:
            assert selected==41101 and set(stickers.values())=={1}
            for packet,key in enumerate(sorted(expected),30):
                reply=call(sock,"ChangeHead",packet,head=key)
                assert (reply.status,reply.head)==(pb.StatusCode_OK,key)
            assert call(sock,"ChangeHead",250,head=0xffffffff).status!=pb.StatusCode_OK
            assert call(sock,"ChangeHead",251,head=9005).head==9005
            assert call(sock,"ChangeHead",251,head=9005).head==9005
    # Simulate an upgraded role with an existing currency receipt, a nondefault active
    # head and old stickers retained in several quantities.
    with closing(sqlite3.connect(root/"game.sqlite")) as db,db:
        assert db.execute("SELECT head FROM players WHERE player_id=?",(uid,)).fetchone()==(9005,)
        assert db.execute("DELETE FROM claimed_rewards WHERE player_id=? AND key=?",(uid,HEAD_VERSION)).rowcount==1
        assert db.execute("SELECT 1 FROM claimed_rewards WHERE player_id=? AND key=?",(uid,VERSION)).fetchone()
        db.executemany("DELETE FROM items WHERE player_id=? AND item_id=?",((uid,key) for key in expected-{41102,9005}))
        set_amount(db,uid,41102,7)
        set_amount(db,uid,9005,0)
        set_amount(db,uid,101,123)
    with world(root,resources) as (port,host):
        first=account("login",accounts[0])
        sock,_,selected,stickers,_=snapshot(host.port,first["ticket"],expected)
        with sock:
            assert selected==9005 and stickers[41102]==7 and stickers[9005]==1
            source=synthetic()
            source["decoded"]["main"]["frames"][0]["decoded"]["head"]=9404
            projected=project(source)
            assert projected["head"]==9404
            projected["stacks"].append([41102,8])
            projected["identity"]["public_label"]=projected["identity"].pop("uid")
            second=account("register",{**accounts[1],"nickname":"头像乙"},201)
            def restore():
                auth={"import_ticket":second["import_ticket"]}
                preview=account("import-preview",{**auth,"data":projected})
                assert preview["eligible"]
                account("import-apply",{**auth,"preview_id":preview["preview_id"]})
            restore()
            with closing(sqlite3.connect(root/"game.sqlite")) as db:
                assert db.execute("SELECT head FROM players p JOIN private_slots s USING(player_id) WHERE s.slot=?",(second["slot"],)).fetchone()==(9404,)
            # Older receipts record no prior sticker. Undo must still keep the new baseline grant.
            with host.lock,game_db.conn:
                assert game_db.conn.execute("UPDATE private_import_delta SET prior=NULL WHERE slot=? AND kind='items' AND k=41102",
                                            (second["slot"],)).rowcount==1
            account("import-undo",{"import_ticket":second["import_ticket"]})
            with closing(sqlite3.connect(root/"game.sqlite")) as db:
                owner,prior=db.execute("SELECT player_id,head FROM players JOIN private_slots USING(player_id) WHERE slot=?",
                                       (second["slot"],)).fetchone()
                assert prior==41101 and item(db,owner,41102).main_item.base_item.num==1
            restore()
            second=account("login",accounts[1])
            other,player_id,head,_,label=snapshot(host.port,second["ticket"],expected)
            with other:
                assert head==9404 and label==4444444 and player_id!=label
                assert call(sock,"OtherPlayerInfo",251,player_id=player_id).other_info.head==9404
                assert call(other,"ChangeHead",252,head=41103).head==41103
                assert call(sock,"OtherPlayerInfo",253,player_id=player_id).other_info.head==41103
    with world(root,resources) as (port,host):
        restored=account("login",accounts[1])
        sock,_,selected,_,_=snapshot(host.port,restored["ticket"],expected)
        with sock:
            assert selected==41103
        with closing(sqlite3.connect(root/"game.sqlite")) as db:
            assert item(db,uid,101).main_item.base_item.num==123
            assert item(db,uid,41102).main_item.base_item.num==7
            assert db.execute("SELECT count(*) FROM claimed_rewards WHERE key=?",(HEAD_VERSION,)).fetchone()==(2,)
    print("PASS 207/207 retail heads selectable; 207 stickers on fresh, upgraded and imported roles; earlier stocks, balances, avatar, undo, search profile and restart retained.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)
