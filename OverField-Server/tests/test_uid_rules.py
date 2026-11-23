"""Migrate real old-style small player IDs without rewriting players' inventory or relationships."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from local_game_service import LocalGameService,pb
from local_identity import LocalIdentity
from realm_rules import RealmRules,command,load
from test_game_realm import login,join
from test_observed_initialization import call
from utils import db


def run(root,resources):
    ident=LocalIdentity(root/"identity.sqlite3")
    users=[ident.create_profile(n) for n in ("旧甲","旧乙","新丙")]
    game=LocalGameService(ident,root,resources,port=0)
    game.start()
    try:
        with game.lock,db.conn:
            for number,(slot,key) in enumerate(users[:2],1):
                db.conn.execute("INSERT INTO private_slots(player_id,slot) VALUES(?,?)",(number,slot))
                db.init_player(number)
                db.conn.execute("UPDATE players SET player_name=? WHERE player_id=?",(ident.profile_name(slot),number))
            db.conn.execute("INSERT INTO friend(player_id,friend_id,friend_status) VALUES(1,2,2)")
            db.conn.execute("INSERT INTO friend(player_id,friend_id,friend_status) VALUES(2,1,2)")
        game.stop();game.start()
        for i,user in enumerate(users):
            sock,_,uid=login(game.port,ident.issue_ticket(*user))
            with sock:
                main,scene=join(sock)
                assert main.player_id==scene.data.players[0].player_id==uid==1000001+i
                if i==0:
                    assert call(sock,"Friend",type=2).info[0].info.player_id==1000002
                    assert call(sock,"FriendSearch",search_args="1000002").data.player_id==1000002
                    assert call(sock,"FriendSearch",search_args="2").status==pb.StatusCode_FRIEND_SEARCH_FAIL
        game.stop()
        with closing(sqlite3.connect(root/"game.sqlite")) as conn:
            assert conn.execute("SELECT player_id,uid FROM realm_public_uids ORDER BY player_id").fetchall()==[(1,1000001),(2,1000002)]
            assert conn.execute("SELECT COUNT(*) FROM items WHERE player_id=1").fetchone()[0]>0
        command(root,SimpleNamespace(home_furniture_limit=150,world_max=23,public_max=85))
        assert (load(ident).home_furniture_limit,load(ident).world_max,load(ident).public_max)==(150,23,85)
        for data in ({"uid_start":2000000},{"public_min":90},{"public_furniture_scope":"xyz"},{"world_min":30}):
            try:command(root,SimpleNamespace(**data))
            except ValueError:pass
            else:raise AssertionError("Invalid rules accepted")
        game=LocalGameService(ident,root,resources,port=0)
        game.start()
        sock,_,uid=login(game.port,ident.issue_ticket(*users[0]))
        with sock:
            main,_=join(sock)
            assert uid==main.player_id==1000001 and game.realm_rules.world_max==23
    finally:game.stop()
    print("PASS stable old UIDs 1/2 become 1000001/1000002 on wire, old save and friends untouched, new UID allocation, mutable realm ranges and limits.")
if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:run(Path(tmp),ap.parse_args().resources)

