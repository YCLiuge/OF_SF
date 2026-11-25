"""Realm-local operator grants and audit. Only the local file owner can change grants."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

from state_lock import exclusive_state


def setup(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS private_action_challenges (
        player INTEGER PRIMARY KEY, code INTEGER NOT NULL, expires INTEGER NOT NULL,
        attempts INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS gm_grants (
        slot TEXT PRIMARY KEY, generation INTEGER NOT NULL UNIQUE) WITHOUT ROWID""")
    conn.execute("""CREATE TABLE IF NOT EXISTS gm_audit (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, stamp INTEGER NOT NULL, actor INTEGER NOT NULL,
        via TEXT NOT NULL, verb TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS gm_denials (
        day INTEGER NOT NULL, player INTEGER NOT NULL, via TEXT NOT NULL, attempts INTEGER NOT NULL,
        PRIMARY KEY(day,player,via)) WITHOUT ROWID""")


def audit(conn, actor, via, verb, outcome, detail=""):
    return conn.execute("INSERT INTO gm_audit(stamp,actor,via,verb,outcome,detail) VALUES(?,?,?,?,?,?)",
                        (int(time.time()),actor,via,verb,outcome,detail)).lastrowid


def arguments(commands):
    group = commands.add_parser("gm", help="Manage only this realm's in-game administrators from the server host.")
    sub = group.add_subparsers(dest="gm_action", required=True)
    for verb in ("grant", "revoke"):
        parser = sub.add_parser(verb)
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--account", help="Registered local password account username")
        target.add_argument("--uid", type=int, help="Public UID on this realm")
    sub.add_parser("list")
    sub.add_parser("audit").add_argument("--limit", type=int, default=30)


def resolve(game, identity, account, uid):
    if account is not None:
        from realm_accounts import username_key
        key=username_key(account)
        has_accounts=identity.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='realm_accounts'").fetchone()
        row=identity.execute("SELECT slot FROM realm_accounts WHERE username=? COLLATE NOCASE", (key,)).fetchone() if has_accounts else None
    else:
        if type(uid) is not int or not 0 < uid < 0xffffffff:
            raise ValueError("Invalid public UID")
        row=game.execute("""SELECT p.slot FROM private_slots p
            LEFT JOIN realm_public_uids u ON u.player_id=p.player_id
            WHERE coalesce(u.uid,p.player_id)=?""",(uid,)).fetchone()
    if not row or not identity.execute("SELECT 1 FROM profiles WHERE slot=?",row).fetchone():
        raise ValueError("Account or public UID does not exist on this realm")
    return row[0]


def operator(state, action, *, account=None, uid=None, limit=30):
    state=Path(state).resolve()
    if not (state/"game.sqlite").is_file() or not (state/"identity.sqlite3").is_file():
        raise ValueError("Start this realm once before managing in-game administrators")
    if action not in ("grant","revoke","list","audit") or not 1 <= limit <= 200:
        raise ValueError("Unknown GM operator action or invalid audit limit (1..200)")
    with exclusive_state(state,purpose="profiles"), closing(sqlite3.connect(state/"game.sqlite",timeout=10)) as game, \
            closing(sqlite3.connect((state/"identity.sqlite3").as_uri()+"?mode=ro",uri=True)) as identity, game:
        game.execute("BEGIN IMMEDIATE")
        setup(game)
        if action=="audit":
            return {"records":[dict(zip(("seq","time","uid","source","command","outcome","detail"),row))
                    for row in game.execute("SELECT * FROM gm_audit ORDER BY seq DESC LIMIT ?",(limit,))],
                    "denied":[dict(zip(("utc_day","uid","source","attempts"),row))
                    for row in game.execute("SELECT * FROM gm_denials ORDER BY day DESC,player,via LIMIT ?",(limit,))]}
        accounts_table=identity.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='realm_accounts'").fetchone()
        def profile(slot):
            account_row=identity.execute("SELECT username FROM realm_accounts WHERE slot=?",(slot,)).fetchone() if accounts_table else None
            player=game.execute("""SELECT coalesce(u.uid,p.player_id) FROM private_slots p
                LEFT JOIN realm_public_uids u ON u.player_id=p.player_id WHERE p.slot=?""",(slot,)).fetchone()
            return {"account":account_row[0] if account_row else None,"uid":player[0] if player else None}
        if action=="list":
            return {"administrators":[profile(slot) for slot, in game.execute("SELECT slot FROM gm_grants ORDER BY generation")]}
        slot=resolve(game,identity,account,uid)
        subject=profile(slot)
        exists=bool(game.execute("SELECT 1 FROM gm_grants WHERE slot=?",(slot,)).fetchone())
        changed=exists!=(action=="grant")
        generation=audit(game,0,"operator",action,"changed" if changed else "unchanged",
                         json.dumps(subject,ensure_ascii=False,sort_keys=True))
        if changed:
            if action=="grant":
                game.execute("INSERT INTO gm_grants VALUES(?,?)",(slot,generation))
            else:
                game.execute("DELETE FROM gm_grants WHERE slot=?",(slot,))
        return dict(subject,granted=action=="grant",changed=changed)


def command(state,args):
    opts={k:getattr(args,k) for k in ("account","uid","limit") if hasattr(args,k)}
    print(json.dumps(operator(state,args.gm_action,**opts),ensure_ascii=True,sort_keys=True))
