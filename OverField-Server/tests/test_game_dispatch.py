"""Exercise real HTTP with optional mounted TapTap client (read-only)."""
import argparse
from contextlib import contextmanager
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from game_dispatch import Assets, BUILD, PATCH, VERSION, PREFIX, GameDispatch


@contextmanager
def listening(dispatch):
    from local_identity import LocalIdentity
    from private_profiles import create_profile_server
    with TemporaryDirectory() as state:
        identity=LocalIdentity(Path(state)/"identity.sqlite3")
        service=create_profile_server(identity,port=0)
        service.game_dispatch=dispatch
        thread=Thread(target=service.serve_forever,kwargs={"poll_interval":0.02},daemon=True)
        thread.start()
        try:
            yield service.server_port
        finally:
            service.shutdown()
            service.server_close()
            thread.join()


def http(port, method, path, data=None, headers=None):
    c=HTTPConnection("127.0.0.1",port,timeout=15)
    try:
        c.request(method,path,body=data,headers=headers or {})
        r=c.getresponse()
        return r.status,dict(r.getheaders()),r.read()
    finally:
        c.close()


class PublicDiscovery(unittest.TestCase):
    def setUp(self):
        self.dispatch=GameDispatch("http://127.0.0.1:18881","127.0.0.1",11033,region_name="本服")

    def test_region_list_and_gate(self):
        with listening(self.dispatch) as port:
            code,_,also=http(port,"POST","/private-game/dispatch/get_login_url_list")
            self.assertEqual(code,200)
            self.assertEqual(http(port,"POST","/private-game/local/v1/sessions",data=b"{}")[0],404)
            code,_,body=http(port,"POST","/dispatch/get_login_url_list")
            self.assertEqual((code,json.loads(body)),(200,{"LoginUrlList":[{"loginUrl":"http://127.0.0.1:18881","name":"本服"}]}))
            code,_,body=http(port,"POST","/dispatch/region_info",data=b"ignored")
            d=json.loads(body)
            self.assertEqual((code,d["gate_tcp_ip"],d["gate_tcp_port"],d["currentVersion"]),(200,"127.0.0.1",11033,VERSION))
            self.assertTrue(d["is_server_open"])

    def test_empty_mount_and_other_paths(self):
        with listening(self.dispatch) as port:
            code,_,body=http(port,"POST","/dispatch/client_hot_update")
            self.assertEqual(code,503)
            self.assertIs(json.loads(body)["status"],False)
            self.assertEqual(http(port,"GET",f"/Resources/{BUILD}/{PREFIX}.version")[0],404)
            self.assertEqual(http(port,"GET","/dispatch/region_info")[0],405)
            self.assertEqual(http(port,"POST","/open/oauth")[0],404)
            self.assertEqual(http(port,"POST","/dispatch/region_info",headers={"Cookie":"synthetic=test"})[0],403)
            self.assertEqual(http(port,"GET","/local/v1/region")[0],200)

    def test_notice_contracts(self):
        with listening(self.dispatch) as port:
            status, _, body=http(port,"POST","/dispatch/get_notice_url_list")
            self.assertEqual((status,json.loads(body)),(200,{"data":[]}))
            status, _, body=http(port,"POST","/dispatch/get_notice_list")
            notices=json.loads(body)
            self.assertEqual((status,notices["data"]),(200,[]))
            self.assertGreater(notices["serverTime"],1_600_000_000_000)

    def test_refuses_changed_and_unlisted_content(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            source=object.__new__(Assets)
            source.root=root
            source._meta={}
            from threading import Lock
            source.plaza_lock=Lock()
            source.plaza_versions={}
            expected=hashlib.md5(b"test").hexdigest()
            path=root/"bundle"
            name=expected+".bundle"
            source._bundles={name:(path,4,expected)}
            path.write_bytes(b"test")
            with source.open(name) as contents:
                self.assertEqual(contents.read(),b"test")
            path.write_bytes(b"also")
            with self.assertRaises(ValueError):
                source.open(name)
            with self.assertRaises(FileNotFoundError):
                source.open("../../file.txt")

    def test_only_recognizes_previously_issued_private_manifests(self):
        from game_dispatch import _issued_manifest
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stock = root / "issued"
            stock.mkdir()
            version = b"2150-01-01-00-00-00"
            name = PREFIX + "_" + version.decode()
            blob = b"test staged content"
            (root / (name + ".bytes")).write_bytes(blob)
            (root / (name + ".hash")).write_text(hashlib.md5(blob).hexdigest())
            self.assertFalse(_issued_manifest(version, root, stock))
            (stock / (name + ".sha256")).write_text(hashlib.sha256(blob).hexdigest())
            self.assertTrue(_issued_manifest(version, root, stock))
            self.assertFalse(_issued_manifest(b"../../alternate", root, stock))
            (root / (name + ".bytes")).write_bytes(b"changed")
            (root / (name + ".hash")).write_text(hashlib.md5(b"changed").hexdigest())
            self.assertFalse(_issued_manifest(version, root, stock))

    def test_invalid_private_origin(self):
        for value in ("https://8.8.8.8","http://0.0.0.0","file://localhost","http://127.0.0.1/other","http://test@127.0.0.1"):
            with self.subTest(origin=value), self.assertRaises(ValueError):
                GameDispatch(value,"127.0.0.1",11033)


class MountedClient(unittest.TestCase):
    client_root = None
    @classmethod
    def setUpClass(cls):
        if cls.client_root is None:
            raise unittest.SkipTest("Optional original 1.1.12 client not mounted")
        cls.dispatch=GameDispatch("http://127.0.0.1:18881","127.0.0.1",11033,client_root=cls.client_root)

    def test_validated_source(self):
        source=self.dispatch.assets
        self.assertEqual(source.count,8936)
        self.assertEqual(source.from_cache + (source.count-source.from_cache),8936)
        print(f"Verified source availability and listed lengths: {source.count} active bundles, {source.from_cache} using update-cache copies.",flush=True)

    def test_scheduled_plaza_package(self):
        import struct
        import zlib
        import plaza_assets
        from realm_plaza import validate

        plan = validate({"theme": 23, "activities": [4010,5087,260212103,260212104], "arcade": 12003,
                         "opens_at":"2001-01-01 00:00:00", "closes_at":"2099-12-31 23:59:59"})
        class Schedule:
            def plaza(self):
                return plan
        self.dispatch.maintenance = Schedule()
        try:
            with listening(self.dispatch) as port:
                root = f"/Resources/{BUILD}/"
                def package():
                    status,_,body = http(port,"POST","/dispatch/client_hot_update")
                    self.assertEqual(status,200)
                    return json.loads(body)["currentVersion"].split("_")[1]
                version = package()
                self.assertNotEqual(version,PATCH)
                self.assertEqual(version,http(port,"GET",root+PREFIX+".version")[2].decode())
                fname = root+PREFIX+"_"+version
                manifest = http(port,"GET",fname+".bytes")[2]
                self.assertEqual(hashlib.md5(manifest).hexdigest(),http(port,"GET",fname+".hash")[2].decode())
                p = manifest.index(b"pbstaticconfigure_resbundle_pbstaticconfigure.bundle") + len(b"pbstaticconfigure_resbundle_pbstaticconfigure.bundle")
                bundle_name = manifest[p+6:p+38].decode()
                self.assertNotEqual(bundle_name,plaza_assets.STOCK_BUNDLE)
                bundle = http(port,"GET",root+bundle_name+".bundle")[2]
                self.assertEqual(bundle_name,hashlib.md5(bundle).hexdigest())
                self.assertEqual(manifest[p+40:p+48].decode(),struct.pack("<I",zlib.crc32(bundle)).hex())
                self.assertEqual(struct.unpack_from("<Q",manifest,p+48)[0],len(bundle))
                self.assertEqual(http(port,"GET",root+bundle_name+".bundle",headers={"Range":"bytes=3-17"})[2],bundle[3:18])
                plan.clear()
                self.assertEqual(package(),PATCH)
                self.assertEqual(http(port,"GET",root+PREFIX+".version")[2].decode(),PATCH)
        finally:
            self.dispatch.maintenance = None

    def test_http_reads_real_manifest_and_bundle(self):
        with listening(self.dispatch) as port:
            root=f"/Resources/{BUILD}/"
            status,_,data=http(port,"POST","/dispatch/client_hot_update")
            cfg=json.loads(data)
            self.assertEqual((status,cfg["currentVersion"],cfg["hotOssUrl"]),(200,VERSION,"http://127.0.0.1:18881/Resources"))
            self.assertTrue(cfg["status"])
            self.assertEqual((cfg["error_log_level"], cfg["open_error_log"], cfg["isTestServer"]), (2, False, False))
            self.assertEqual(http(port,"GET",root+PREFIX+".version")[2].strip().decode(),PATCH)
            status,_,manifest=http(port,"GET",root+f"{PREFIX}_{PATCH}.bytes")
            self.assertEqual(status,200)
            self.assertEqual(hashlib.sha256(manifest).hexdigest(),"8c467ba267e06ffa4cd6e3dcb2326a6afc403c7f5e1c2dc18bcc1e83410204d9")
            for md5 in ["cb672ef51110a72a0c591b1736c95053","e49d461782ed9bf3309d2c35662e132a"]:
                url=root+md5+".bundle"
                status,header,bundle=http(port,"GET",url)
                self.assertEqual(status,200)
                self.assertEqual(hashlib.md5(bundle).hexdigest(),md5)
                self.assertEqual(http(port,"HEAD",url)[1]["Content-Length"],str(len(bundle)))
                status,header,slice_=http(port,"GET",url+"?t=123",headers={"Range":"bytes=5-13"})
                self.assertEqual((status,slice_,header["Content-Range"]),(206,bundle[5:14],f"bytes 5-13/{len(bundle)}"))
            for leaf in ("../version","%2e%2e%2fversion","../../Other","config.txt", "f"*32+".bundle",""):
                self.assertEqual(http(port,"GET",root+leaf)[0],404)
            self.assertEqual(http(port,"GET","/Resources/unlisted/"+PREFIX+".version")[0],404)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--client-root",type=Path)
    parser.add_argument("--identity-source",type=Path,help="Optional development module directory")
    args,remaining=parser.parse_known_args()
    if args.identity_source:
        sys.path.insert(0,str(args.identity_source.resolve()))
    MountedClient.client_root=args.client_root
    unittest.main(argv=[sys.argv[0],*remaining])

