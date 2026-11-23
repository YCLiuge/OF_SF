import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

base = Path(__file__).resolve().parents[1]
tools = (base / "kit" if (base / "kit/client_tools").is_dir() else base) / "client_tools"
sys.path.insert(0, str(tools))
import client_media
import prepare_client as prep
from private_channel_privacy import protect_private_channel_log


class PrivateChannelPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "私服 & local"
        self.client = self.home / "Client"
        self.data = self.client / "launcher_Data"
        self.log = self.data / "Quicksdk/qgpc_log.txt"

    def mark(self):
        self.data.mkdir(parents=True)
        marker = json.dumps({"instance": "private-test-instance"})
        (self.client / ".of-private-client.json").write_text(marker)
        (self.home / "client-config.json").write_text(marker)
        (self.data / "app.info").write_text("OFPriv\n开放空间", "utf8")
        self.log.parent.mkdir()

    def assert_protected(self):
        self.assertEqual(self.log.read_bytes(), b"")
        self.assertFalse(self.log.stat().st_mode & 0o222)
        self.assertEqual(self.log.stat().st_nlink, 1)
        if os.name == "nt":
            for mode in ("ab", "wb"):
                with self.assertRaises(PermissionError):
                    self.log.open(mode).close()

    def test_new_old_and_repeat(self):
        self.mark()
        for state in ("missing", "writable", "readonly"):
            with self.subTest(state=state):
                if state != "missing":
                    self.log.chmod(stat.S_IREAD | stat.S_IWRITE)
                    self.log.write_bytes(b"username=synthetic-test")
                    if state == "readonly":
                        self.log.chmod(stat.S_IREAD)
                self.assertTrue(protect_private_channel_log(self.home))
                self.assert_protected()
                previous = self.log.stat()
                self.assertFalse(protect_private_channel_log(self.home))
                self.assertEqual(previous.st_ino, self.log.stat().st_ino)

    def test_hardlinks_never_touch_original_bytes_or_attributes(self):
        self.mark()
        for readonly in (False, True):
            with self.subTest(readonly=readonly):
                original = self.root / ("other-" + str(readonly))
                original.write_bytes(b"username=synthetic-test")
                if readonly:
                    original.chmod(stat.S_IREAD)
                before = original.stat()
                if self.log.exists():
                    self.log.chmod(stat.S_IREAD | stat.S_IWRITE)
                    self.log.unlink()
                os.link(original, self.log)
                self.assertTrue(protect_private_channel_log(self.home))
                self.assert_protected()
                self.assertFalse(self.log.samefile(original))
                self.assertEqual(original.read_bytes(), b"username=synthetic-test")
                self.assertEqual((original.stat().st_ino, original.stat().st_mode),
                                 (before.st_ino, before.st_mode))

    def test_wrong_product_or_marker_leaves_log_intact(self):
        self.mark()
        self.log.write_bytes(b"existing")
        (self.data / "app.info").write_text("Inutan\n开放空间", "utf8")
        with self.assertRaises(ValueError):
            protect_private_channel_log(self.home)
        (self.data / "app.info").write_text("OFPriv\n开放空间", "utf8")
        (self.home / "client-config.json").write_text('{"instance":"different"}')
        with self.assertRaises(ValueError):
            protect_private_channel_log(self.home)
        self.assertEqual(self.log.read_bytes(), b"existing")

    def test_redirected_sdk_folder_is_refused(self):
        self.mark()
        self.log.parent.rmdir()
        other = self.root / "other"
        other.mkdir()
        (other / self.log.name).write_bytes(b"source")
        if os.name == "nt":
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "New-Item -ItemType Junction -Path $env:OF_TEST_LINK "
                            "-Target $env:OF_TEST_TARGET -ErrorAction Stop | Out-Null"],
                           env={**os.environ, "OF_TEST_LINK": str(self.log.parent),
                                "OF_TEST_TARGET": str(other)}, check=True)
        else:
            self.log.parent.symlink_to(other, target_is_directory=True)
        with self.assertRaises(ValueError):
            protect_private_channel_log(self.home)
        self.assertEqual((other / self.log.name).read_bytes(), b"source")

    def test_prepare_from_local_and_iso_and_upgrade(self):
        source = self.root / "original"
        sd = source / "launcher_Data"
        for name in prep.RELEASE_DIRS | {"Quicksdk"}:
            (sd / name).mkdir(parents=True)
        for name in prep.RELEASE_ROOTS:
            (source / name).write_bytes(b"original")
        for name in prep.RELEASE_FILES:
            (sd / name).write_bytes(b"fixture")
        settings = sd / "data.unity3d"
        settings.write_bytes(b"original endpoint")
        original_log = sd / "Quicksdk/qgpc_log.txt"
        original_log.write_bytes(b"username=synthetic-test")
        (source / "version").write_text(prep.BUILD)
        version = sd / "yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version"
        version.parent.mkdir(parents=True)
        version.write_text(prep.PATCH)
        archive = self.root / client_media.ARCHIVE
        with zipfile.ZipFile(archive, "w") as output:
            for f in source.rglob("*"):
                if f.is_file():
                    output.write(f, f.relative_to(source).as_posix())

        def export(_source, target, endpoint, **_kw):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(endpoint)
            return {"sha256": prep.sha(target)}

        with mock.patch.multiple(prep, LAUNCHER=prep.sha(source / "launcher.exe"),
                                 ASSEMBLY=prep.sha(source / "GameAssembly.dll"),
                                 KNOWN_SOURCE=prep.sha(settings), export_config=export), \
             mock.patch.object(client_media, "open_media", zipfile.ZipFile):
            for kind, src in (("local", source), ("media", archive)):
                with self.subTest(kind=kind):
                    self.home = self.root / kind
                    self.log = self.home / "Client/launcher_Data/Quicksdk/qgpc_log.txt"
                    url = "http://127.0.0.1:18881"
                    prep.prepare(src, self.home, url, progress=lambda *_, **__: None)
                    self.assert_protected()
                    self.log.chmod(stat.S_IREAD | stat.S_IWRITE)
                    self.log.write_bytes(b"legacy log")
                    prep.prepare(self.home / "Client", self.home, url)
                    self.assert_protected()
                    self.assertEqual(original_log.read_bytes(), b"username=synthetic-test")


if __name__ == "__main__":
    unittest.main()
