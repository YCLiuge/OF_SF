"""Actual player bootstrap, legacy migration, idempotence, concurrent grants and archive restore."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from migrate import backup, restore
from private_entitlements import CURRENCY_IDS, INITIAL_BALANCE, VERSION, grant
from test_game_realm import login
from test_observed_initialization import call


def item(conn,player,key):
    row=conn.execute("SELECT item_detail_blob FROM items WHERE player_id=? AND item_id=?",(player,key)).fetchone()
    return pb.ItemDetail.FromString(row[0]) if row else None


def set_amount(conn,player,key,n):
    data=item(conn,player,key)
    data.main_item.base_item.num=n
    conn.execute("UPDATE items SET item_detail_blob=? WHERE player_id=? AND item_id=?",(data.SerializeToString(),player,key))


def run(directory,resources):
    state=directory/"state"
    state.mkdir()
    identity=LocalIdentity(state/"identity.sqlite3")
    slots=[identity.create_profile(name) for name in ("收藏甲","收藏乙")]
    game=LocalGameService(identity,state,resources,port=0)
    database=state/"game.sqlite"
    game.start()
    try:
        definitions=game.entitlements
        assert {101,102,109,104,107,108,110,126}<=set(CURRENCY_IDS)
        assert not {1,10,11,115,116,6001,6002,6003,6004,6005,62101} & set(CURRENCY_IDS)
        assert len(definitions.furniture)==768 and len(definitions.emoji)==314
        assert 4300011 not in definitions.items and 4535 not in definitions.animations
        players=[]
        for s in slots:
            sock,_,pid=login(game.port,identity.issue_ticket(*s))
            players.append(pid)
            with sock:
                sock.sendall(encode(MsgId.PlayerMainDataReq,pb.PlayerMainDataReq().SerializeToString(),3))
                bag=set()
                unlocked=None
                for _ in range(130):
                    head,raw=receive(sock)
                    if head.msg_id==MsgId.PackNotice:
                        bag.update(i.main_item.item_id for i in pb.PackNotice.FromString(raw).items)
                    elif head.msg_id==MsgId.AchieveActionUnLockNotice:
                        unlocked=set(pb.AchieveActionUnLockNotice.FromString(raw).action_id_lst)
                    elif head.msg_id==MsgId.ChatUnLockExpressionNotice:
                        assert set(pb.ChatUnLockExpressionNotice.FromString(raw).expression_id)==definitions.emoji
                        break
                else:raise AssertionError("Missing native unlock notices")
                assert unlocked==definitions.menu_actions and {1200,101200,10700}<=unlocked
                assert set(definitions.items)<=bag
                assert call(sock,"SendAction",action_id=101200).status==pb.StatusCode_OK
                assert call(sock,"PopEmoji",emoji_id=1000).status==pb.StatusCode_OK
                assert call(sock,"PopEmoji",emoji_id=0xffffffff).status!=pb.StatusCode_OK
                assert call(sock,"SendChatMsg",type=1,expression=632045).status==pb.StatusCode_OK
        assert players[0]!=players[1]
    finally:
        game.stop()
    a,b=players
    clothing=4011012
    furnished=5040001
    with closing(sqlite3.connect(database)) as conn,conn:
        for pid in (a,b):
            assert all(item(conn,pid,key).main_item.base_item.num==INITIAL_BALANCE for key in CURRENCY_IDS)
            assert item(conn,pid,10).main_item.base_item.num==150 and item(conn,pid,11).main_item.base_item.num==800
        set_amount(conn,a,101,7)
        # Simulate a pre-upgrade player with currency above the grant, purchased colors,
        # a real home placement and old progression. They never reconnect during migration.
        conn.execute("DELETE FROM claimed_rewards WHERE player_id=? AND system='private_entitlements'",(b,))
        set_amount(conn,b,102,2_500_000)
        set_amount(conn,b,10,43)
        set_amount(conn,b,5040002,9)
        conn.execute("DELETE FROM items WHERE player_id=? AND item_id=?",(b,furnished))
        obj=pb.FurnitureDetailsInfo(furniture_id=17,furniture_item_id=furnished)
        conn.execute("INSERT INTO furnitures VALUES(?,?,?,?,?)",(9999,b,b,17,obj.SerializeToString()))
        outfit=item(conn,b,clothing)
        outfit.main_item.outfit.dye_schemes.add().is_un_lock=True
        raw_outfit=outfit.SerializeToString()
        conn.execute("UPDATE items SET item_detail_blob=? WHERE player_id=? AND item_id=?",(raw_outfit,b,clothing))
        conn.execute("UPDATE players SET level=23,exp=4711 WHERE player_id=?",(b,))
    game.start()
    try:
        with closing(sqlite3.connect(database)) as conn:
            assert item(conn,a,101).main_item.base_item.num==7
            assert all(item(conn,b,k).main_item.base_item.num==INITIAL_BALANCE for k in CURRENCY_IDS)
            assert item(conn,b,10).main_item.base_item.num==43
            assert item(conn,b,clothing).SerializeToString()==raw_outfit
            assert item(conn,b,furnished) is None and item(conn,b,5040002).main_item.base_item.num==9
            assert conn.execute("SELECT level,exp FROM players WHERE player_id=?",(b,)).fetchone()==(23,4711)
    finally:game.stop()
    with closing(sqlite3.connect(database)) as conn,conn:
        conn.execute("DELETE FROM claimed_rewards WHERE player_id=? AND system='private_entitlements'",(a,))
        # A failure partway through a grant must not suppress the next valid attempt.
        conn.execute("CREATE TRIGGER fail_test BEFORE UPDATE ON items WHEN NEW.item_id=101 BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with closing(sqlite3.connect(database)) as conn:
        try:
            with conn:grant(conn,a,definitions)
        except sqlite3.IntegrityError:pass
        else:raise AssertionError("Expected a transactional failure")
        assert item(conn,a,101).main_item.base_item.num==7
        assert not conn.execute("SELECT 1 FROM claimed_rewards WHERE player_id=? AND key=?",(a,VERSION)).fetchone()
        with conn:conn.execute("DROP TRIGGER fail_test")
    barrier=Barrier(2)
    def claim(_):
        with closing(sqlite3.connect(database)) as conn,conn:
            barrier.wait(timeout=10)
            return grant(conn,a,definitions)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim,(0,1)))==[False,True]
    with closing(sqlite3.connect(database)) as conn,conn:
        set_amount(conn,a,101,900_001)
    archive=directory/"realm.zip"
    backup(state,archive)
    restored=directory/"restored"
    restore(archive,restored)
    with closing(sqlite3.connect(restored/"game.sqlite")) as conn,conn:
        assert not grant(conn,a,definitions) and not grant(conn,b,definitions)
        assert item(conn,a,101).main_item.base_item.num==900_001
        assert item(conn,b,clothing).SerializeToString()==raw_outfit
    print("PASS native balances, cosmetics, action/chat/emoji notices, offline migration, retained dye/placements, rollback, concurrent grants and backup restore.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as temp:
        run(Path(temp),parser.parse_args().resources)
