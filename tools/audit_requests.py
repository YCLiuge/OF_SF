"""Compare local CN 1.1.12 metadata to an isolated OF_SF game server; no publisher connection."""
import argparse
import ast
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import runpy
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory


METADATA_SHA256 = "8b463bfeb8cf448f2db2b42b155139f3d5d15b6b2a3ae5c36497611fd70b0da2"
TYPE = struct.Struct("<16i8H2I")
SMALL_SUITES = ("test_game_realm", "test_observed_initialization", "test_native_gacha",
                "test_native_story_catalog", "test_native_story_dialogues")
OTHER_SUITES = ("test_game_archive","test_game_dungeon","test_dungeon_updates","test_private_channels",
                "test_home_chairs","test_plaza_actions","test_private_entitlements","test_private_gift_shop",
                "test_story_starter","test_story_entry","test_bottle_intro","test_hud_queries",
                "test_faye_dialog","test_season_catalog","test_explore_dispatch",
                "test_native_prayer_tasks","test_native_prayer_conversion")
HANDSHAKE = {"VerifyLoginTokenReq","PlayerLoginReq"}
MODES = {"AcceptQuestReq": "类型1接取目录主线，类型0按广场委托与已开放的活动对话接取。其他类型有独立参数限制。",
         "ClaimQuestRewardReq": "类型1按目录主线结算。类型9使用额外故事真实末步编号领取，24项结算有独立测试。",
         "PlayerSceneRecordReq": "实际移动上报更新场景或副本位置，测试可核对落库；目前处理器不发送同名响应。",
         "CreatePayOrderReq": "本地福利能发入背包；客户端交易响应会关闭真实支付。"}
REGRESSION = "isolated_route_probe"


def unpack_signed(storage, i):
    first = storage[i]
    if first < 0x80:
        value = first
    elif first < 0xc0:
        value = ((first & 0x3f) << 8) | storage[i+1]
    elif first < 0xe0:
        value = int.from_bytes(storage[i:i+4],"big") & 0x1fffffff
    elif first == 0xf0:
        value = int.from_bytes(storage[i+1:i+5],"little")
    elif first in (0xfe,0xff):
        value = 0xffffff00 | first
    else:
        raise ValueError("Invalid encoded IL2CPP enum constant")
    return -(value // 2) - 1 if value & 1 else value // 2


def native_catalog(client):
    path = client if client.is_file() else client/"launcher_Data/il2cpp_data/Metadata/global-metadata.dat"
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest() != METADATA_SHA256:
        raise ValueError("This audit requires the supported CN PC 1.1.12 global-metadata.dat")
    if struct.unpack_from("<II",data)!=(0xfab11baf,31):
        raise ValueError("Unexpected metadata header")
    offsets=list(struct.iter_unpack("<II",data[8:256]))
    def table(i):
        start,size=offsets[i]
        return memoryview(data)[start:start+size]
    strings=bytes(table(2))
    def name(i):
        return strings[i:strings.index(0,i)].decode("utf-8")
    field_defaults={f:i for f,_,i in struct.iter_unpack("<iii",table(7)) if i>=0}
    default_data=table(8)
    fields=table(11)
    def constants(t):
        for n in range(t[8],t[8]+t[18]):
            if n in field_defaults:
                yield name(struct.unpack_from("<i",fields,n*12)[0]),unpack_signed(default_data,field_defaults[n])
    ids={}
    classes={}
    for t in TYPE.iter_unpack(table(19)):
        type_name,namespace=name(t[0]),name(t[1])
        if (namespace,type_name)==("Inutan","NetworkEventIDNew"):
            ids=dict(constants(t))
        if namespace=="Pb" and type_name.endswith(("Req","Rsp")):
            classes[type_name]={key[:-len("FieldNumber")]:value for key,value in constants(t) if key.endswith("FieldNumber")}
    requests={n for n in ids if n.endswith("Req")}
    if len(requests)!=303 or requests!={n for n in classes if n.endswith("Req")}:
        raise ValueError("Retail request enum and message classes disagree")
    return ids,classes


def bootstrap(server):
    for name in tuple(os.environ):
        if name.startswith(("OF_REMOTE_","OF_QUICKSDK_")) or name in ("OF_ACCOUNT_NATIVE","OF_PRIVATE_CLIENT_EXE"):
            os.environ.pop(name)
    platform="win64" if os.name=="nt" else "linux-x86_64"
    sys.path[:0]=[str(server/p) for p in ("app","app/upstream","tests","vendor/common","vendor/"+platform)]


def wire_schema(native, descriptor):
    native_by_tag={v:k.lower().replace("_","") for k,v in native.items()}
    hosted_by_tag={} if descriptor is None else {f.number:f.name.lower().replace("_","") for f in descriptor.fields}
    return dict(client_fields=native,server_fields=None if descriptor is None else {f.name:f.number for f in descriptor.fields},
                top_level_tags_match=descriptor is not None and native_by_tag==hosted_by_tag)


def fixed_reply(handler):
    # Conservative structural check. Delegated helpers are not assumed to implement their entire public request.
    try:
        node=ast.parse(inspect.getsource(inspect.getmodule(handler).__dict__.get("Handler",handler.__class__)))
    except (TypeError, OSError, IndentationError):
        return False
    method=next((n for n in ast.walk(node) if isinstance(n,ast.FunctionDef) and n.name=="handle"),None)
    if method is None or any(isinstance(n,(ast.If,ast.IfExp,ast.For,ast.While,ast.Try)) for n in ast.walk(method)):
        return False
    calls={ast.unparse(n.func) for n in ast.walk(method) if isinstance(n,ast.Call)}
    if "session.send" not in calls:
        return False
    if any(not (c.endswith(("Req","Rsp",".FromString",".ParseFromString")) or c=="session.send") for c in calls):
        return False
    # The five fixed responses in the original server assign only constants, request echoes, or TEST_DATA constants.
    for line in method.body:
        if isinstance(line,ast.Assign) and any(isinstance(x,ast.Attribute) for x in line.targets):
            if isinstance(line.value,ast.Constant):
                continue
            if isinstance(line.value,ast.Attribute) and isinstance(line.value.value,ast.Name):
                if line.value.value.id in ("req","request","pb","StatusCode"):
                    continue
            if isinstance(line.value,ast.Subscript) and isinstance(line.value.value,ast.Name) and line.value.value.id=="TEST_DATA":
                continue
            return False
    return True


def implementations(server,ids,classes):
    from local_game_service import LocalGameService, MsgId, pb
    from local_identity import LocalIdentity
    from unsupported_requests import OWNER_DISABLED, unsupported_status
    result={}
    with TemporaryDirectory(prefix="ofs-request-routing-") as temp:
        state=Path(temp)
        service=LocalGameService(LocalIdentity(state/"identity.sqlite3"),state,server/"resources",port=0)
        with redirect_stdout(io.StringIO()):
            service.start()
        try:
            for name,mid in sorted(ids.items(),key=lambda x:x[1]):
                if not name.endswith("Req"):continue
                pair=name[:-3]+"Rsp"
                server_id=getattr(MsgId,name,None)
                handler=service.handlers.get(mid)
                if name in HANDSHAKE:
                    route="login_handshake"
                elif handler:
                    route="registered_fixed_reply" if fixed_reply(handler) else "registered"
                elif mid in service.failure_replies:
                    route="explicit_unsupported"
                else:
                    route="no_route"
                request=getattr(pb,name,None)
                response=getattr(pb,pair,None)
                req_desc=request.DESCRIPTOR if request else None
                rsp_desc=response.DESCRIPTOR if response else None
                source=Path(inspect.getfile(type(handler))).name if handler else (
                    "local_game_service._Connection.handle" if route=="login_handshake"
                    else "local_game_service._handle" if route=="explicit_unsupported" else None)
                closures=[]
                if handler and handler.handle.__closure__:
                    for cell in handler.handle.__closure__:
                        value=cell.cell_contents
                        if inspect.isfunction(value):
                            closures.append(value.__module__+"."+value.__name__)
                selectors={}
                if req_desc:
                    for f in req_desc.fields:
                        if f.enum_type:
                            selectors[f.name]={v.name:v.number for v in f.enum_type.values}
                item=dict(request=name,native_id=mid,server_id=server_id,
                          request_shape=wire_schema(classes[name],req_desc),response=pair,native_response_id=ids[pair],
                          server_response_id=getattr(MsgId,pair,None),response_shape=wire_schema(classes[pair],rsp_desc),
                          runtime_route=route,dispatch_source=source,delegates=closures,enum_fields=selectors)
                if route=="explicit_unsupported":
                    item["fallback_status"]=pb.StatusCode.Name(unsupported_status(mid))
                    item["owner_disabled"]=mid in OWNER_DISABLED
                if name in MODES:item["known_scope"]=MODES[name]
                result[name]=item
        finally:
            service.stop()
    return result


def source_mentions(server,known):
    found=defaultdict(set)
    for path in (server/"tests").glob("test_*.py"):
        syntax=ast.parse(path.read_text("utf-8-sig"))
        for node in ast.walk(syntax):
            name=None
            if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id in ("MsgId","pb"):
                name=node.attr
            elif isinstance(node,ast.Call):
                for arg in node.args:
                    if isinstance(arg,ast.Constant) and isinstance(arg.value,str) and arg.value+"Req" in known:
                        found[arg.value+"Req"].add(path.stem)
            if name in known:found[name].add(path.stem)
    return found


def inventory_probe(game,player,sock,main,scene):
    from local_game_service import pb
    from test_native_gacha import call
    char=main.characters[0]
    for index,(name,items) in enumerate((("GetArmor","armors"),("GetPoster","posters"),("GetPet","pets")),80):
        page=call(sock,name,index,start_index=0,end_index=10)
        assert page.status==pb.StatusCode_OK and page.total_num>=len(getattr(page,items))
    assert call(sock,"GetAllCharacterEquip",83).status==pb.StatusCode_OK
    equip=call(sock,"CharacterEquipUpdate",84,char_id=char.character_id,equipment_preset=char.equipment_presets[0])
    assert equip.status==pb.StatusCode_OK and equip.character[0].character_id==char.character_id
    assert call(sock,"CharacterGatherWeaponUpdate",85,character_id=char.character_id,weapon_id=char.gather_weapon).status==pb.StatusCode_OK
    assert call(sock,"CharacterStarUp",86,char_id=999999).status==pb.StatusCode_CharNotExist
    from utils import db
    from utils.pb_create import make_item
    from utils.res_loader import res
    from private_shop import balance
    star_config=next(row for row in res["Character"]["character_star"]["datas"] if row["i_d"]==char.character_id)
    level=next(row for row in star_config["star_info"] if row["star"]==char.star+1)
    shard,cost=level["item_i_d"],level["item_num"]
    # Supply this isolated test character with the configured fragments, then use the real growth request.
    with game.lock,db.conn:
        db.set_item_detail(player,make_item(shard,cost+1,player).SerializeToString(),shard)
    upgrade=call(sock,"CharacterStarUp",88,char_id=char.character_id)
    assert upgrade.status==pb.StatusCode_OK and upgrade.star==char.star+1
    with game.lock:
        assert balance(player,shard).main_item.base_item.num==1
        assert pb.Character.FromString(db.get_characters(player,char.character_id)[0]).star==upgrade.star
    from private_field_gathering import points
    plant_group = 100003
    index = next(i for i, group in points()[("Scenes", scene.scene_id)].items() if group == plant_group)
    plant = next(row for row in res["Gather"]["gather"]["datas"] if row["i_d"] == plant_group)
    lead = next(actor for actor in main.characters if actor.character_id == main.team.char1)
    equipped = call(sock, "CharacterGatherWeaponUpdate", 89, character_id=lead.character_id,
                    weapon_id=lead.character_appearance.collection_gloves_instance_id)
    assert equipped.status == pb.StatusCode_OK
    gather = call(sock, "Gather", 90, gather_item={"index": index, "reward": plant["gather_group_info"][0]["reward"]})
    assert gather.status == pb.StatusCode_OK and gather.items


def probe(resources):
    """Check real game-state change, typed rejection, and a supported read after an unsupported request."""
    from local_game_service import LocalGameService,MsgId,pb
    from local_identity import LocalIdentity
    from test_game_realm import join_after_intro,login
    from test_native_gacha import call
    with TemporaryDirectory(prefix="ofs-protocol-probe-") as temp:
        state=Path(temp);identity=LocalIdentity(state/"identity.sqlite3")
        profile=identity.create_profile("协议审计")
        game=LocalGameService(identity,state,resources,port=0)
        game.start()
        try:
            sock,_,player=login(game.port,identity.issue_ticket(*profile))
            with sock:
                main,scene=join_after_intro(sock)
                inventory_probe(game,player,sock,main,scene.data)
                script=call(sock,"AcceptQuest",73,quest_id=1000001,quest_type=pb.EQuestType_MainQuest)
                assert script.status==pb.StatusCode_OK and script.quest.quest_id==100011
                assert call(sock,"AcceptQuest",74,quest_id=1000001,quest_type=pb.EQuestType_ExtraQuest).status==pb.StatusCode_BAD_REQ
                # Dispatch only requests that the running service explicitly routes to its typed fallback.
                # A matching response and successful subsequent ping establish the wire behavior of each denial.
                from unsupported_requests import unsupported_status
                fallback = sorted((number,name) for name,number in vars(MsgId).items() if name.endswith("Req")
                                  and number in game.failure_replies and number not in game.handlers)
                for packet,(number,name) in enumerate(fallback,100):
                    assert call(sock,name[:-3],packet).status==unsupported_status(number),name
                assert call(sock,"PlayerPing",500,client_time_ms=8765).client_time_ms==8765
        finally:
            game.stop()


def observed_suite(server,suite,output):
    """Record only names, result codes and enum selectors on the test's loopback sockets."""
    bootstrap(server)
    import local_game_wire as wire
    from network.msg_id import MsgId
    from proto import net_pb2 as pb
    original=wire.receive
    names={v:n for n,v in vars(MsgId).items() if isinstance(v,int) and n.endswith(("Req","Rsp"))}
    counts=defaultdict(Counter)
    selectors=defaultdict(lambda:defaultdict(set))
    by_mode=defaultdict(lambda:defaultdict(Counter))
    pending=defaultdict(deque)
    from google.protobuf.message import DecodeError
    def receive(sock):
        head,raw=original(sock)
        n=names.get(head.msg_id)
        if n and sock.family in (2,10):
            try:
                remote,local=sock.getpeername(),sock.getsockname()
            except OSError:
                return head,raw
            if remote[0] in ("127.0.0.1","::1","::ffff:127.0.0.1"):
                if n.endswith("Req"):
                    counts[n]["requests"]+=1
                    kind=getattr(pb,n,None)
                    modes=[]
                    if kind and any(f.enum_type and not f.is_repeated for f in kind.DESCRIPTOR.fields):
                        try:
                            msg=kind.FromString(raw)
                        except DecodeError:
                            return head,raw
                        for f in kind.DESCRIPTOR.fields:
                            if f.enum_type and not f.is_repeated:
                                key=getattr(msg,f.name)
                                label=f.enum_type.values_by_number.get(key)
                                value=label.name if label else "unknown_enum"
                                selectors[n][f.name].add(value)
                                modes.append(f.name+"="+value)
                    if modes:
                        pending[(remote,local,n[:-3],head.packet_id)].append(modes)
                else:
                    kind=getattr(pb,n,None)
                    if kind and "status" in kind.DESCRIPTOR.fields_by_name:
                        desc=kind.DESCRIPTOR.fields_by_name["status"].enum_type
                        if desc:
                            try:
                                status=kind.FromString(raw).status
                            except DecodeError:
                                return head,raw
                            entry=desc.values_by_number.get(status)
                            label=entry.name if entry else "unknown_status"
                            req_name=n[:-3]+"Req"
                            counts[req_name][label]+=1
                            key=(local,remote,n[:-3],head.packet_id)
                            if key in pending:
                                for mode in pending[key].popleft():
                                    by_mode[req_name][mode][label]+=1
                                if not pending[key]:
                                    del pending[key]
        return head,raw
    wire.receive=receive
    with redirect_stdout(io.StringIO()):
        if suite==REGRESSION:
            probe(server/"resources")
        else:
            script=server/"tests"/(suite+".py")
            sys.argv=[str(script),"--resources",str(server/"resources")]
            runpy.run_path(str(script),run_name="__main__")
    save={name:dict(counts=stats,enum_inputs={f:sorted(v) for f,v in selectors[name].items()},
                    correlated_enum_responses=by_mode[name]) for name,stats in counts.items()}
    output.write_text(json.dumps(save),encoding="utf-8")


def verify(server,full):
    suites=list(SMALL_SUITES)
    if full:
        gameplay = {p.stem for p in (server/"tests").glob("test_native_*.py")
                    if not any(tag in p.stem for tag in ("audit_requests", "request_audit", "request_coverage"))}
        suites=sorted(set(suites)|gameplay|set(OTHER_SUITES))
    suites.append(REGRESSION)
    report={}
    with TemporaryDirectory(prefix="ofs-request-tests-") as tmp:
        def worker(suite):
            path=Path(tmp)/(suite+".json")
            args=[sys.executable,"-B","-X","utf8",str(Path(__file__).resolve()),"--server",str(server),
                  "--worker",suite,"--output",str(path)]
            job=subprocess.run(args,capture_output=True,timeout=360)
            if job.returncode:
                # Existing tests may expose local particulars in failure output. Keep the audit report aggregate-only.
                raise RuntimeError(f"Isolated test {suite} failed (exit {job.returncode}); rerun that test separately")
            return suite,json.loads(path.read_bytes())
        with ThreadPoolExecutor(max_workers=3) as pool:
            for suite,exercised in pool.map(worker,suites):
                report[suite]=exercised
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client",type=Path,help="Local CN PC 1.1.12 installation directory or its global-metadata.dat")
    parser.add_argument("--server",type=Path,default=Path("OverField-Server"))
    parser.add_argument("--full",action="store_true",help="Include a row for each native request")
    parser.add_argument("--verify",action="store_true",help="Run isolated regression and a representative set of native suites")
    parser.add_argument("--verify-all",action="store_true",help="Also include the native gameplay suites in this checkout")
    parser.add_argument("--output",type=Path)
    parser.add_argument("--worker",help=argparse.SUPPRESS)
    args=parser.parse_args()
    server=args.server.resolve()
    if args.worker:
        if not args.output or not (args.worker==REGRESSION or (args.worker.startswith("test_") and args.worker.isidentifier()
                                   and (server/"tests"/(args.worker+".py")).is_file())):parser.error("Invalid isolated suite")
        observed_suite(server,args.worker,args.output)
        return
    if not args.client:parser.error("Supply --client")
    ids,classes=native_catalog(args.client)
    bootstrap(server)
    rows=implementations(server,ids,classes)
    mentioned=source_mentions(server,rows)
    observed=verify(server,args.verify_all) if args.verify or args.verify_all else {}
    for name,item in rows.items():
        item["test_source_mentions"]=sorted(mentioned[name])
        item["observed_loopback"]={suite:seen[name] for suite,seen in observed.items() if name in seen}
    def passes(item):
        return any(c["counts"].get("StatusCode_OK",0) for c in item["observed_loopback"].values())
    counts=dict(native_requests=len(rows),native_responses=sum(n.endswith("Rsp") for n in ids),
                native_notices=sum(n.endswith("Notice") for n in ids),
                request_and_reply_ids_matching=sum(r["server_id"]==r["native_id"] and r["server_response_id"]==r["native_response_id"] for r in rows.values()),
                request_top_level_tags_matching=sum(r["request_shape"]["top_level_tags_match"] for r in rows.values()),
                response_top_level_tags_matching=sum(r["response_shape"]["top_level_tags_match"] for r in rows.values()),
                routes=dict(Counter(r["runtime_route"] for r in rows.values())),
                fallback_statuses=dict(Counter(r["fallback_status"] for r in rows.values() if "fallback_status" in r)),
                test_source_mentioned=len([r for r in rows.values() if r["test_source_mentions"]]),
                native_request_observed=sum(any(c["counts"].get("requests",0) for c in r["observed_loopback"].values()) for r in rows.values()),
                ok_response_observed=sum(passes(r) for r in rows.values()))
    result=dict(metadata_sha256=METADATA_SHA256,counts=counts,passed_suites=list(observed),
                limits=["Fields compare top-level names and tags; wire types, nested semantics and notices are not certified.",
                        "A registered route or a successful example does not prove every operation, side effect, or client UI.",
                        "Source mentions are leads only; loopback observations are counted only for suites that passed."])
    if args.full:result["requests"]=list(rows.values())
    rendered=json.dumps(result,ensure_ascii=False,indent=2)+"\n"
    if args.output:args.output.write_text(rendered,encoding="utf-8")
    else:print(rendered,end="")


if __name__=="__main__":
    main()
