"""Exercise player and operator password changes using temporary native and HTTP accounts."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from uuid import uuid4

import realm_admin
from local_identity import LocalIdentity
from local_game_service import LocalGameService
from private_profiles import create_profile_server
from test_game_realm import login,join
from test_private_profiles import request


def run(parent,resources):
    root=parent/"realm";root.mkdir()
    authority=LocalIdentity(root/"identity.sqlite3")
    game=LocalGameService(authority,root,resources,port=0)
    game.start()
    try:
        with create_profile_server(authority,port=0) as http:
            http.accounts.gifts.game=game;http.accounts.imports.game=game
            thread=Thread(target=http.serve_forever,kwargs={"poll_interval":0.01},daemon=True)
            thread.start()
            try:
                port=http.server_port
                def post(action,**body):return request(port,"POST","/local/v1/accounts/"+action,body)
                def admin(name,**data):return realm_admin.execute(root,"accounts."+name,data,None)
                user="回归测试Ab"; name="冰霜"; old="first password";new="second password"
                code,a=post("register",username=user,password=old,nickname=name)
                assert code==201
                code,o=post("register",username="不同玩家",password=old,nickname="雪地");assert code==201
                assert admin("find",field="username",value="不同玩家")["accounts"][0]["uid"] is None
                code,world=post("launch",slot=a["slot"],ticket=a["ticket"]);assert code==200
                code,ongoing=post("login",username=user,password=old);assert code==200
                sock,_,_=login(game.port,ongoing["ticket"])
                with sock:main,_=join(sock)
                code,prior=post("login",username=user,password=old);assert code==200
                offer=next(g for g in request(port,"GET","/private/gifts")[1]["gifts"] if g["shop"]==100 and g["grid"]==1)
                gift=dict(ticket=prior["gift_ticket"],shop=offer["shop"],grid=offer["grid"],pool=offer["pool"],request_id=uuid4().hex)
                assert post("change-password",username=user,password="incorrect",new_password=new)==(401,{"error":"invalid_credentials"})
                assert post("change-password",username=user,password=old,new_password=old)[0]==400
                assert post("change-password",username=user,password=old,new_password=new)==(200,{"changed":True,"reauthenticate":True})
                assert post("change-password",username=user,password=old,new_password="old replay")[0]==401
                assert post("login",username=user,password=old)[0]==401
                assert post("claim-gift",**gift)==(401,{"error":"gift_login_required"})
                assert post("import-undo",import_ticket=prior["import_ticket"])==(401,{"error":"import_login_required"})
                assert post("launch",slot=a["slot"],ticket=prior["ticket"])[0]==401
                assert post("launch",slot=a["slot"],resume=world["resume"])[0]==401
                assert login(game.port,world["ticket"]) is None
                code,fresh=post("login",username=user,password=new);assert code==200
                assert post("claim-gift",**(gift|{"ticket":fresh["gift_ticket"]}))[0]==200
                entry,=admin("find",field="username",value=user.lower())["accounts"]
                assert entry["nickname"]==name and entry["uid"]>0 and entry["password_version"]==1
                assert admin("find",field="nickname",value=name)["accounts"]==[entry]
                assert admin("find",field="uid",value=str(entry["uid"]))["accounts"]==[entry]
                params={k:entry[k] for k in ("account_ref","username","password_version")}
                reset="owner reset password"
                assert admin("reset",**params,new_password=reset)=={"changed":True,"reauthenticate":True}
                try:admin("reset",**params,new_password=new)
                except ValueError:pass
                else:raise AssertionError("Stale admin selection accepted")
                assert post("login",username=user,password=new)[0]==401
                assert post("launch",slot=a["slot"],ticket=fresh["ticket"])[0]==401
                assert post("login",username=user,password=reset)[0]==200
                assert post("launch",slot=o["slot"],ticket=o["ticket"])[0]==200
                candidates=("concurrent left","concurrent right")
                with ThreadPoolExecutor(max_workers=2) as pool:
                    codes=list(pool.map(lambda v:post("change-password",username=user,password=reset,new_password=v)[0],candidates))
                assert sorted(codes)==[200,401]
                assert admin("find",field="username",value=user)["accounts"][0]["password_version"]==3
            finally:
                http.shutdown();thread.join()
    finally:
        game.stop()
    print("PASS password rotation, concurrent update, owner search/reset, scoped ticket revocation, unaffected second account and progress.")


if __name__=="__main__":
    cli=argparse.ArgumentParser();cli.add_argument("--resources",required=True,type=Path)
    args=cli.parse_args()
    with TemporaryDirectory() as tmp:run(Path(tmp),args.resources)
