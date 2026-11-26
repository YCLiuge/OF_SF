"""Open the authoritative game database and initialize the shared private-realm tables."""
from config import Config
from utils import db


def open_game_database(identity, data_dir):
    Config.DB_PATH = str(data_dir / "game.sqlite")
    from realm_accounts import initialize
    initialize(identity)
    db.init()
    db.conn.execute("ATTACH DATABASE ? AS authority",(str(identity.path),))
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS private_slots (
            player_id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS private_poses (
            player_id INTEGER NOT NULL, scene INTEGER NOT NULL, pos BLOB NOT NULL, rot BLOB NOT NULL,
            PRIMARY KEY(player_id,scene));
        CREATE TABLE IF NOT EXISTS player_kv (
            player_id INTEGER NOT NULL, ns TEXT NOT NULL, k TEXT NOT NULL, v BLOB,
            PRIMARY KEY(player_id,ns,k), FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS claimed_rewards (
            player_id INTEGER NOT NULL, system TEXT NOT NULL, key TEXT NOT NULL, claim_time INTEGER DEFAULT 0,
            PRIMARY KEY(player_id,system,key), FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS supply_box (
            player_id INTEGER PRIMARY KEY REFERENCES players(player_id), next_reward_time INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS private_wishes (
            player_id INTEGER NOT NULL REFERENCES players(player_id), shop INTEGER NOT NULL,
            grid INTEGER NOT NULL, pool INTEGER NOT NULL, created INTEGER NOT NULL,
            PRIMARY KEY(player_id,shop,grid));
        CREATE TABLE IF NOT EXISTS private_boxes (
            player_id INTEGER NOT NULL REFERENCES players(player_id), zone INTEGER NOT NULL, dynamic INTEGER NOT NULL,
            idx INTEGER NOT NULL, template INTEGER NOT NULL, data BLOB, refresh INTEGER DEFAULT 0, claimed INTEGER DEFAULT 0,
            PRIMARY KEY(player_id,zone,dynamic,idx));
        CREATE TABLE IF NOT EXISTS private_locations (
            player_id INTEGER PRIMARY KEY, scene INTEGER NOT NULL);
    """)
    import realm_mail
    realm_mail.schema(db.conn, attached=True)
    db.conn.execute("""CREATE TABLE IF NOT EXISTS realm_mail_cursor (
        player_id INTEGER PRIMARY KEY, last_batch INTEGER NOT NULL)""")
    from private_activity_fragments import setup as setup_activity_fragments
    setup_activity_fragments(db.conn)
    import private_abyss
    private_abyss.setup()
    from private_secondary_password import setup as setup_secondary_password
    setup_secondary_password(db.conn)
    from private_redemption import setup as setup_redemption
    setup_redemption()
    import realm_reports
    realm_reports.setup(db.conn)
    from private_character_life import setup as setup_character_life
    setup_character_life()
    from private_photos import setup as setup_photos
    setup_photos(db.conn)
    from private_field_activities import setup as setup_field_activities
    setup_field_activities(db.conn)
    from private_ground_loot import setup as setup_ground_loot
    setup_ground_loot(db.conn)
    from private_encounter_gifts import setup as setup_encounter_gifts
    setup_encounter_gifts(db.conn)
