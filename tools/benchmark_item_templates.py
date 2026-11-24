"""Compare every 1.1.12 item factory result with another checkout, including bulk grants."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import random
import statistics
import sys
from time import perf_counter
from unittest.mock import patch


def run(server,rounds):
    sys.path[:0]=[str(server/x) for x in ("app","app/upstream","vendor/common",
                 "vendor/win64" if sys.platform=="win32" else "vendor/linux-x86_64")]
    from utils import db,pb_create,res_loader
    res_loader.init(server/"resources")
    ids=[r["i_d"] for r in res_loader.res["Item"]["item"]["datas"]]
    def measure(bulk):
        random.seed(1731)
        serial=itertools.count(1)
        digest=hashlib.sha256()
        def append(item):
            raw=item.SerializeToString(deterministic=True)
            digest.update(len(raw).to_bytes(4,"little")+raw)
        start=perf_counter()
        with patch.object(db,"get_instance_id",lambda player:next(serial)),patch.object(pb_create.time,"time",return_value=1700000000):
            if bulk:
                for item in pb_create.make_item(0,2,1000001,is_all=True):
                    append(item)
            else:
                for tid in [*ids,0,0xffffffff]:
                    item=pb_create.make_item(tid,2,1000001)
                    if isinstance(item,list):
                        assert not item
                        digest.update(b"missing")
                    else:
                        append(item)
        return digest.hexdigest(),perf_counter()-start
    reference,bulk_time=measure(True)
    checks=[measure(False) for _ in range(rounds)]
    assert len(set(d for d,_ in checks))==1
    print(json.dumps(dict(templates=len(ids),single_sha256=checks[0][0],bulk_sha256=reference,
                     single_median_s=round(statistics.median(t for _,t in checks),4),bulk_s=round(bulk_time,4))))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--server",type=Path,required=True)
    parser.add_argument("--rounds",type=int,default=3)
    args=parser.parse_args()
    run(args.server.resolve(),args.rounds)
