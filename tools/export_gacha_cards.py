"""Build localized recruit metadata and small toolbar portraits from local game assets."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import struct
from zipfile import ZipFile, ZipInfo

from PIL import Image
from check_axe_assets import Assets, PREFIX, bundle_table

ROOT = Path(__file__).resolve().parents[1] / "OverField-Server"
RELATIVE = "resources/operator/gacha-cards.zip"


def indexed(rows):
    return {r["i_d"]: r for r in rows}


def native_molly(client):
    # The early art export has a transparent sheet for Molly. Use the matching live sheet.
    import UnityPy
    mount = Assets(client)
    with mount.open_stock(f"{PREFIX}_{mount.patch}.bytes") as fp:
        manifest = fp.read()
    folder = "Assets/Development/Arts/UI/Atlas/CN/ResBundle/Texture/Character/Character_33/"
    asset = (folder + "UI_character_33_quanshen_01.png").encode("ascii")
    pos = manifest.index(struct.pack("<H", len(asset)) + asset) + 2 + len(asset)
    record = bundle_table(manifest, mount.count)[struct.unpack_from("<I", manifest, pos + 4)[0]]
    with mount.open_stock(record.digest + ".bundle") as fp:
        entry = UnityPy.load(fp.read()).container[asset.decode().lower()]
        return entry.read().image


def preview(image):
    rgba = image.convert("RGBA")
    bounds = rgba.getchannel("A").getbbox()
    if bounds is None:
        raise ValueError("Empty portrait")
    cropped = rgba.crop(bounds)
    cropped.thumbnail((286, 272), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    cropped.save(out, format="WEBP", quality=85, method=6, exact=True)
    return out.getvalue()


def build(args):
    data = json.loads(args.gacha.read_bytes())
    strings = json.loads(args.strings.read_bytes())
    titles = indexed(json.loads(args.gacha_strings.read_bytes()) if args.gacha_strings else strings["string_gacha"]["datas"])
    names = indexed(strings["string_character_name"]["datas"])
    item_names = json.loads((args.server / "resources/operator/item-names.zh.json").read_bytes())
    groups = indexed(data["reward_pool"]["datas"])
    files, cards = {}, {}
    with ZipFile(args.characters) as artwork:
        csv_path = next(name for name in artwork.namelist() if name.endswith(".csv"))
        prefix = csv_path.rsplit("/", 1)[0] + "/"
        rows = csv.DictReader(artwork.read(csv_path).decode("utf-8-sig").splitlines())
        pictures = {r["角色名"]: prefix + r["文件"].replace("\\", "/") for r in rows
                    if r["类型"] == "全身立绘" and r["空图"] != "是" and r["角色名"]}
        for row in data["info"]["datas"]:
            kind = row["new_u_i_gacha_type"]
            if kind not in (1, 2, 3, 4, 5):
                continue
            key = row["i_d"]
            text = titles[row["text_i_d"]]["text"]
            title = text[0] or re.search("【([^】]+)】", text[1])[1]
            spec = {"name": title, "kind": {1:"新人", 2:"常驻", 3:"探索员", 4:"映像", 5:"时装"}[kind],
                    "opening": row.get("opening_time", ""), "actor": "", "alias": "", "image": ""}
            cards[key] = spec
            if kind not in (3, 4) and key != 2001:
                continue
            group = groups[row["big_guarantee_pool_i_d"]]["items"]
            item = row.get("character_i_d1") or group[0]["item_i_d"]
            if key == 2001:
                alias = re.search("探索员「([^」]+)」", text[1])[1]
                item = next(i for i in names if item_names.get(str(i)) == alias)
            short = item_names[str(item)]
            spec["actor"] = names[item]["text"][0] if kind != 4 else short
            spec["alias"] = short
            filename = str(item) + ".webp"
            spec["image"] = "/admin/recruit-art/" + filename
            if filename in files:
                continue
            if kind == 4:
                poster = next(args.posters.glob(f"poster{(item - 10000) // 10:03d}_*.png"))
                with Image.open(poster) as source:
                    files[filename] = preview(source)
            elif item == 202002:
                files[filename] = preview(native_molly(args.client))
            else:
                source_path = pictures["虎" if short == "寅" else short]
                with Image.open(io.BytesIO(artwork.read(source_path))) as source:
                    files[filename] = preview(source)
    # Dates distinguish otherwise identical native titles across releases.
    sets = {}
    for key, card in cards.items():
        if card["kind"] in ("探索员", "映像"):
            sets.setdefault((card["name"],card["actor"]), []).append(key)
    for ids in sets.values():
        for key in ids:
            if len(ids)>1:
                opening = cards[key]["opening"][:10]
                label = "基础期" if key < 100000 else "复刻"
                cards[key]["edition"] = f"{label} · {opening}" if opening else label
    for card in cards.values():
        card.pop("opening")
    files["cards.json"] = json.dumps(cards, ensure_ascii=False, sort_keys=True, separators=(",",":")).encode()
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        for key, content in sorted(files.items()):
            archive.writestr(ZipInfo(key), content)
    raw = output.getvalue()
    target = args.server / RELATIVE
    digest = hashlib.sha256(raw).hexdigest()
    if args.expected and digest != args.expected:
        raise ValueError("Generated artwork does not match the expected SHA-256")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    print(f"{len(cards)} banners, {len(files)-1} illustrations, {len(raw)} bytes; SHA-256 {digest}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("gacha","strings","characters","posters","client"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--gacha-strings", type=Path, help="Optional string_gacha.datas export from the same client patch")
    parser.add_argument("--server", type=Path, default=ROOT)
    parser.add_argument("--expected", help="Verify generated bytes against the resource manifest")
    build(parser.parse_args())
