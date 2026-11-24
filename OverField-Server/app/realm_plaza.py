"""Operator selection for the native plaza stage and associated activity entrances."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import json
from pathlib import Path

from realm_events import _timestamp

CN = timezone(timedelta(hours=8))
PAGES = {5075: "幽冥庭院（首期）", 260815130: "幽冥庭院（复刻）", 260815020: "桂影同欢",
         4010: "万家灯火（活动页）", 260212104: "万家灯火（签到）", 260212103: "巧日得春（签到）",
         5087: "限时活动·年夜饭"}
ARCADES = {12000: "罗塔咖啡厅", 12001: "罗塔火锅店", 12002: "罗塔圣诞屋", 12003: "罗塔年夜饭"}


@lru_cache(maxsize=1)
def themes():
    src = Path(__file__).resolve().parents[1] / "resources/data/Scene.json"
    return {r["i_d"]: r for r in json.loads(src.read_bytes())["scene_theme"]["datas"]}


@lru_cache(maxsize=1)
def wheel_editions():
    from realm_events import available
    kinds = {"turntable", "sandbag", "koi", "sign_in", "battle_pass", "invite", "recall"}
    return {r["id"]: r["name"] for r in available() if r["kind"] in kinds and r["opens_at"] and r["closes_at"]}


@lru_cache(maxsize=1)
def dated_passes():
    from realm_events import available
    return frozenset(r["id"] for r in available() if r["kind"] == "battle_pass" and r["id"] in wheel_editions())


@lru_cache(maxsize=1)
def photo_editions():
    from realm_events import available
    return {r["id"]: r for r in available() if r["kind"] == "photo_show"}


KOI = 260326180


def koi_deadline(opens_at, closes_at):
    from realm_events import _catalog
    row = _catalog()[KOI]
    original = datetime.fromisoformat(row["settle_at"]) - datetime.fromisoformat(row["opens_at"])
    return min(closes_at, (datetime.fromisoformat(opens_at) + original).strftime("%Y-%m-%d %H:%M:%S"))


def photo_deadline(aid, opens_at, closes_at):
    row = photo_editions()[aid]
    if row["submit_until"] == row["closes_at"]:
        return closes_at
    window = datetime.fromisoformat(row["submit_until"]) - datetime.fromisoformat(row["opens_at"])
    return min(closes_at, (datetime.fromisoformat(opens_at) + window).strftime("%Y-%m-%d %H:%M:%S"))


def controlled_activities(ids):
    selected = set(ids)
    return (PAGES.keys() | selected
            | (set(photo_editions()) if selected.intersection(photo_editions()) else set())
            | (dated_passes() if selected.intersection(dated_passes()) else set()))


def catalog():
    return {"themes": [{"id": i, "name": Path(r["asset_path"]).stem.lstrip("-"),
                        "opens_at": r.get("opening_time"), "closes_at": r.get("closing_time")}
                       for i, r in themes().items()],
            "activities": [{"id": i, "name": v} for i, v in (PAGES | wheel_editions() | {i:r["name"] for i,r in photo_editions().items()}).items()],
            "arcades": [{"id": i, "name": v} for i, v in ARCADES.items()]}


def validate(data):
    if not isinstance(data, dict):
        raise ValueError("Expected a plaza configuration object")
    if not data:
        return {}
    if data.keys() != {"theme", "activities", "arcade", "opens_at", "closes_at"}:
        raise ValueError("Expected theme, activities, arcade, opens_at and closes_at")
    theme, arcade, pages = data["theme"], data["arcade"], data["activities"]
    if type(theme) is not int or theme not in themes():
        raise ValueError("Unknown plaza theme")
    if type(arcade) is not int or (arcade != 0 and arcade not in ARCADES):
        raise ValueError("Unknown plaza arcade")
    if not isinstance(pages, list) or any(type(i) is not int or i not in PAGES and i not in wheel_editions() and i not in photo_editions() for i in pages) or len(set(pages)) != len(pages):
        raise ValueError("Unknown or duplicate plaza activity")
    if len(set(pages).intersection(photo_editions())) > 1:
        raise ValueError("Choose one photo competition at a time")
    if len(set(pages).intersection(dated_passes())) > 1:
        raise ValueError("Choose one battle pass season at a time")
    if 5075 in pages and 260815130 in pages:
        raise ValueError("Choose one edition of the courtyard dialogue")
    for key in ("opens_at", "closes_at"):
        if not data[key]:
            raise ValueError("Both dates are required in UTC+8")
        _timestamp(data[key])
    if data["opens_at"] >= data["closes_at"]:
        raise ValueError("Plaza opening must precede closing")
    return {**data, "activities": sorted(pages)}


def active(data, instant=None):
    if not data:
        return False
    when = (instant or datetime.now(CN)).strftime("%Y-%m-%d %H:%M:%S")
    return data["opens_at"] <= when <= data["closes_at"]
