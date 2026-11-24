"""Check the lightweight player's hard-link contract with a small clean installation."""
from pathlib import Path
import os
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
client_tools = root / "client_tools" if (root / "client_tools").is_dir() else root / "kit/client_tools"
sys.path.insert(0, str(client_tools))
import prepare_client as player


class CompactPlayer(unittest.TestCase):
    def setUp(self):
        self.cleanup = TemporaryDirectory()
        self.addCleanup(self.cleanup.cleanup)
        self.base = Path(self.cleanup.name).resolve()
        self.src = self.base / "game"
        self.home = self.base / "private"
        self.src.mkdir()
        self.home.mkdir()
        self.paths = [Path("launcher.exe"), Path("GameAssembly.dll"), player.BUILT / ("a"*32 + ".bundle")]
        for rel in self.paths:
            path = self.src / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(rel.name.encode())
        self.config = self.src / "launcher_Data/data.unity3d"
        self.config.write_bytes(b"settings")
        (self.src / "version").write_text(player.BUILD)
        manifest = self.src / "launcher_Data/yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(player.PATCH)

    def prepare(self):
        def export(original, dst, endpoint):
            self.assertEqual(original.read_bytes(), b"settings")
            dst.write_bytes(b"my realm")
            return {"sha256": player.sha(dst)}
        with (patch.object(player, "KNOWN_SOURCE", player.sha(self.config)),
              patch.object(player, "LAUNCHER", player.sha(self.src / self.paths[0])),
              patch.object(player, "ASSEMBLY", player.sha(self.src / self.paths[1])),
              patch.object(player, "asset_paths", return_value=[self.src / rel for rel in self.paths]),
              patch.object(player, "export_config", side_effect=export)):
            return player._from_installed(self.src, self.home, "http://127.0.0.1:18881", lambda *a, **kw: None,
                                          compact_only=True)

    def test_link_failure_resumes_without_copying_bundle(self):
        real_link = os.link
        def fail_on_bundle_write(src, dst):
            if Path(dst).name.startswith(".of-link-check-"):
                return real_link(src, dst)
            raise OSError("unavailable")
        with patch.object(os, "link", side_effect=fail_on_bundle_write), self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse((self.home / ".client-preparing" / self.paths[2]).exists())
        output = self.prepare()
        private = self.home / "Client"
        self.assertEqual(output["sharedBytes"], (self.src / self.paths[2]).stat().st_size)
        self.assertTrue(os.path.samefile(private / self.paths[2], self.src / self.paths[2]))
        for rel in self.paths[:2]:
            self.assertFalse(os.path.samefile(private / rel, self.src / rel))
        self.assertEqual(self.config.read_bytes(), b"settings")

    def test_preflight_cross_volume_and_low_space(self):
        with patch.object(os, "link", side_effect=OSError("EXDEV")), self.assertRaises(ValueError):
            self.prepare()
        with patch.object(player.shutil, "disk_usage") as capacity, self.assertRaises(ValueError):
            capacity.return_value.free = 1
            self.prepare()
        self.assertFalse((self.home / "ClientSettings").exists())
        self.assertFalse((self.home / ".client-preparing").exists())
        self.assertFalse(list(self.home.glob(".of-link-check-*")))


if __name__ == "__main__":
    unittest.main()
