"""Command-line access to the same Windows player launcher used by the desktop UI."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from native_launch import is_running
from realm_addresses import BOOK

API = "overfield.launcher/v1"
ACTIONS = {
    "schema": "Describe commands and data format",
    "status": "Report installation and native game status",
    "servers": "List saved connection settings",
    "save": "Bookmark a server without preparing or starting the game",
    "prepare": "Prepare the isolated game directory and save settings",
    "play": "Prepare and launch the native game",
}
ENGINE = {"save": "bookmark", "prepare": "prepare", "play": "launch"}
FIELDS = ("url", "localServer", "remoteSsh", "remoteSshPort", "remoteProfile", "remotePairFirst")


def read_json(file, default):
    if not file.exists():
        return default
    return json.loads(file.read_bytes())


def run(args):
    home = args.home.resolve()
    if args.action == "schema":
        return {"commands": ACTIONS, "modes": ["local", "gateway", "ssh"],
                "defaultEndpoint": "http://127.0.0.1:18881",
                "playTicket": "optional JSON on stdin with --account-stdin"}
    if args.action == "status":
        return {"home": str(home), "prepared": (home / "Client/.of-private-client.json").is_file(),
                "gameRunning": is_running(home), "runtime": "net10.0-windows-x64",
                "authentication": {"private": "realm-account", "retailChannel": "publisher-sdk"}}
    if args.action == "servers":
        rows = read_json(home / BOOK, [])
        if not isinstance(rows, list):
            raise ValueError("地址簿格式错误。")
        return {"servers": [{k: row[k] for k in FIELDS if k in row} for row in rows if isinstance(row, dict)]}
    if (args.mode == "ssh") != bool(args.ssh):
        raise ValueError("SSH 模式需要指定 --ssh，其他模式请省略。")
    source = args.source
    if not source and args.action != "save":
        config = read_json(home / "client-config.json", {})
        source = config.get("source") if isinstance(config, dict) else None
        if not source:
            raise ValueError("首次准备客户端请使用 --source 指定原版安装目录或介质。")
    command = [sys.executable, "-B", str(HERE / "native_launch.py"), ENGINE[args.action],
               "--home", str(home), "--source", str(source or home), "--endpoint", args.endpoint]
    if args.mode == "local":
        command.append("--local-server")
    for opt, val in (("--remote-ssh", args.ssh), ("--remote-ssh-port", args.ssh_port),
                     ("--remote-profile", args.profile)):
        if val is not None:
            command.extend([opt, str(val)])
    if args.account_stdin:
        if args.action != "play":
            raise ValueError("--account-stdin 只用于启动游戏。")
        command.append("--account-stdin")
    completed = subprocess.run(command, stdin=None if args.account_stdin else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                               errors="replace", env={**os.environ, "PYTHONUTF8": "1"})
    if completed.returncode:
        raise ValueError(completed.stderr.strip() or "启动器操作失败。")
    return {"action": args.action, "gameRunning": is_running(home)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=HERE.parent)
    commands = parser.add_subparsers(dest="action", required=True)
    for name, help_text in ACTIONS.items():
        cmd = commands.add_parser(name, help=help_text)
        if name in ENGINE:
            cmd.add_argument("--endpoint", default="http://127.0.0.1:18881")
            cmd.add_argument("--mode", choices=("local", "gateway", "ssh"), default="local")
            cmd.add_argument("--source", type=Path)
            cmd.add_argument("--ssh")
            cmd.add_argument("--ssh-port", type=int)
            cmd.add_argument("--profile", type=Path)
            cmd.add_argument("--account-stdin", action="store_true")
    args = parser.parse_args()
    try:
        result = {"apiVersion": API, "ok": True, "result": run(args)}
    except (OSError, ValueError, RuntimeError) as exc:
        result = {"apiVersion": API, "ok": False, "error": {"code": "launch_failed", "message": str(exc)}}
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
