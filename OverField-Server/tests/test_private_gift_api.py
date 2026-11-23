"""Claim a catalog gift over HTTP, then check authenticated game inventory in an isolated realm."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import hashlib
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

from local_game_service import LocalGameService, pb, MsgId
from local_identity import LocalIdentity
from private_profiles import create_profile_server
from private_gift_service import TTL
from test_game_realm import login, join, next_msg
from test_private_profiles import request
from utils import db


def run(root, resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    game=LocalGameService(identity,root,resources,port=0)
    game.start()
    try:
        with create_profile_server(identity,port=0) as http:
            http.accounts.gifts.game=game
            port=http.server_port
            worker=Thread(target=http.serve_forever,kwargs={"poll_interval":0.01},daemon=True)
            worker.start()
            try:
                def post(op,form):
                    return request(port,"POST","/local/v1/accounts/"+op,form)
                code,published=request(port,"GET","/private/gifts")
                assert code==200 and len(published["gifts"])>=100
                assert request(port,"GET","/private/gifts",extra={"Authorization":"fixture"})[0]==403
                offer=next(g for g in published["gifts"] if g["shop"]==100 and g["grid"]==1)
                assert offer["title"] and offer["section"]
                credentials={"username":"giftusers","password":"test-only-password"}
                _,account=post("register",{**credentials,"nickname":"领取甲"})
                _,other=post("register",{**credentials,"username":"giftpeer","nickname":"领取乙"})
                key=uuid4().hex
                def form(grant,req=key):
                    return {"shop":offer["shop"],"grid":offer["grid"],"pool":offer["pool"],"request_id":req,"ticket":grant}
                assert post("claim-gift",form(account["ticket"]))[0]==401
                assert post("claim-gift",form(account["gift_ticket"]))==(409,{"error":"gift_character_required"})
                assert post("claim-gift",dict(form(account["gift_ticket"]),player=2))[0]==400
                first,_,_=login(game.port,account["ticket"])
                second,_,_=login(game.port,other["ticket"])
                with first,second:
                    join(first);join(second)
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        calls=list(pool.map(lambda _:post("claim-gift",form(account["gift_ticket"])),range(2)))
                    for code, receipt in calls:
                        assert code == 200 and receipt["ok"] and receipt["claimed"]
                        assert 0 < receipt["gift_expires_in"] <= TTL
                    # The expiry counts down between replies, even when the grant is identical.
                    assert calls[0][1]["gift_ticket"] == calls[1][1]["gift_ticket"]
                    received=next_msg(first,MsgId.PaySendGoodsNotice,pb.PaySendGoodsNotice)
                    assert received.shop_id==offer["shop"]
                    inventory=next_msg(first,MsgId.PackNotice,pb.PackNotice)
                    assert inventory.items
                    successor=calls[0][1]["gift_ticket"]
                    assert successor!=account["gift_ticket"]
                    assert post("claim-gift",form(account["gift_ticket"],uuid4().hex))==(409,{"error":"gift_ticket_used"})
                    assert post("claim-gift",form(successor))[0]==200
                    with game.lock:
                        counts=db.conn.execute("""SELECT s.slot,COUNT(*) FROM private_gift_claims c
                            JOIN private_slots s ON s.player_id=c.player GROUP BY s.slot""").fetchall()
                        assert counts==[(account["slot"],1)]
                    # Its own gift ticket can only deliver to the second account; game startup uses a distinct ticket.
                    _,signed=post("login",{**credentials,"username":"giftpeer"})
                    code,reply=post("claim-gift",form(signed["gift_ticket"],uuid4().hex))
                    assert code==200 and reply["claimed"]
                    assert post("launch",{"slot":signed["slot"],"ticket":signed["ticket"]})[0]==200
                    with game.lock,db.conn:
                        db.conn.execute("UPDATE private_gift_tickets SET expires=0 WHERE digest=?",
                                        (hashlib.sha256(reply["gift_ticket"].encode("ascii")).digest(),))
                    assert post("claim-gift",form(reply["gift_ticket"],uuid4().hex))==(401,{"error":"gift_login_required"})
                tools=Path(__file__).resolve().parents[2]/"client_tools"
                if (tools/"private_account_api.py").is_file():
                    sys.path.insert(0,str(tools))
                    import private_account_api as api
                    config={"url":f"http://127.0.0.1:{port}","localServer":False}
                    for remote in (False,True):
                        routed=dict(config,viaSsh=True,ssh="test-only") if remote else config
                        @contextmanager
                        def tunnel(channels,requested):
                            assert requested==18881
                            with socket.create_connection(("127.0.0.1",port),timeout=4) as connection:
                                yield connection
                        with patch.object(api.SshChannels,"open",tunnel):
                            listed=api.handle({"action":"gifts","server":routed})
                            assert listed["ok"] and offer in listed["gifts"]
                            signed=api.handle({"action":"login","server":routed,**credentials})
                            receipt=api.handle({"action":"claim-gift","server":routed,**form(signed["gift_ticket"],uuid4().hex)})
                            assert receipt["claimed"] and receipt["gift_ticket"]!=signed["gift_ticket"]
            finally:
                http.shutdown()
                worker.join()
    finally:
        game.stop()
    print("PASS paid-gift catalog, realm-bound and expiring gift login, concurrency, replay, rotation, online delivery and separate game login.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as temp:
        run(Path(temp),parser.parse_args().resources)

