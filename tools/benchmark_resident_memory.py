"""Compare Windows process memory for clean isolated realms serving the same native saves.

The controller creates one disposable synthetic database and makes identical copies for
fresh A and B processes. Native clients and their buffers live in a separate process.
Uses Windows process counters directly, without an external monitoring library.
"""
import argparse
from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import platform
import shutil
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
import time


def use(server):
    sys.path[:0]=[str(server / leaf) for leaf in ("app","app/upstream","vendor/common","vendor/win64")]


class Counters(ctypes.Structure):
    _fields_=[("cb",wintypes.DWORD),("faults",wintypes.DWORD)] + [
        (k,ctypes.c_size_t) for k in ("peak_ws","ws","peak_paged","paged","peak_nonpaged","nonpaged",
                                    "pagefile","peak_pagefile","private")]


def process_memory():
    k=ctypes.WinDLL("kernel32",use_last_error=True)
    k.GetCurrentProcess.restype=wintypes.HANDLE
    fn=ctypes.WinDLL("psapi",use_last_error=True).GetProcessMemoryInfo
    fn.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    sample=Counters()
    sample.cb=ctypes.sizeof(sample)
    if not fn(k.GetCurrentProcess(),ctypes.byref(sample),sample.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return [sample.ws,sample.private]


def worker(server,state,clients):
    from local_identity import LocalIdentity
    from local_game_service import LocalGameService,db,pb
    from realm_rules import configure
    from utils.res_loader import res
    configure(state,{"game_connections":clients})
    identity=LocalIdentity(state/"identity.sqlite3")
    host=LocalGameService(identity,state,server/"resources",port=0)
    host.start()
    def write(data):
        print(json.dumps(data),flush=True)
    try:
        process_memory()
        write({"port":host.port})
        for line in sys.stdin:
            cmd,args=json.loads(line)
            if cmd=="stop":
                break
            if cmd=="sample":
                write(process_memory())
            elif cmd=="profiles":
                write([identity.create_profile(f"内存测量{i:02}") for i in range(clients)])
            elif cmd=="tickets":
                write([identity.issue_ticket(*p) for p in args])
            elif cmd=="gear":
                # Create ordinary structured gear with a role-local namespace. The same resulting
                # SQLite files are reused by both versions; no captured identity participates.
                item_rows=res["Item"]["item"]["datas"]
                types={pb.EBagItemTag_Weapon:"weapon",pb.EBagItemTag_Armor:"armor",pb.EBagItemTag_Poster:"poster"}
                choices={tag:[r["i_d"] for r in item_rows if r.get("new_bag_item_tag")==tag] for tag in types}
                data=[]
                for player in args:
                    for i in range(93):
                        tag=list(types)[i%3]
                        item_id=choices[tag][i//3%len(choices[tag])]
                        gear=pb.ItemDetail()
                        gear.main_item.item_id=item_id
                        gear.main_item.item_tag=tag
                        kind=types[tag]
                        record=getattr(gear.main_item,kind)
                        setattr(record,kind+"_id",item_id)
                        record.instance_id=90000+i
                        if kind!="poster":
                            record.level=12
                            record.random_property.add(value=120)
                        data.append((player,record.instance_id,gear.SerializeToString()))
                with host.lock,db.conn:
                    db.conn.executemany("INSERT INTO items_s VALUES(?,?,?)",data)
                write(len(data))
            elif cmd=="active":
                with host.lock:
                    write(len(host.active))
            else:
                raise ValueError("Unknown memory probe command")
    finally:
        host.stop()


@contextmanager
def running(server,state,clients):
    state.mkdir(exist_ok=True)
    cmd=[sys.executable,"-B","-X","utf8",str(Path(__file__).resolve()),"--worker","--server",
         str(server),"--state",str(state),"--clients",str(clients)]
    proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,encoding="utf8")
    def receive():
        result=proc.stdout.readline()
        if not result:
            raise RuntimeError(f"Isolated server exited: {proc.poll()}")
        return json.loads(result)
    def ask(cmd,args=None):
        proc.stdin.write(json.dumps((cmd,args))+"\n")
        proc.stdin.flush()
        return receive()
    try:
        yield receive()["port"],ask
    finally:
        if proc.poll() is None:
            proc.stdin.write('["stop",null]\n');proc.stdin.flush()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill();proc.wait()
        proc.stdin.close();proc.stdout.close()


def player_clients(stack,port,tickets,prepare):
    from local_game_wire import receive,encode
    from network.msg_id import MsgId
    from proto import net_pb2 as pb
    import socket
    peers=[]
    player_ids=[]
    signatures=[]
    item_counts=[]
    for index,ticket in enumerate(tickets):
        sock=stack.enter_context(socket.create_connection(("127.0.0.1",port),timeout=10))
        sock.settimeout(12)
        def rpc(op,seq,**payload):
            sock.sendall(encode(getattr(MsgId,op+"Req"),getattr(pb,op+"Req")(**payload).SerializeToString(),seq))
            for _ in range(400):
                head,body=receive(sock)
                if head.msg_id==getattr(MsgId,op+"Rsp") and head.packet_id==seq:
                    msg=getattr(pb,op+"Rsp").FromString(body)
                    assert msg.status==pb.StatusCode_OK
                    return msg
            raise AssertionError(op)
        pid=rpc("VerifyLoginToken",1,login_token=ticket).user_id
        assert rpc("PlayerLogin",2).player_name
        main=rpc("PlayerMainData",3)
        assert main.player_id==pid
        bag=[]; count=0
        for _ in range(400):
            head,body=receive(sock)
            if head.msg_id==MsgId.PackNotice:
                packet=pb.PackNotice.FromString(body)
                if packet.items:
                    bag.extend(detail.SerializeToString(deterministic=True) for detail in packet.items)
                    count+=len(packet.items)
            if head.msg_id==MsgId.SceneDataNotice:
                break
        else:
            raise AssertionError("Missing initial scene")
        if prepare:
            rpc("ChangeSceneChannel",4,scene_id=9999,channel_label=index//6+1)
            rpc("ChangeSign",5,sign=f"Resident{index}")
            rpc("ChangeHead",6,head=9005)
            rpc("SetArchiveInfo",7,key="memory_bench",value=f"seed{index}")
        else:
            assert main.scene_id==9999 and main.head==9005 and main.sign==f"Resident{index}"
            assert rpc("GetArchiveInfo",7,key="memory_bench").value==f"seed{index}"
        rpc("PlayerPing",8)
        signatures.append(hashlib.sha256(b"".join(sorted(bag))).hexdigest())
        item_counts.append(count);player_ids.append(pid)
        peers.append(sock)
    return peers,player_ids,item_counts,hashlib.sha256("".join(signatures).encode()).hexdigest()


def sample(ask):
    # External polling does not retain any message or sample history in the service process.
    observations=[]
    for _ in range(7):
        time.sleep(.15)
        observations.append(ask("sample"))
    return [int(median(column)) for column in zip(*observations)]


def source_fingerprint(server):
    d=hashlib.sha256()
    for f in sorted((server/"app").rglob("*.py")):
        d.update(f.relative_to(server).as_posix().encode()+b"\0"+f.read_bytes())
    return d.hexdigest()


def run(a,b,clients,pairs,out):
    if platform.system()!="Windows":
        raise SystemExit("This benchmark records Windows Working Set and Private Bytes.")
    use(a)
    order=["A","B","B","A"]*(pairs//2)+(["A","B"] if pairs%2 else [])
    versions={"A":a,"B":b};results=[]
    with TemporaryDirectory(prefix="of-resident-memory-") as t:
        d=Path(t);seed=d/"seed"
        # Prepare on the newer version so newly issued starter tools already exist in both runs.
        with running(b,seed,clients) as (port,ask):
            profiles=ask("profiles")
            with ExitStack() as stack:
                _,pids,_,_=player_clients(stack,port,ask("tickets",profiles),True)
                assert ask("gear",pids)==clients*93
        for version in order:
            with TemporaryDirectory(prefix=version+"-",dir=d) as dest:
                state=Path(dest)/"state"
                shutil.copytree(seed,state)
                with running(versions[version],state,clients) as (port,ask):
                    assert ask("active")==0
                    idle=sample(ask)
                    with ExitStack() as stack:
                        _,_,inventory,digest=player_clients(stack,port,ask("tickets",profiles),False)
                        assert ask("active")==clients and all(count>=1900 for count in inventory)
                        time.sleep(.6)
                        online=sample(ask)
                        assert ask("active")==clients
                    row=dict(version=version,idle_bytes=idle,online_bytes=online,
                             items=sum(inventory),bags_sha256=digest)
                    results.append(row)
                    print(version,sum(inventory),digest[:12],"startup/online MiB [working set,private]",
                          [[round(x/1048576,2) for x in v] for v in (idle,online)],flush=True)
    report=dict(python=sys.version.splitlines()[0],system=platform.platform(),process_bits=ctypes.sizeof(ctypes.c_void_p)*8,
                clients=clients,gear_instances_per_account=93,metric=["WorkingSetSize","PrivateUsage"],
                source_sha256={v:source_fingerprint(s) for v,s in versions.items()},samples=results)
    print(json.dumps(report,indent=2))
    if out:out.write_text(json.dumps(report,indent=2),encoding="utf8")
    assert len({row["items"] for row in results})==len({row["bags_sha256"] for row in results})==1


if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--a",type=Path);ap.add_argument("--b",type=Path)
    ap.add_argument("--pairs",type=int,default=3);ap.add_argument("--clients",type=int,default=24)
    ap.add_argument("--output",type=Path)
    ap.add_argument("--worker",action="store_true",help=argparse.SUPPRESS)
    ap.add_argument("--server",type=Path,help=argparse.SUPPRESS)
    ap.add_argument("--state",type=Path,help=argparse.SUPPRESS)
    args=ap.parse_args()
    if args.worker:
        use(args.server);worker(args.server,args.state,args.clients)
    else:
        if not (args.a and args.b and args.pairs>0 and args.clients>0):
            ap.error("--a and --b are required, pairs and clients must be positive")
        run(args.a.resolve(),args.b.resolve(),args.clients,args.pairs,args.output)
