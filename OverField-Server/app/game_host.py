"""Run the hosted private game worker (standalone CLI); combined deployments use the supervisor."""
import argparse
import logging
from pathlib import Path
import signal
from threading import Event

from local_game_service import LocalGameService
from local_identity import LocalIdentity


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--bind",default="127.0.0.1")
    parser.add_argument("--port",type=int,default=11033)
    parser.add_argument("--data-dir",type=Path,default=Path("data"))
    parser.add_argument("--resources",type=Path,default=Path(__file__).resolve().parent/"resources")
    args=parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    args.data_dir.mkdir(parents=True,exist_ok=True)
    service=LocalGameService(LocalIdentity(args.data_dir/"identity.sqlite3"),args.data_dir,args.resources,args.bind,args.port)
    closing=Event()
    for sign in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sign,lambda *_:closing.set())
    service.start()
    try:
        print(f"Private game listening on {args.bind}:{service.port} ({len(service.handlers)} gameplay handlers)",flush=True)
        closing.wait()
    finally:
        service.stop()


if __name__=="__main__":
    main()

