"""Owner-only exact search and guarded reset of independently registered realm accounts."""
from contextlib import closing
from pathlib import Path
import secrets
import sqlite3

from config_revision import ConfigChanged
from local_identity import LocalIdentity, SLOT
from realm_accounts import N, R, P, SALT_SIZE, derive, initialize, password_bytes, username_spelling


def find(state, field, value):
    if not isinstance(value,str) or not 1<=len(value)<=80:
        raise ValueError("请输入账号、游戏名或公开 UID。")
    auth=Path(state)/"identity.sqlite3"
    if not auth.is_file():
        return {"accounts":[]}
    with closing(sqlite3.connect(auth.as_uri()+"?mode=ro",uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_schema WHERE name='realm_accounts'").fetchone():
            return {"accounts":[]}
        game=Path(state)/"game.sqlite"
        tables=set()
        if game.is_file():
            db.execute("ATTACH DATABASE ? AS game",(game.as_uri()+"?mode=ro",))
            tables={r[0] for r in db.execute("SELECT name FROM game.sqlite_schema WHERE type='table'")}
        played={'players','private_slots'}<=tables
        labels='realm_public_uids' in tables and played
        joins=(" LEFT JOIN game.private_slots s USING(slot) LEFT JOIN game.players g USING(player_id)" if played else "")
        if labels:
            joins+=" LEFT JOIN game.realm_public_uids u ON u.player_id=g.player_id"
        uid="coalesce(u.uid,g.player_id)" if labels else "g.player_id" if played else "NULL"
        nickname="coalesce(g.player_name,p.nickname)" if played else "p.nickname"
        if field=="username":
            condition="a.username=? COLLATE NOCASE";args=(username_spelling(value),)
        elif field=="uid":
            if not value.isascii() or not value.isdecimal() or not 1<=int(value)<2**32:
                raise ValueError("公开 UID 须为正整数。")
            if not played:return {"accounts":[]}
            num=int(value)
            condition=("s.player_id IN (SELECT player_id FROM game.realm_public_uids WHERE uid=? UNION ALL SELECT ? WHERE NOT EXISTS (SELECT 1 FROM game.realm_public_uids WHERE player_id=?))" if labels else "s.player_id=?")
            args=(num,num,num) if labels else (num,)
        elif field=="nickname":
            from player_name_index import functions,signup
            key=signup(value); functions(db)
            if 'private_player_names' in tables and played:
                condition="""(s.player_id IN (SELECT uid FROM game.private_player_names WHERE signup=?
                    UNION ALL SELECT uid FROM game.private_player_names WHERE digest IS NULL AND of_nick_signup(raw)=?)
                    OR (g.player_name IS NULL AND p.name_key=?))"""
                args=(key,key,key)
            else:
                condition="of_nick_signup("+nickname+")=?";args=(key,)
        else:
            raise ValueError("请选择账号、游戏名或公开 UID。")
        has_epochs=db.execute("SELECT 1 FROM main.sqlite_schema WHERE name='realm_password_epochs'").fetchone()
        epoch="coalesce(e.epoch,0)" if has_epochs else "0"
        authority=" FROM realm_accounts a JOIN profiles p USING(slot)"
        if has_epochs:authority+=" LEFT JOIN realm_password_epochs e ON e.slot=a.slot"
        rows=db.execute("SELECT a.username,a.slot,"+nickname+","+uid+","+epoch+authority+joins+" WHERE "+condition+" ORDER BY a.username LIMIT 25",args)
        return {"accounts":[dict(username=name,account_ref=slot,nickname=nick,uid=ident,password_version=version) for name,slot,nick,ident,version in rows]}


def reset(state,data):
    user=username_spelling(data['username'])
    slot=data['account_ref']; version=data['password_version']
    if not SLOT.fullmatch(slot):raise ValueError("请先查找并选择账号。")
    value=password_bytes(data['new_password'])
    path=Path(state)/"identity.sqlite3"
    if not path.is_file():raise FileNotFoundError("账号不存在。")
    # Derive outside the write transaction; require the exact selection/version at commit.
    salt=secrets.token_bytes(SALT_SIZE); verifier=derive(value,salt)
    identity=LocalIdentity(path)
    initialize(identity)
    with identity._write() as db:
        updated=db.execute("""UPDATE realm_accounts SET salt=?,verifier=?,n=?,r=?,p=?
            WHERE slot=? AND username=? COLLATE NOCASE AND coalesce(
                (SELECT epoch FROM realm_password_epochs WHERE slot=?),0)=?""",
            (salt,verifier,N,R,P,slot,user,slot,version))
        if updated.rowcount!=1:raise ConfigChanged("账号或密码已变更，请重新查询。")
    return {"changed":True,"reauthenticate":True}
