"""Synthetic editor/player metadata. No retail files or network are needed."""
import hashlib
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest

import inspect_client_build as build

BASE = "2025-01-01-00-00-00"
UPDATE = "2025-01-02-00-00-00"
PATCH = "Assets/Example/OnlyForTests.patch.bytes"


def string(s):
    b = s.encode()
    return struct.pack("<H", len(b)) + b


def packed(stamp, name, content, index=0):
    digest = hashlib.md5(content).hexdigest()
    info = build.HEADER + bytes(7) + string("BuiltinBuildPipeline") + string("DefaultPackage") + string(stamp)
    asset = string("demo") + string(name) + string("") + struct.pack("<H", 1) + string("synthetic") + struct.pack("<I", index)
    bundle = (string("synthetic.bundle") + bytes(4) + string(digest) + string("01234567") +
              struct.pack("<QBHH", len(content), 0, 0, 0))
    return info + struct.pack("<I", 1) + asset + struct.pack("<I", 1) + bundle


def release(directory, name, stamp, content=b"abc"):
    directory.mkdir(parents=True, exist_ok=True)
    blob = packed(stamp, name, content)
    leaf = directory / (build.PREFIX + "_" + stamp)
    leaf.with_suffix(".bytes").write_bytes(blob)
    leaf.with_suffix(".hash").write_text(hashlib.md5(blob).hexdigest())
    (directory / (build.PREFIX + ".version")).write_text(stamp)


def player(root):
    data = root / "launcher_Data"
    meta = data / "il2cpp_data/Metadata"
    meta.mkdir(parents=True)
    (root / "launcher.exe").touch()
    (root / "GameAssembly.dll").touch()
    (root / "version").write_text(BASE)
    (data / "ScriptingAssemblies.json").write_text(json.dumps({"names":["Assembly-CSharp.dll","IFix.Core.dll"]}))
    (data / "data.unity3d").write_bytes(b"UnityFS\0" + struct.pack(">I", 8) + b"5.x.x\x002021.3.45f2c1\x00")
    (meta / "global-metadata.dat").write_bytes(struct.pack("<II", 0xFAB11BAF, 31))
    release(data / build.STOCK, "Assets/Example/Scene.unity", BASE)
    return data


class Inventory(unittest.TestCase):
    def test_old_wrapper_and_cache_digest(self):
        with TemporaryDirectory() as work:
            outer = Path(work)
            (outer / "launcher.exe").touch()
            data = player(outer / "Data/download/games/11113/install")
            release(data / build.CACHE / "ManifestFiles", PATCH, UPDATE)
            content = b"abc"
            digest = hashlib.md5(content).hexdigest()
            leaf = data / build.CACHE / "CacheFiles" / digest[:2] / digest / "__data"
            leaf.parent.mkdir(parents=True)
            for payload in (None, b"bad", content):
                if payload is not None:
                    leaf.write_bytes(payload)
                info, _ = build.inspect(outer)
                item = info["cache"]["indexed_code_assets"][PATCH]
                self.assertEqual(item["verified_local"], "cache" if payload == content else None)
                self.assertEqual(item["mismatches"], {"cache":hashlib.md5(b"bad").hexdigest()} if payload == b"bad" else {})
            self.assertEqual(info["unity"], "2021.3.45f2c1")
            self.assertEqual(info["backend"], "IL2CPP")
            self.assertFalse(info["managed_game_dll"])
            self.assertEqual(info["metadata_header_version"], 31)
            self.assertFalse(any(info["project_markers"].values()))

    def test_cache_pairing_and_unknown_metadata(self):
        with TemporaryDirectory() as work:
            root, overlay = Path(work) / "game", Path(work) / "other"
            data, external = player(root), player(overlay)
            (data / "il2cpp_data/Metadata/global-metadata.dat").write_bytes(struct.pack("<II", 0xFAB11BAF, 99999))
            info, _ = build.inspect(root)
            self.assertIsNone(info["cache"])
            self.assertIsNone(info["metadata_header_version"])
            with self.assertRaisesRegex(ValueError, "活动清单"):
                build.inspect(root, overlay)
            release(external / build.CACHE / "ManifestFiles", PATCH, UPDATE)
            self.assertEqual(build.inspect(root, overlay)[0]["cache_source"], "provided")
            release(external / build.STOCK, "Assets/Other.unity", BASE)
            with self.assertRaisesRegex(ValueError, "不同的基础"):
                build.inspect(root, overlay)

    def test_malformed_metadata_and_comparison(self):
        with self.assertRaisesRegex(ValueError, "索引"):
            build.parse_manifest(packed(BASE, PATCH, b"a", index=1), BASE)
        with self.assertRaisesRegex(ValueError, "截断"):
            build.parse_manifest(build.HEADER + bytes(8), BASE)
        with TemporaryDirectory() as work:
            root = Path(work)
            data = player(root)
            (data / build.STOCK / (build.PREFIX + "_" + BASE + ".hash")).write_text("0" * 32)
            with self.assertRaisesRegex(ValueError, "hash"):
                build.inspect(root)
        self.assertEqual(build.compare({"a":"1","b":"2","c":"3"},{"a":"1","b":"4","d":"4"}),
                         {"unchanged":1,"changed":1,"added":1,"removed":1})


if __name__ == "__main__":
    unittest.main()
