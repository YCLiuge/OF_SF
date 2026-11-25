"""Validate the resource lifetime shared by gameplay modules across service restarts."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from utils.res_loader import ResourceCatalog


def run():
    with TemporaryDirectory() as root:
        base = Path(root)
        tables = base / "data"
        tables.mkdir()
        for name in ("ScenesConfigAsset", "Quest", "Character", "Flag", "Item"):
            (tables / (name + ".json")).write_text("{}", encoding="utf8")
        table = tables / "Fishing.json"
        table.write_text('{"areas":{"datas":[{"i_d":123,"name":"old"}]}}', encoding="utf8")
        resources = ResourceCatalog()
        resources.load(base)
        table.write_text('{"areas":{"datas":[{"i_d":124,"name":"new"}]}}', encoding="utf8")
        assert len(resources) == 6 and "Fishing" in resources and resources.get("absent") is None
        with ThreadPoolExecutor(max_workers=8) as pool:
            views = list(pool.map(lambda _: resources["Fishing"], range(32)))
        assert all(view is views[0] for view in views)
        with ThreadPoolExecutor(max_workers=8) as pool:
            indices = list(pool.map(lambda _: resources.index("Fishing", "areas"), range(32)))
        old = indices[0]
        assert all(index is old for index in indices)
        assert old[123]["name"] == "old" and resources.index("Fishing", "areas") is old
        resources.load(base)
        new = resources.index("Fishing", "areas")
        assert 123 not in new and new[124]["name"] == "new"
        table.write_text("{", encoding="utf8")
        try:
            resources.load(base)
        except json.JSONDecodeError:
            assert resources.index("Fishing", "areas") is new
        else:
            raise AssertionError("Invalid resource accepted")
        table.unlink()
        (tables / "Item.json").unlink()
        try:
            resources.load(base)
        except ValueError:
            assert resources["Fishing"]["areas"]["datas"][0]["name"] == "new"
        else:
            raise AssertionError("Missing required resource accepted")
    print("PASS immutable resource snapshot, concurrent first use, shared indices, reload and corrupt or missing inputs.")


if __name__ == "__main__":
    run()
