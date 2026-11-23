"""Replicate a retail-client record lookup followed by one single draw on a temporary realm."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from test_game_realm import join, login
from test_native_gacha import call


def run(root, resources):
    owner = LocalIdentity(root / "identity.sqlite3")
    profile = owner.create_profile("卡池入口核验")
    with patch.dict("os.environ", {"OF_STAGE_DIAGNOSTICS":"1"}):
        host = LocalGameService(owner, root, resources, port=0)
    host.start()
    try:
        user, _, _ = login(host.port, owner.issue_ticket(*profile))
        with user:
            join(user)
            accepted = call(user,"AcceptQuest",9,quest_id=1000001,quest_type=pb.EQuestType_MainQuest)
            assert accepted.quest.quest_id == 100011
            banners = call(user,"GachaList",10)
            assert banners.status == pb.StatusCode_OK and any(pool.gacha_id == 2000 for pool in banners.gachas)
            records = call(user,"GachaRecord",11,gacha_id=2000,page=1)
            assert records.status == pb.StatusCode_OK
            drawn = call(user,"Gacha",12,gacha_id=2000,is_single=True)
            assert drawn.status == pb.StatusCode_OK and len(drawn.items) == 1
            after = call(user,"GachaRecord",15,gacha_id=2000,page=1)
            assert len(after.records) == 1 and drawn.info.gacha_times == 1
            assert call(user,"AcceptQuest",16,quest_id=1000001,quest_type=pb.EQuestType_MainQuest).quest.quest_id == 100011
            assert call(user,"Gacha",13,gacha_id=4294967295,is_single=True).status != pb.StatusCode_OK
            audit = [json.loads(line)["gacha"] for line in (host.data_dir/"stage-gacha.jsonl").read_text().splitlines()]
            assert [(e["id"],e["status"],e["single"],e["mode"]) for e in audit] == [
                (2000,pb.StatusCode_OK,True,"normal"),("unknown",pb.StatusCode_BAD_REQ,True,"normal")]
            assert audit[0]["size"] > 0 and audit[0]["enabled"] and audit[0]["choice"] == 0
            assert "4294967295" not in (host.data_dir/"stage-gacha.jsonl").read_text()
    finally:
        host.stop()
    print("PASS native plaza recruit listing, history, single draw, rejected unknown pool and bounded redacted trace.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as scratch:
        run(Path(scratch),p.parse_args().resources)
