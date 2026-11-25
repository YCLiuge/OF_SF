"""Portable owner-controlled game rules, applied consistently at realm startup."""
import argparse
from dataclasses import asdict,dataclass,fields
import json
from pathlib import Path
import sqlite3
from contextlib import closing

from local_identity import LocalIdentity
from state_lock import exclusive_state


@dataclass(frozen=True)
class RealmRules:
    public_min:int=1
    public_max:int=99999
    uid_start:int=1000001
    home_furniture_limit:int=500
    visitor_furniture_limit:int=1
    public_furniture_limit:int=1
    public_furniture_scope:str="channel"
    world_min:int=1
    world_max:int=9999
    mp_team_members:int=16
    mp_team_name_length:int=20
    mp_team_limit:int=0
    game_connections:int=24
    starter_teachable_actions:int=1
    monster_gift_percent:int=20
    activity_fragment_percent:int=20
    activity_fragment_quantity:int=0
    ordinary_ground_drop_percent:int=20

    def validated(self):
        numbers=[value for k,value in asdict(self).items() if k!="public_furniture_scope"]
        if any(type(num) is not int or not 0<=num<0xffffffff for num in numbers):
            raise ValueError("Invalid integer for realm rules")
        if not 1<=self.game_connections<=512:
            raise ValueError("Choose 1..512 simultaneous game connections")
        if not 0 <= self.ordinary_ground_drop_percent <= 100:
            raise ValueError("Choose an ordinary ground drop chance between 0 and 100 percent")
        if not 0 <= self.activity_fragment_percent <= 100:
            raise ValueError("Choose an outdoor fragment chance between 0 and 100 percent")
        if not 0 <= self.monster_gift_percent <= 100:
            raise ValueError("Choose a monster gift chance between 0 and 100 percent")
        if self.starter_teachable_actions not in (0,1):
            raise ValueError("Choose 1 for permanent starter actions or 0 to learn teachable actions")
        if not 1<=self.public_min<=self.public_max<self.uid_start<0xffffffff:
            raise ValueError("Public channels must be positive, nonempty and below the first private home UID")
        if not 1<=self.world_min<=self.world_max:
            raise ValueError("World chat must have a positive nonempty range")
        if not 2<=self.mp_team_members<=16 or not 1<=self.mp_team_name_length<=20:
            raise ValueError("Choose 2..16 multiplayer members and 1..20 team-name characters")
        if self.public_furniture_scope not in ("channel","realm"):
            raise ValueError("Public furniture quota scope must be channel or realm")
        return self


def read(db):
    db.execute("CREATE TABLE IF NOT EXISTS realm_rules(id INTEGER PRIMARY KEY CHECK(id=1),settings TEXT NOT NULL)")
    row=db.execute("SELECT settings FROM realm_rules WHERE id=1").fetchone()
    return (RealmRules(**json.loads(row[0])) if row else RealmRules()).validated()


def load(identity):
    with identity._write() as db:
        return read(db)


def parser(commands):
    cmd=commands.add_parser("rules",help="Show or set private channel, UID, furniture and chat limits")
    for f in fields(RealmRules):
        cmd.add_argument("--"+f.name.replace("_","-"),dest=f.name,type=str if f.name=="public_furniture_scope" else int)


def configure(state,changes):
    # The server lock makes range changes exclusive with both a running realm
    # and migration. Reads share the profile lifecycle lock with backup/restore.
    purpose = "server" if changes else "profiles"
    with exclusive_state(state, purpose=purpose) as root:
        root.mkdir(parents=True,exist_ok=True)
        identity=LocalIdentity(root/"identity.sqlite3")
        if not changes:
            return load(identity)
        with identity._write() as db:
            current=read(db)
            updated=RealmRules(**(asdict(current)|changes)).validated()
            if current.uid_start!=updated.uid_start and (root/"game.sqlite").exists():
                with closing(sqlite3.connect((root/"game.sqlite").as_uri()+"?mode=ro",uri=True)) as game:
                    if game.execute("SELECT 1 FROM sqlite_schema WHERE name='players'").fetchone() and game.execute("SELECT 1 FROM players LIMIT 1").fetchone():
                        raise ValueError("UIDs have already been issued. Existing UIDs and home addresses must remain stable.")
            db.execute("INSERT OR REPLACE INTO realm_rules VALUES(1,?)",(json.dumps(asdict(updated)),))
        return updated


def command(state,args):
    changes={f.name:getattr(args,f.name,None) for f in fields(RealmRules) if getattr(args,f.name,None) is not None}
    print(json.dumps(asdict(configure(state,changes)),ensure_ascii=False,indent=2))
