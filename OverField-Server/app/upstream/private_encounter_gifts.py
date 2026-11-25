"""Persist the treasure marker the native encounter preview reads before a battle."""
import random

from utils import db
from utils.res_loader import res


TEMPLATE = 300000


def setup(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS realm_encounter_gifts(
            player INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            scene INTEGER NOT NULL, encounter INTEGER NOT NULL, cycle INTEGER NOT NULL, template INTEGER NOT NULL,
            PRIMARY KEY(player,scene,encounter)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS realm_encounter_chest_channels(
            player INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            idx INTEGER NOT NULL, channel INTEGER NOT NULL, expires INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(player,idx)) WITHOUT ROWID;
    """)
    columns={row[1] for row in conn.execute("PRAGMA table_info(realm_encounter_chest_channels)")}
    if "expires" not in columns:
        conn.execute("ALTER TABLE realm_encounter_chest_channels ADD COLUMN expires INTEGER NOT NULL DEFAULT 0")


def previews(session, available):
    """Each entry is (id, last cooldown, already running on the previous release)."""
    if not available:
        return {}
    pid, scene = session.player_id,session.scene_id
    if len(available)==1:
        key=available[0][0]
        old=db.conn.execute("SELECT encounter,cycle,template FROM realm_encounter_gifts "
                            "WHERE player=? AND scene=? AND encounter=?",(pid,scene,key))
    else:
        old=db.conn.execute("SELECT encounter,cycle,template FROM realm_encounter_gifts "
                            "WHERE player=? AND scene=?",(pid,scene))
    persisted={key:(cycle,gift) for key,cycle,gift in old}
    result={}
    new=[]
    for key,cycle,legacy in available:
        stored=persisted.get(key)
        if stored is not None and stored[0]==cycle:
            gift=stored[1]
        else:
            # Older builds promised every started encounter a chest. An ongoing
            # pre-upgrade battle retains it when no preview was previously saved.
            gift=TEMPLATE if legacy or random.randrange(100)<session.realm_rules.monster_gift_percent else 0
            new.append((pid,scene,key,cycle,gift))
        result[key]=gift
    if new:
        db.conn.executemany("INSERT OR REPLACE INTO realm_encounter_gifts VALUES(?,?,?,?,?)",new)
    return result


def for_battle(session,key,cycle,started):
    return previews(session,[(key,cycle,bool(started))])[key]


def earned(session,index,stamp):
    ttl=res.index("TreasureBox","encounter_treasure_box")[TEMPLATE]["auto_disappear"]
    db.conn.execute("INSERT INTO realm_encounter_chest_channels(player,idx,channel,expires) VALUES(?,?,?,?)",
                    (session.player_id,index,session.channel_id,stamp+ttl))


def recorded_chest(session,index):
    return db.conn.execute("SELECT channel,expires FROM realm_encounter_chest_channels WHERE player=? AND idx=?",
                        (session.player_id,index)).fetchone()


def visible(session,index):
    row=recorded_chest(session,index)
    if row is None:
        return True
    from private_field_activities import now
    channel,expires=row
    return channel==session.channel_id and (not expires or int(now().timestamp())<expires)
