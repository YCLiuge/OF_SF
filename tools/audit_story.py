"""Audit OF_SF against locally hydrated CN 1.1.12 tables. Emits IDs and coverage, never dialogue text."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


CHAPTER_NAMES = {
    10000: "序章", 10001: "第一章 “眼”", 10002: "第二章 尘封秘话",
    10003: "第三章 拾光", 10004: "第四章 兔子与饭团", 10005: "第五章 美食节",
    10006: "第六章 最棒的礼物", 10007: "第七章 魔女的茶会", 10008: "第八章 罗塔庆典",
}
EXTRA_NAMES = {1001: "间章", 2001: "后日谈", 3001: "探索员", 4001: "闲谈"}
QUEST_TYPES = {0: "未标类型", 1: "主线", 2: "支线", 3: "世界", 4: "日常", 5: "循环",
               6: "秘境", 7: "随机事件", 8: "委托", 9: "额外剧情"}
CONDITIONS = {4: "完成任务", 6: "秘境通关", 37: "任务对话", 38: "招募", 39: "进入场景", 40: "机关交互",
              43: "区域解锁数量", 44: "星云树能力解锁", 45: "完成遭遇", 113: "场景完成度（万分制）", 168: "制作料理次数"}
TESTS = ("test_bottle_intro.py", "test_story_starter.py", "test_native_first_quest.py", "test_story_entry.py",
         "test_native_badge_tree.py", "test_native_story_catalog.py", "test_native_story_dialogues.py",
         "test_native_story_scene.py", "test_native_story_map_progress.py", "test_native_story_objectives.py",
         "test_native_panel_quests.py", "test_native_extra_story_rewards.py", "test_native_field_flags.py")

# Each entry names the assertions that justify it. Rendering and dialogue timing need a client run.
# Keys refer to the original concrete Quest, not a story number or a whole handler category.
STEP_EVIDENCE = {
    100011: ("test_native_first_quest.py", "原生接取", "原生完成", "原生完成", "无本步资源奖励", "重登验证"),
    100012: ("test_native_first_quest.py", "前步自动接续", "原生完成", "原生完成", "核对两项入包、重复对话", "重登验证"),
    100013: ("test_native_first_quest.py", "前步自动接续", "原生完成", "原生完成", "教学抽及故事奖防重", "重启验证"),
    100021: ("test_story_entry.py", "原生接取及前置拒绝", "原生完成", "原生完成", "无本步资源奖励", "重启验证"),
    100022: ("test_story_entry.py", "前步自动接续", "原生完成", "原生完成", "有发奖实现，入包数未单独断言", "重启验证"),
    100023: ("test_story_entry.py", "前步自动接续", "区域装置实际激活", "原生完成", "装置实际发奖且防重", "重启验证"),
    100024: ("test_story_entry.py", "前步自动接续", "地标机关位置核验", "原生完成", "无本步资源奖励", "重启验证"),
    100025: ("test_story_entry.py", "前步自动接续", "闪光点机关位置核验", "原生完成", "无本步资源奖励", "重启验证"),
    100026: ("test_story_entry.py", "前步自动接续", "真实切回广场场景", "原生完成", "原生故事奖励入包、防重", "重启验证"),
    100031: ("test_story_entry.py", "依序从前节接取", "靠近芳汀并对话", "原生完成", "起始材料防重另有独立测试", "重启验证"),
    100032: ("test_story_entry.py", "前步自动接续", "消耗徽章学习二段跳", "原生完成", "整节领奖已核对", "重启验证"),
    100033: ("test_story_entry.py", "前步自动接续", "进入樱庭世界", "原生完成", "整节领奖已核对", "重启验证"),
    100034: ("test_story_entry.py", "前步自动接续", "抵达教学机关的水平和高度范围", "原生完成", "整节领奖已核对", "重启验证"),
    100035: ("test_story_entry.py", "前步自动接续", "缓降专属机关交互", "原生完成", "领取故事奖励、防重", "重启验证"),
    100086: ("test_native_story_objectives.py", "测试预置本步在途", "实际制作一道料理", "达标后接续100087", "本步未独立断言", "重启验证"),
    100093: ("test_native_story_objectives.py", "测试预置本步在途", "以不同樱庭标记越过10%门槛", "达标后接续100094", "本步未独立断言", "重启验证"),
}
for _ids in ((106061,106062),(106081,),(107051,107052,107053)):
    for _index, _id in enumerate(_ids):
        STEP_EVIDENCE[_id] = ("test_native_story_dialogues.py", "原生接取（预置前章条件）" if _index == 0 else "前步自动接续",
                              "原生 NPC 对话及原场景交互位置", "原生完成", "无本步奖励，整节领取已验", "重启验证")
# Same new role throughout these six stories; every saved step is re-read after restart.
_PROLOGUE = {
    100041:"樱庭教学区域",100042:"采集点触发范围",100043:"回溯操作",100044:"采集点交互",100045:"采集结尾",
    100051:"芳汀",100052:"进入樱庭",100053:"先过野外战斗110210，再交互机关",100054:"地标",100055:"芳汀",
    100061:"荧荧",100062:"进入樱庭",100063:"研究机关",100064:"野外战斗110300",100065:"返回广场",100066:"荧荧",
    100071:"芳汀",100072:"进入樱庭",100073:"三个不同的水阀",100074:"首开水车景点1002",100075:"到达水车顶",
    100081:"荧荧",100082:"梓",100083:"采集火花",100084:"绫",100085:"餐厅NPC",100086:"购买食材后烹饪成功",
    100087:"餐厅NPC",100088:"芳汀",100089:"篝火机关",100090:"篝火过场区域",100098:"焰火收尾",
    100091:"芳汀",100092:"进入樱庭",100093:"收集不同樱庭水晶累计46/456",100094:"返回广场与芳汀交谈",
}
_REWARDS = {100051:"三项解锁物品入包",100063:"十份饮料入包",100064:"一件独立装备入包",
            100074:"景点初次四张明信片入包，重复开不重发",100085:"五份料理入包",
            100098:"焰火解锁道具入包",100094:"探索解锁道具入包"}
for _id, _action in _PROLOGUE.items():
    STEP_EVIDENCE[_id] = ("test_story_entry.py", "依序接取或自动接续", _action, "原生完成",
                          _REWARDS.get(_id,"无本步资源奖励"), "重启验证")
COMPLETE = {1000001: "test_native_first_quest.py", 1000002: "test_story_entry.py", **{sid: "test_story_entry.py" for sid in range(1000003,1000010)},
            1000606: "test_native_story_dialogues.py", 1000608: "test_native_story_dialogues.py",
            1000705: "test_native_story_dialogues.py"}
OBJECTIVE_NAMES = {11000321: "星云树解锁二段跳", 11000861: "制作任意一道料理",
                   11000931: "樱庭世界完成度达到 1000/10000（10%）"}
TUTORIAL_EVENTS = {"count": 53, "prologue_count": 24, "star_count": 29,
                   "prologue_source": "StatisticSystem.InitP2TutorialKeyToName",
                   "star_source": "StatisticSystem.TrackCustomEventP2TutorialStar",
                   "star_ids": [*range(100,127),144,145],
                   "ranges": {"1-10": "瓶中空间、风车与字幕", "11-16": "菲、梓及招募申请对话",
                              "17-20": "招募教学与诗的对话", "21-24": "剧情菜单、领取奖励与接取任务"},
                   "evidence": "test_bottle_intro.py"}


def table(data, name, key):
    return data[name][key]["datas"]


def checked(path, manifest):
    content = path.read_bytes()
    expected = manifest.get("files", {}).get("resources/data/" + path.name, {}).get("sha256")
    if expected and hashlib.sha256(content).hexdigest() != expected:
        raise ValueError(f"Resource does not match local 1.1.12 manifest: {path.name}")
    return json.loads(content)


def verify(server):
    env = os.environ.copy()
    roots = [server / "app", server / "app/upstream", server / "tests",server / "vendor/common"]
    roots.append(server / ("vendor/win64" if os.name == "nt" else "vendor/linux-x86_64"))
    # The embedded Windows interpreter ignores PYTHONPATH. Set paths inside the child in both cases.
    command = "import runpy,sys; paths=sys.argv[1:6]; test=sys.argv[6]; sys.path[:0]=paths; sys.argv=[test]+sys.argv[7:]; runpy.run_path(test,run_name='__main__')"
    for name in TESTS:
        test = server / "tests" / name
        args=[sys.executable,"-B","-X","utf8","-c",command,*map(str,roots),str(test),"--resources",str(server/"resources")]
        result=subprocess.run(args,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
        if result.returncode:
            sys.stderr.buffer.write(result.stdout)
            raise RuntimeError(f"Failed isolated native test: {name}")


def build(server, client):
    path = server / "resources/data"
    manifest = json.loads((server/"resource-sources.json").read_bytes())
    data = {n:checked(path/(n+".json"), manifest) for n in ("Story","Quest","Achieve")}
    if client:
        # Parser exports may use indentation or escape Unicode differently. Verify content, not formatting.
        additional = {
            "Dialog": "ef68600469f8b5feefaae13257542b210de0bf2d5d09e6f63e96a2747c4754d4",
            "NPC": "f000f7f37dba5f51e4d760557b8b095c197c659d533b0e81049c421697e6828b",
            "RandomEvent": "6613eec8f847149eed8600f4ee4340687a1c878a225f22a99a37134bbeb83d3f",
        }
        data["Activity"] = checked(path/"Activity.json",manifest)
        for name in ("Story","Quest","Achieve","Activity"):
            if json.loads((client/(name+".json")).read_bytes()) != data[name]:
                raise ValueError(f"The local client's {name} data differ from this server build")
        for name, expected in additional.items():
            data[name] = json.loads((client/(name+".json")).read_bytes())
            canonical = json.dumps(data[name],ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")
            if hashlib.sha256(canonical).hexdigest() != expected:
                raise ValueError(f"The local client's {name} table differs from CN 1.1.12")
    chapters=table(data,"Story","story_chapter")
    main=table(data,"Story","story")
    extra=table(data,"Story","extra_chapter")
    reviews=table(data,"Story","story_review")
    review_dialogs={r["i_d"]:[d["dialog_i_d"] for d in r["story_review_set"]] for r in reviews}
    listed={sid:ch["i_d"] for ch in chapters for sid in ch["story_list"]}
    groups={r["quest_i_d"]:r for r in main}
    extras={r["quest_i_d"]:ch["i_d"] for ch in extra for r in ch["extra_quest"]}
    steps=table(data,"Quest","quest")
    groups_by_id={r["i_d"]:r["quest_condition_set"] for r in table(data,"Quest","condition_set_group")}
    resources={r["i_d"]:r.get("quest_resource_items",[]) for r in table(data,"Quest","quest_resource")}
    achieves={r["i_d"]:r for r in table(data,"Achieve","achieve") if "i_d" in r}
    npc=data.get("NPC") or checked(path/"NPC.json",manifest)
    pools={r["i_d"]:r for r in npc["delegate_quest_pool"]["datas"]}
    commissions={row["quest_i_d"]:actor["i_d"] for actor in npc["info"]["datas"] for func in actor.get("funcs",())
                 if func.get("new_n_p_c_function")==13 for row in pools[func["sub_meter_i_d"]]["delegate_quest_pool_group_info"]}
    panel_test="test_native_panel_quests.py"
    sample_commissions={401001,401002,401103,401201,401301,401401,401501,401601}
    extra_tails={row["quest_group"]:row["i_d"] for row in steps if row.get("new_type")==9 and row["quest_group"] in extras}
    rows=[]
    for q in steps:
        qid, group, qt = q["i_d"], q["quest_group"], q.get("new_type",0)
        story = groups.get(group, {})
        sid = story.get("i_d")
        book = listed.get(sid)
        extra_id = extras.get(group)
        phases=[]
        for group_row in groups_by_id[q["condition_set_group_i_d"]]:
            conditions=[]
            for key in group_row.get("achieve_condition_i_d",[]):
                spec=achieves.get(key, {})
                category = spec.get("new_achieve_condition")
                conditions.append(dict(id=key, type=category, category=CONDITIONS.get(category),
                                       objective=OBJECTIVE_NAMES.get(key), target=spec.get("count_param"),
                                       parameters=spec.get("param",[])))
            relevant = group_row.get("resource_i_d",[])
            scenes = sorted({entry["scene_i_d"] for key in relevant for entry in resources.get(key,[]) if "scene_i_d" in entry})
            phases.append(dict(conditions=conditions,resource_ids=relevant,scene_ids=scenes))
        baseline = "未验证"
        evidence,accept,interaction,settle,reward,reload = STEP_EVIDENCE.get(qid,(
            None, "目录通用接取，未逐节实走" if book is not None and qid == group else baseline,
            baseline,baseline,baseline if q.get("reward") or q.get("first_reward") else "无本步资源奖励",baseline))
        if book is not None and qid not in STEP_EVIDENCE and any(
                cond["type"] == 39 for ph in phases for cond in ph["conditions"]):
            evidence = "test_native_story_scene.py"
            accept,interaction,settle = "测试预置本步在途", "原生切入所需场景", "完成次数与后续步骤已核对"
            reload = "回归含一个重启样例，未逐目标重登"
        if group in extra_tails and qid==extra_tails[group]:
            evidence="test_native_extra_story_rewards.py"
            accept="测试预置接取首步" if qid==220012 else "前序测试预置已完成，末步在途"
            interaction,settle,reward="原生对话或机关完成末步", "已完成待领取2→领取后3", "原生末步ID和类型9结算，记录与实际库存已核对"
            reload="已覆盖代表故事重启，未逐项单独重启"
        elif qid==220011:
            evidence,accept,interaction,settle="test_native_extra_story_rewards.py","测试预置本步在途","原生对话","自动接续220012"
        elif qid in commissions:
            accept="委托表的接取规则，未逐项实测"
            if qid in sample_commissions:
                evidence,accept=panel_test,"在对应广场NPC旁完成原生接取"
        elif qid==910101:
            evidence,accept,reload=panel_test,"两期限时活动在开放期间分别通过原生接取并核对重复","跨活动结束重登验证，旧进度保留"
        rows.append(dict(quest=qid,group=group,type=qt,story=sid,chapter=book,extra_catalog=extra_id,
                         phases=phases,step_reward=q.get("reward"),first_reward=q.get("first_reward"),
                         accept=accept,interaction=interaction,settlement=settle,reward=reward,relogin=reload,
                         evidence=evidence))
    story_details = []
    for entry in main:
        sid=entry["i_d"]; steps_in_story=[r for r in rows if r["story"]==sid]
        story_details.append(dict(story=sid,chapter=listed.get(sid),first=entry["quest_i_d"],
                                  prerequisite=entry.get("pre_story_unlock"),level=entry.get("lv_unlock"),
                                  time=entry.get("time_unlock"),reward=entry.get("reward"),review=entry.get("review_i_d"),
                                  dialogs=len(review_dialogs.get(entry.get("review_i_d"),[])),
                                  quests=[r["quest"] for r in steps_in_story],verified_scenario=COMPLETE.get(sid),
                                  settlement_test="test_native_story_catalog.py" if sid in listed else None,
                                  settlement_fixture="预置本节已完成状态，原生领取、防重与重启" if sid in listed else None))
    extra_details = [dict(group=ch["i_d"],first=r["quest_i_d"],prerequisite_quest=r.get("quest_unlock"),level=r.get("lv_unlock"),
                          time=r.get("time_unlock"),review=r.get("review_i_d"),
                          quests=[q["quest"] for q in rows if q["group"]==r["quest_i_d"]],
                          verified_terminal=extra_tails[r["quest_i_d"]],settlement_test="test_native_extra_story_rewards.py",natural_full_chain=False)
                     for ch in extra for r in ch["extra_quest"]]
    optional={}
    if client:
        dialogs=table(data,"Dialog","dialog")
        dialog_ids={d["i_d"] for d in dialogs}
        all_refs={i for ls in review_dialogs.values() for i in ls}
        directory_counts=Counter("额外" if "OutSideChapter" in r["asset_full_name"] else "活动" if "/Activity/" in r["asset_full_name"] else "主线" for r in dialogs)
        optional=dict(dialog_ids=len(dialog_ids),review_dialogs_resolved=len(all_refs & dialog_ids),
                      missing_review_dialogs=sorted(all_refs-dialog_ids),dialog_directories=directory_counts,
                      random_event_templates=len(table(data,"RandomEvent","random_event")),
                      npc_talk_groups=len(table(data,"NPC","talk_info")),
                      scripted_activities=sum(bool(r.get("dialog_i_d")) for r in table(data,"Activity","activity")))
    def summarize(group_id, section):
        selected = [r for r in rows if r[section]==group_id]
        return dict(id=group_id,stories=len({r["story"] if section=="chapter" else r["group"] for r in selected}),
                    tasks=len(selected),conditions=sum(len(ph["conditions"]) for r in selected for ph in r["phases"]))
    return dict(tutorial=TUTORIAL_EVENTS,panel_acceptance=dict(test=panel_test,commission_definitions=len(commissions),
                plaza_actors=len(set(commissions.values())),tested_commission_ids=sorted(sample_commissions),
                activity_dialogue_quest=910101,activity_ids=[5075,260815130]),chapters=[summarize(r["i_d"],"chapter") for r in chapters],
                extra_chapters=[summarize(r["i_d"],"extra_catalog") for r in extra],
                role_quests=dict(Counter(r["type"] for r in rows)), stories=story_details,extra=extra_details,quests=rows,
                review_groups=len(reviews),review_dialogs=sum(map(len,review_dialogs.values())),client_index=optional)


def summary(report):
    cat=report["chapters"];ex=report["extra_chapters"]
    return dict(catalog_main_stories=sum(ch["stories"] for ch in cat), catalog_main_steps=sum(ch["tasks"] for ch in cat),
                catalog_main_conditions=sum(ch["conditions"] for ch in cat),main_definitions=len(report["stories"]),
                unlisted_main_ids=[s["story"] for s in report["stories"] if s["chapter"] is None],
                catalog_extra_stories=sum(ch["stories"] for ch in ex),catalog_extra_steps=sum(ch["tasks"] for ch in ex),
                verified_extra_terminals=len(report["extra"]),natural_full_extra=0,
                all_quest_records=len(report["quests"]),native_finished_main=sorted(COMPLETE))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server",type=Path,default=Path("OverField-Server"))
    parser.add_argument("--client-tables",type=Path,help="Optional complete local 1.1.12 client table export (Story, Quest, Achieve, Activity, Dialog, NPC, RandomEvent).")
    parser.add_argument("--full",action="store_true",help="Include a condition-by-condition row for every quest.")
    parser.add_argument("--verify",action="store_true",help="Rerun the mapped native protocol tests in disposable realms.")
    parser.add_argument("--output",type=Path,help="Write the JSON report here. Default is stdout.")
    args=parser.parse_args()
    server=args.server.resolve()
    if args.verify:
        verify(server)
    report=build(server,args.client_tables)
    totals=summary(report)
    out={"summary":totals, **report} if args.full else totals
    out["evidence_tests"]=list(TESTS)
    out["tests_reran_this_invocation"]=args.verify
    contents=json.dumps(out,ensure_ascii=False,indent=2)+"\n"
    if args.output:
        args.output.write_text(contents,encoding="utf-8")
    else:
        print(contents,end="")


if __name__=="__main__":
    main()
