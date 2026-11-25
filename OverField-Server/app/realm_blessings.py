"""Native blessing tree editions and their independently configurable realm windows (UTC+8)."""
from functools import lru_cache
import json

from config_revision import check
from local_identity import LocalIdentity
from realm_events import DATA, SCHEMA, _timestamp
from state_lock import exclusive_state

# Names transcribed from the national UI_BlessingTree icon table and its Chinese title art.
TITLES = {
    101:"星辉树",102:"星幻树",103:"云端树",104:"海蓝树",105:"绮梦树",106:"秘语树",107:"星辉树",
    108:"蜜月树",109:"新学树",110:"甜食树",111:"华装树",112:"暖风树",113:"灵思树",
    114:"童趣树",115:"红冬树",116:"棒球树",117:"宴席树",118:"雅韵树",119:"净心树",120:"春日甜食",121:"樱络树",
    10001:"星辉树",10002:"限时资源树",10003:"庆典星愿",10004:"万圣树",10005:"火锅店",
    10006:"共创树Ⅰ",10007:"共创树Ⅱ",10008:"共创树Ⅰ",10009:"共创树Ⅳ",10010:"共创树",
    20001:"乐器行",20002:"兜财树",20003:"乐器行",30001:"樱花树",30002:"火锅",
    40000:"圣诞树",40001:"节日屋",40010:"年夜饭",40011:"万家灯火",40012:"元宵",40013:"踏云树",50010:"资源树",
}


@lru_cache(maxsize=1)
def catalog():
    source = json.loads((DATA/"BlessingTree.json").read_bytes())
    nodes = {row["i_d"]:row["blessing_tree_info_group_info"] for row in source["blessing_tree_info"]["datas"]}
    result = {}
    for edition in source["blessing_tree"]["datas"]:
        layout = edition["blessing_tree_info_i_d"]
        entries = nodes[layout]
        result[edition["i_d"]] = {"id":edition["i_d"], "name":TITLES[edition["icon"]], "layout":layout,
            "opens_at":edition["opening_time"], "closes_at":edition["closing_time"], "nodes":len(entries),
            "currency_ids":sorted({r["item_i_d"] for r in entries}), "shop_id":edition.get("shop_i_d",0)}
    return result


def validate(trees):
    if not isinstance(trees,list) or len(trees)>len(catalog()):
        raise ValueError("Invalid blessing tree list")
    seen=set()
    for row in trees:
        if not isinstance(row,dict) or row.keys()!={"id","enabled","opens_at","closes_at"}:
            raise ValueError("Each blessing tree needs id, enabled, opens_at and closes_at")
        tree=row["id"]
        if type(tree)!=int or tree not in catalog() or tree in seen or type(row["enabled"])!=bool:
            raise ValueError("Unknown or duplicate native blessing tree or invalid enabled flag")
        seen.add(tree)
        start,end=row["opens_at"],row["closes_at"]
        _timestamp(start)
        _timestamp(end)
        stock=catalog()[tree]
        if (start or stock["opens_at"])>(end or stock["closes_at"]):
            raise ValueError("Empty blessing tree window")
    return sorted(trees,key=lambda row:row["id"])


def manage(state, name, data, *, expected=None):
    if name=="blessings.catalog":
        return {"trees":list(catalog().values())}
    if name=="blessings.get" and not (state/"identity.sqlite3").is_file():
        return {"trees":[]}
    with exclusive_state(state,purpose="profiles") as root:
        root.mkdir(parents=True,exist_ok=True)
        with LocalIdentity(root/"identity.sqlite3")._write() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS private_event_config "+SCHEMA)
            stored=conn.execute("SELECT settings FROM private_event_config WHERE id=1").fetchone()
            config=json.loads(stored[0]) if stored else {"events":[]}
            if name=="blessings.set":
                check({"trees":config.get("blessings",[])}, expected)
                trees=validate(data["trees"])
                if trees:
                    config["blessings"]=trees
                else:
                    config.pop("blessings",None)
                conn.execute("INSERT OR REPLACE INTO private_event_config VALUES(1,?)",
                             (json.dumps(config,separators=(",",":")),))
            return {"trees":config.get("blessings",[])}


def client_plan(settings):
    plaza=settings.get("plaza",{})
    trees=settings.get("blessings")
    return {**plaza,"blessings":trees} if trees else plaza


def validate_plan(plan, *, active_only=False):
    import realm_plaza
    if not isinstance(plan,dict):
        raise ValueError("Invalid realm calendar")
    plaza=realm_plaza.validate({k:v for k,v in plan.items() if k!="blessings"})
    if active_only and not realm_plaza.active(plaza):
        plaza={}
    trees=validate(plan.get("blessings",[]))
    return {**plaza,"blessings":trees} if trees else plaza


def dates(override):
    stock=catalog()[override["id"]]
    return override["opens_at"] or stock["opens_at"], override["closes_at"] or stock["closes_at"]
