"""Two native clients view masked profiles and persist private friend labels, gifts and cooldowns."""
import argparse
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_social_profile import daily, now
from test_game_realm import login, join
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profiles = [identity.create_profile(label) for label in ("社交甲","社交乙")]
    host = LocalGameService(identity, root, resources, port=0)

    def connect(i):
        sock, _, pid = login(host.port, identity.issue_ticket(*profiles[i]))
        data, _ = join(sock)
        return sock, pid, data

    def info(s, other):
        return call(s, "OtherPlayerInfo", 200, player_id=other)

    def fields(s, other, seq, *values):
        return call(s, "SetFriendExtInfo", seq, player_id=other, data=values)

    def gift(s, other, seq, item=1503):
        return call(s, "UseItemFriendIntimacy", seq, friend_id=other, item_id=item)

    def remaining(pid):
        raw = db.get_item_detail(pid,1503)
        return pb.ItemDetail.FromString(raw).main_item.base_item.num

    alias = dict(type=1,value="队友")
    tag = dict(type=2,int_value=2002)
    background = dict(type=3,int_value=2001)
    host.start()
    try:
        a, owner, _ = connect(0)
        b, peer, _ = connect(1)
        with a, b:
            with host.lock, db.conn:
                for column, value in (("sign","测试签名"),("birthday","2000-03-15"),("account_type",3)):
                    db.set_players_info(owner,column,value)
                db.set_item_detail(owner,make_item(1503,10,owner).SerializeToString(),1503)
            assert info(b,owner).other_info.birthday == "2000-03-15"
            assert call(a,"ChangeIsHideBirthday",201).status == pb.StatusCode_OK
            assert call(a,"ChangeIsHideBirthday",201).status == pb.StatusCode_OK
            assert call(a,"ChangeHideType",202,hide_type=pb.HideType_SIGN_Type).hide_value == 2
            assert call(a,"ChangeHideType",202,hide_type=pb.HideType_SIGN_Type).hide_value == 2
            assert call(a,"ChangeHideType",203,hide_type=pb.HideType_Account_Type).hide_value == 3
            assert call(a,"ChangeHideType",204,hide_type=3).status == pb.StatusCode_BAD_REQ
            assert call(a,"ChangePlayerSex",205,sex=pb.ESexType_Female).sex == pb.ESexType_Female
            assert call(a,"ChangePlayerSex",206,sex=88).status == pb.StatusCode_BAD_REQ
            view = info(b,owner).other_info
            assert view.hide_value == 3 and not view.birthday and not view.sign and not view.account_type
            assert view.sex == pb.ESexType_Female
            assert not call(b,"FriendSearch",207,search_args=str(owner)).data.sign
            assert info(a,0).status != pb.StatusCode_OK
            assert fields(a,peer,208,alias).status == gift(a,peer,209).status == pb.StatusCode_FRIEND_NOT_EXIST
            assert call(a,"FriendAdd",210,player_id=peer).status == pb.StatusCode_OK
            assert call(b,"FriendHandle",211,player_id=owner,is_agree=True).status == pb.StatusCode_OK
            assert fields(a,peer,212,alias,tag).status == pb.StatusCode_FRIEND_INTIMACY_NOT_ENOUGH
            assert not info(a,peer).alias
            assert gift(a,peer,213,101).status == pb.StatusCode_BAD_REQ
            assert gift(a,peer,214).intimacy == gift(a,peer,214).intimacy == 20
            for n in range(4):
                assert gift(a,peer,215+n).intimacy == (n+2)*20
            assert fields(a,peer,220,alias,tag,background).status == pb.StatusCode_OK
            other = info(a,peer)
            assert (other.alias, other.friend_tag, other.friend_background, other.friend_intimacy) == ("队友",2002,2001,100)
            assert info(b,owner).friend_intimacy == 100 and not info(b,owner).alias
            assert remaining(owner)==5
            assert fields(a,peer,221,dict(type=1,value="替换"),dict(type=3,int_value=987654)).status == pb.StatusCode_CFG_NOT_FOUND
            assert info(a,peer).alias == "队友"
            with host.lock, db.conn:
                db.conn.execute("UPDATE player_kv SET v=? WHERE player_id=? AND ns='friend_gifts'",(daily(owner)[0]+"/999",owner))
            assert gift(a,peer,222).status == pb.StatusCode_FRIEND_INTIMACY_DAY_GIFT_LIMIT and remaining(owner)==5
            with patch("private_social_profile.now",return_value=now()+timedelta(days=1)):
                assert gift(a,peer,223).intimacy == 120 and remaining(owner)==4
            with host.lock,db.conn:
                for left,right in ((owner,peer),(peer,owner)):
                    db.set_friend_info(left,right,"friend_intimacy",99999)
            assert gift(a,peer,224).status == pb.StatusCode_FRIEND_INTIMACY_LIMIT and remaining(owner)==4
    finally:
        host.stop()
    host.start()
    try:
        a, owner, main = connect(0)
        b, peer, _ = connect(1)
        with a,b:
            assert main.hide_value==3 and main.is_hide_birthday and main.sex==pb.ESexType_Female
            assert info(a,peer).alias=="队友" and not info(b,owner).other_info.birthday
            assert call(a,"ChangeHideType",230,hide_type=pb.HideType_SIGN_Type).hide_value==1
            assert info(b,owner).other_info.sign=="测试签名"
            assert call(a,"FriendDel",231,player_id=peer).status==pb.StatusCode_OK
            assert not info(a,peer).alias and gift(a,peer,232).status==pb.StatusCode_FRIEND_NOT_EXIST
    finally:
        host.stop()
    print("PASS native profile visibility, private alias and labels, intimacy costs/limits/day change, retries, two roles and restart.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as root:
        run(Path(root),parser.parse_args().resources)
