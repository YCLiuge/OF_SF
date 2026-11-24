"""Exercise the native coloring panel over TCP, including its durable unfinished roll."""
import argparse
from pathlib import Path
import random
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_dye_palette import colors
from test_native_gacha import call
from test_game_realm import login, join_after_intro
from utils import db
from utils.pb_create import make_item


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profiles = [identity.create_profile(n) for n in ("染色甲", "染色乙")]
    host = LocalGameService(identity, root, resources, port=0)
    clothes = 4011023
    ok = pb.StatusCode_OK

    def connect(slot):
        sock, _, player = login(host.port, identity.issue_ticket(*profiles[slot]))
        main, _ = join_after_intro(sock)
        return sock, player, main

    def amount(player, item):
        return pb.ItemDetail.FromString(db.get_item_detail(player, item)).main_item.base_item.num

    # Known pixels from the community reference implementation on the national texture.
    rng = random.Random(19)
    params = [rng.uniform(.2, .8) * (-1 if i % 8 == 7 else 1) for i in range(64)]
    param = pb.OutfitDyeParam(picture_id=1, params=params)
    assert colors(resources / "swirlnoisetexture", param, .5) == [
        (91,130,0), (127,186,2), (38,172,108), (2,178,168), (2,253,119)]

    host.start()
    try:
        a, owner, initial = connect(0)
        b, other, _ = connect(1)
        with a, b:
            def request(name, seq, **fields):
                return call(a, name, seq, outfit_id=clothes, **fields)

            with host.lock, db.conn:
                for item, count in ((401,3), (404,1), (102,100)):
                    db.set_item_detail(owner, make_item(item,count,owner).SerializeToString(), item)
            assert initial.un_save_outfit_dye_scheme.outfit_id == 0
            unlock = request("OutfitDyeUnlockIndex", 10, index=1)
            assert unlock.status == ok and request("OutfitDyeUnlockIndex", 10, index=1) == unlock
            assert request("OutfitDyeUnlockIndex", 11, index=1).status == pb.StatusCode_OUTFIT_SCHEME_INDEX_HAS_UNLOCKED
            assert request("OutfitDyeUnlockIndex", 12, index=2).status == pb.StatusCode_ITEM_NOT_ENOUGH
            assert request("OutfitDyeUnlockIndex", 13, index=5).status != ok
            assert request("OutfitColorantSelect", 14, colorant_item_id=999999).status == pb.StatusCode_CFG_NOT_FOUND
            assert request("OutfitColorantSelect", 15, colorant_item_id=404).status == ok
            assert request("OutfitRandomColor", 16, uvy=.25).status == pb.StatusCode_RANDOM_DYE_STUFF_NOT_RANDOM_COLOR
            value = dict(pos=1, scheme_index=1, add_rate_color_index=1, uvy=.5)
            fixed = request("OutfitDye", 17, **value)
            assert fixed.status == ok and fixed.pos_color == pb.PosColor(pos=1, red=255, green=135, blue=182)
            assert request("OutfitDye", 17, **value) == fixed
            assert call(b, "OutFitDyeSave", 18, outfit_id=clothes, pos=1, scheme_index=1, is_save_dye_result=True).status != ok
            assert request("OutFitDyeSave", 18, pos=2,scheme_index=1,is_save_dye_result=True).status != ok
            with host.lock:
                assert amount(owner, 404) == amount(owner, 102) == 0
                assert pb.Achieve.FromString(db.get_achieve(owner,80100709)).count == 1

        host.stop()
        host.start()
        a, same, main = connect(0)
        with a:
            assert same == owner and main.un_save_outfit_dye_scheme.color == fixed.pos_color
            saved = request("OutFitDyeSave", 19, pos=1, scheme_index=1, is_save_dye_result=True)
            assert saved.status == ok and saved.items[0].main_item.outfit.dye_schemes[1].colors[0] == fixed.pos_color
            assert request("OutFitDyeSave", 19, pos=1, scheme_index=1, is_save_dye_result=True) == saved
            p = request("OutfitColorantSelect", 20, colorant_item_id=401)
            assert p.status == ok and len(p.param.params)==64 and 1<=p.param.picture_id<=4
            assert request("OutfitColorantSelect", 20, colorant_item_id=401) == p
            key = pb.CommonParam(int_value=clothes)
            assert call(a, "GenericGameA", 71, generic_msg_id=17, params=[key]).status == pb.StatusCode_FAIL
            assert request("OutfitColorantSelect", 72, colorant_item_id=401) == p
            refreshed = call(a, "GenericGameA", 73, generic_msg_id=7, params=[key])
            assert refreshed.status == ok and refreshed.generic_msg_id == 7
            next_palette = request("OutfitColorantSelect", 74, colorant_item_id=401)
            assert next_palette.status == ok and next_palette.param != p.param
            assert call(a, "GenericGameA", 73, generic_msg_id=7, params=[key]) == refreshed
            p = next_palette
            with host.lock, db.conn:
                db.set_item_detail(owner,make_item(108,100,owner).SerializeToString(),108)
            unlocked = call(a, "GenericGameA", 75, generic_msg_id=5, params=[key])
            assert unlocked.status == ok and call(a, "GenericGameA", 75, generic_msg_id=5, params=[key]) == unlocked
            with host.lock:
                item = pb.ItemDetail.FromString(db.get_item_detail(owner, clothes))
                assert len(item.main_item.outfit.dye_schemes) == 3 and item.main_item.outfit.dye_schemes[2].is_un_lock
                assert amount(owner,108) == 0
            rgb = colors(resources / "swirlnoisetexture", p.param, .5)
            preview = request("OutfitRandomColor", 21, uvy=.5)
            assert preview.status == ok
            assert (preview.pos_color.red, preview.pos_color.green, preview.pos_color.blue) in rgb
            assert request("OutfitRandomColor", 22, uvy=float("nan")).status != ok
            assert request("OutfitDye", 23, pos=1, scheme_index=3, uvy=.5).status == pb.StatusCode_OUTFIT_SCHEME_INDEX_NOT_UNLOCK
            custom = pb.PosColor(pos=0, red=12, green=15, blue=18)
            assert request("OutfitDye", 24, specify_color=custom).status == pb.StatusCode_ITEM_NOT_ENOUGH
            with host.lock, db.conn:
                assert amount(owner, 401) == 3
                db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'fashion_dye','misses',5)", (owner,))
                db.set_item_detail(owner,make_item(102,100,owner).SerializeToString(),102)
            guaranteed = request("OutfitDye", 25, **value)
            assert guaranteed.status == ok and guaranteed.total_guarantee_dye_num == 0
            assert (guaranteed.pos_color.red, guaranteed.pos_color.green, guaranteed.pos_color.blue) == rgb[0]
            assert request("OutFitDyeSave", 26, pos=1, scheme_index=1, is_save_dye_result=False).status == ok
            result = request("OutfitDye", 27, specify_color=custom)
            assert result.status == ok and result.pos_color == custom
            with host.lock:
                assert (amount(owner, 401), amount(owner, 102)) == (1,0)
            assert request("OutFitDyeSave", 28, pos=0, scheme_index=0, is_save_dye_result=True).status == ok
        b, _, separate = connect(1)
        with b, host.lock:
            assert separate.un_save_outfit_dye_scheme.outfit_id == separate.total_guarantee_dye_num == 0
            original = pb.ItemDetail.FromString(db.get_item_detail(other, clothes)).main_item.outfit
            assert len(original.dye_schemes)==1 and not original.dye_schemes[0].colors
    finally:
        host.stop()
    print("PASS native fashion dye: five handlers, palette, slot costs, fixed/custom/random rolls, guarantee, replay, achievement, role isolation and relog.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp), parser.parse_args().resources)
