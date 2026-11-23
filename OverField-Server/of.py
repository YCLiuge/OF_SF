
"""Manage the standalone private OverField server from its distribution directory."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import platform
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import uuid
import urllib.request

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
machine = platform.machine().lower()
if machine not in ("amd64", "x86_64") or sys.maxsize <= (1 << 32):
    raise SystemExit("This release requires an x86-64 Python runtime.")
platform_vendor = "win64" if sys.platform == "win32" else "linux-x86_64"
sys.path[:0] = [str(APP), str(APP / "upstream"), str(ROOT / "vendor" / platform_vendor),
                str(ROOT / "vendor" / "common")]
from local_identity import LocalIdentity
from private_profiles import create_profile_server
from state_lock import exclusive_state


def endpoint_config():
    cfg = {
        "bind": os.getenv("OF_BIND", "127.0.0.1"),
        "game_host": os.getenv("OF_GAME_HOST", "127.0.0.1"),
        "http_port": int(os.getenv("OF_HTTP_PORT", "18881")),
        "game_port": int(os.getenv("OF_GAME_PORT", "11033")),
    }
    if ipaddress.ip_address(cfg["bind"]).version != 4:
        raise ValueError("The game server requires an IPv4 bind address.")
    if cfg["http_port"] == cfg["game_port"] or any(not 1 <= cfg[k] <= 65535 for k in ("http_port","game_port")):
        raise ValueError("Choose two distinct ports in 1..65535.")
    return cfg


def optional_tls():
    cert, key = os.getenv("OF_TLS_CERT"), os.getenv("OF_TLS_KEY")
    if bool(cert) != bool(key):
        raise ValueError("OF_TLS_CERT and OF_TLS_KEY must be set together.")
    if cert:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(cert, key)
        return tls


def control_files(state):
    return state.parent / ("." + state.name + ".active")


def request_stop(state, *, quiet=False):
    active = control_files(state)
    try:
        run_id = uuid.UUID(active.read_text(encoding="ascii").strip())
    except FileNotFoundError:
        raise ValueError("No active server recorded at the selected state directory.") from None
    command = active.with_name(active.name + "." + run_id.hex + ".stop")
    command.touch(exist_ok=True)
    if not quiet:
        print("Local stop requested.")


def serve(state):
    from game_dispatch import GameDispatch
    from local_game_service import LocalGameService
    cfg = endpoint_config()
    shutdown = threading.Event()
    def stop(signum, frame):
        shutdown.set()
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    with exclusive_state(state):
        state.mkdir(parents=True, exist_ok=True)
        identity = LocalIdentity(state / "identity.sqlite3")
        game = LocalGameService(identity, state, ROOT / "resources", bind=cfg["bind"], port=cfg["game_port"])
        tls = optional_tls()
        registration = os.getenv("OF_REGISTRATION", "closed")
        if registration not in ("open","closed"):
            raise ValueError("OF_REGISTRATION must be open or closed.")
        extra = [x.strip() for x in os.getenv("OF_HTTP_HOSTS","").split(",") if x.strip()]
        with create_profile_server(identity, bind_host=cfg["bind"], port=cfg["http_port"],
                    game_host=cfg["game_host"], game_port=cfg["game_port"], allowed_hosts=extra,
                    allow_registration=registration=="open", tls_context=tls) as http:
            scheme = "https" if tls else "http"
            default_origin = f"{scheme}://{cfg['game_host']}:{cfg['http_port']}"
            http.stages = game.stages
            http.accounts.gifts.game = game
            http.native_accounts = game.native_accounts
            http.game_dispatch = GameDispatch(os.getenv("OF_DISPATCH_URL") or default_origin,
                                               cfg["game_host"], cfg["game_port"],
                                               client_root=os.getenv("OF_CLIENT_ROOT") or None)
            from realm_maintenance import SelectedFeed
            selected_remote = game.remote_password or game.remote_realm
            http.game_dispatch.maintenance = SelectedFeed(selected_remote) if selected_remote else http.maintenance
            game.start()
            thread = threading.Thread(target=http.serve_forever, name="private-profile-http", daemon=True)
            run_id=uuid.uuid4().hex
            active=control_files(state)
            command=active.with_name(active.name+"."+run_id+".stop")
            pending=active.with_name(active.name+"."+run_id+".tmp")
            try:
                thread.start()
                pending.write_text(run_id,encoding="ascii")
                pending.replace(active)
                print("Server ready: %s profile %s:%d, game TCP %s:%d, advertised %s, registration %s" % (
                    "HTTPS" if tls else "HTTP", cfg["bind"], cfg["http_port"], cfg["bind"],
                    cfg["game_port"], cfg["game_host"], registration), flush=True)
                print("Data: " + str(state.resolve()),flush=True)
                while not shutdown.wait(0.25) and not command.exists():
                    if not thread.is_alive():
                        raise RuntimeError("Profile listener exited unexpectedly.")
            finally:
                if thread.is_alive():
                    http.shutdown()
                    thread.join()
                try:
                    game.stop()
                finally:
                    pending.unlink(missing_ok=True)
                    command.unlink(missing_ok=True)
                    active.unlink(missing_ok=True)


def make_profile(state, nickname, output):
    # Local administrators can provision while the realm is serving; migrations take both locks. Keep a read/write pair of data and
    # credential consistent, and never print the recovery key to logs.
    from private_profiles import REALM
    with exclusive_state(state, purpose="profiles"):
        state.mkdir(parents=True,exist_ok=True)
        identity = LocalIdentity(state / "identity.sqlite3")
        file = None
        written = False
        try:
            with identity.creating_profile(nickname) as (slot, key, display):
                file = output if output is not None else state.parent / "profiles" / (slot + ".json")
                file.parent.mkdir(parents=True,exist_ok=True)
                with os.fdopen(os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),"w",encoding="utf-8") as dest:
                    written = True
                    json.dump({"realm": REALM, "slot": slot, "profile_key": key, "nickname": display},dest,ensure_ascii=False)
                    dest.write("\n");dest.flush();os.fsync(dest.fileno())
        except BaseException:
            if written: file.unlink(missing_ok=True)
            raise
    print("Private player created. Credentials file: " + str(file.resolve()))
    return file


def health():
    cfg=endpoint_config()
    local = cfg["bind"] if cfg["bind"] != "0.0.0.0" else "127.0.0.1"
    tls = bool(os.getenv("OF_TLS_CERT"))
    host = os.getenv("OF_HEALTH_HOST",local)
    url = ("https" if tls else "http") + "://" + host + ":" + str(cfg["http_port"]) + "/local/v1/region"
    with urllib.request.urlopen(url,timeout=4) as response:
        region = json.load(response)
    if region.get("game_port") != cfg["game_port"] or region.get("game_host") != cfg["game_host"]:
        raise RuntimeError("Region does not advertise the configured game service.")
    with socket.create_connection((local,cfg["game_port"]),timeout=4):
        pass
    print("Profile API and game TCP listener are ready.")


def self_test():
    tests = ROOT / "tests"
    paths = [APP, APP/"upstream", ROOT/"vendor"/platform_vendor, ROOT/"vendor"/"common", tests]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str,paths)), PYTHONDONTWRITEBYTECODE="1")
    for key in list(env):
        if key.startswith(("OF_REMOTE_","OF_QUICKSDK_")):
            env.pop(key)
    for script in ("test_realm_admin.py", "test_private_profiles.py", "test_migrate.py", "test_game_dispatch.py","test_local_picker.py",
                   "test_game_realm.py", "test_game_archive.py", "test_game_dungeon.py","test_dungeon_updates.py","test_native_appearance.py","test_native_activity_rewards.py",
                   "test_native_first_quest.py", "test_story_entry.py","test_private_channels.py","test_realm_maintenance.py","test_online_identity.py","test_remote_realm.py","test_remote_password.py","test_stage_diagnostics.py","test_observed_initialization.py","test_home_chairs.py","test_native_intervals.py","test_plaza_actions.py","test_uid_rules.py","test_realm_accounts.py","test_native_friends.py","test_native_chat.py","test_native_shop.py","test_native_crafts.py","test_native_gacha.py","test_native_inventory.py","test_native_mp_teams.py","test_native_equipment.py",
                   "test_private_entitlements.py","test_private_gift_api.py","test_private_gift_shop.py","test_story_starter.py","test_hud_queries.py","test_faye_dialog.py","test_season_catalog.py",
                   "test_native_boss_rush.py", "test_native_restaurant.py", "test_native_world_levels.py",
                   "test_native_home_visibility.py", "test_native_bless_tree.py", "test_native_bless_ladder.py", "test_native_events.py",
                   "test_native_recruit_entry.py", "test_native_badge_tree.py", "test_native_battle_pass.py", "test_native_newcomer.py",
                   "test_profile_issuance.py","test_explore_dispatch.py"):
        extras = [] if script in ("test_private_profiles.py", "test_migrate.py", "test_game_dispatch.py","test_local_picker.py") else ["--resources", str(ROOT/"resources")]
        subprocess.run([sys.executable, "-B", str(tests/script), *extras], env=env, check=True)


def main():
    if os.name=="posix": os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state",type=Path,default=Path(os.getenv("OF_STATE",ROOT/"state"/"realm")))
    commands=parser.add_subparsers(dest="command")
    import realm_admin
    realm_admin.arguments(commands)
    commands.add_parser("start"); commands.add_parser("health"); commands.add_parser("stop"); commands.add_parser("test")
    from realm_maintenance import arguments as add_maintenance
    add_maintenance(commands)
    create=commands.add_parser("create-player")
    create.add_argument("--nickname")
    create.add_argument("--ask-name",action="store_true")
    create.add_argument("--out",type=Path)
    for command in ("bind-sdk", "unbind-sdk"):
        linking=commands.add_parser(command,help="Link or unlink this realm's profile and a known SDK user ID")
        linking.add_argument("--profile",required=True,type=Path)
    exporting=commands.add_parser("issue-profile",aliases=["export-player"],help="Issue a new game key for an existing character in a stopped realm")
    by=exporting.add_mutually_exclusive_group()
    by.add_argument("--nickname");by.add_argument("--slot")
    exporting.add_argument("--out",type=Path,required=True)
    join=commands.add_parser("check-player")
    join.add_argument("--url",default="http://127.0.0.1:18881")
    join.add_argument("--profile", required=True, type=Path)
    join.add_argument("--trusted-vpn", action="store_true")
    for name in ("backup","restore"):
        cmd=commands.add_parser(name)
        cmd.add_argument("archive",type=Path)
    import realm_rules
    realm_rules.parser(commands)
    args=parser.parse_args()
    state=args.state.resolve()
    if args.command=="admin":
        return realm_admin.command(state,args,request_stop)
    if args.command in ("announce","edit-notice","withdraw-notice","publish-release","withdraw-release","maintenance-info"):
        from realm_maintenance import owner_command
        return owner_command(state,args)
    if args.command=="rules":return realm_rules.command(state,args)
    if args.command in (None,"start"):return serve(state)
    if args.command=="test":return self_test()
    if args.command=="health":return health()
    if args.command=="stop":return request_stop(state)
    if args.command=="create-player":
        nickname=(input("Nickname (Enter for automatic): ").strip() or None) if args.ask_name else args.nickname
        make_profile(state,nickname,args.out)
        return
    if args.command in ("bind-sdk","unbind-sdk"):
        from getpass import getpass
        from online_identity import bind
        from local_identity import LocalIdentity
        from state_lock import exclusive_state
        if not (state/"identity.sqlite3").is_file():
            raise ValueError("First create a profile on this realm")
        uid=getpass("QuickSDK UID (hidden input): ").strip()
        with exclusive_state(state, purpose="profiles"):
            bind(LocalIdentity(state/"identity.sqlite3"),args.profile,uid,remove=args.command=="unbind-sdk")
        print("Private realm binding updated.")
        return
    if args.command in ("issue-profile","export-player"):
        from profile_issuance import issue_profile
        issue_profile(state,args.out,nickname=args.nickname,slot=args.slot)
        return
    if args.command=="check-player":
        from join_check import check_player
        check_player(args.url,args.profile,trusted_vpn=args.trusted_vpn)
        return
    import migrate
    return migrate.main([args.command,"--data",str(state),"--archive",str(args.archive.resolve())])


if __name__=="__main__":
    try:
        sys.exit(main())
    except (ValueError,OSError,RuntimeError,sqlite3.Error) as error:
        print(str(error),file=sys.stderr)
        sys.exit(1)

