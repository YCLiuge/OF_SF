"""Live HTTP, CLI state, original 1.1.12 bulletin shape and fixed-origin verified releases."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import socket
import struct
from tempfile import TemporaryDirectory
from threading import Barrier, Thread
from unittest.mock import patch
import unittest
import zlib
from local_identity import LocalIdentity
from private_profiles import create_profile_server
from game_dispatch import GameDispatch
from realm_maintenance import Maintenance, SelectedFeed, checked_feed, native_notices
from test_game_dispatch import http, listening
import notice_images


class Tests(unittest.TestCase):
    @staticmethod
    def png(red=0):
        def chunk(kind,data):
            return struct.pack(">I",len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data))
        return (b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",1,1,8,2,0,0,0))+
                chunk(b"IDAT",zlib.compress(bytes((0,red,0,0))))+chunk(b"IEND",b""))

    def test_notice_images_and_migration(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);identity=LocalIdentity(root/"identity.sqlite3")
            # A pre-image realm already has text notices.
            with closing(sqlite3.connect(identity.path)) as db, db:
                db.execute("CREATE TABLE realm_notices(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,"
                           "content TEXT NOT NULL,published INTEGER NOT NULL,active INTEGER NOT NULL)")
                db.execute("INSERT INTO realm_notices VALUES(1,'旧公告','旧文',100,1)")
            owner=Maintenance(identity)
            self.assertEqual(owner.feed()["notices"][0],dict(id=1,title="旧公告",content="旧文",published=100))
            p=root/"a.png";q=root/"b.png";bad=root/"bad.png"
            p.write_bytes(self.png());q.write_bytes(self.png(128));bad.write_bytes(self.png()[:-1])
            a=owner.edit_notice(1,title="图文",images=[p,q])
            b=owner.announce("第二条","同一张图",[p])
            self.assertRaises(ValueError,owner.edit_notice,a,title="错误",images=[bad])
            self.assertRaises(ValueError,owner.announce,"过多","失败",[q]*5)
            self.assertEqual(owner.edit_notice(a,body="更新正文"),a)
            feed=owner.feed()
            self.assertEqual([r["id"] for r in feed["notices"]],[b,a])
            rich=feed["notices"][1]
            self.assertEqual((rich["title"],rich["content"]),("图文","更新正文"))
            images=rich["images"];self.assertEqual([i["width"] for i in images],[1,1])
            self.assertRaises(ValueError,notice_images.checked_images,[dict(images[0],url="https://external.invalid/p.png")])
            image_route=images[0]["url"]
            alternate=LocalIdentity(root/"copied.sqlite3")
            with closing(sqlite3.connect(identity.path)) as source, closing(sqlite3.connect(alternate.path)) as target:
                source.backup(target)
            self.assertEqual(Maintenance(alternate).open_image(images[1]["sha256"])[1],q.read_bytes())
            self.assertEqual(Maintenance(alternate).feed(),feed)
            d=GameDispatch("http://127.0.0.1:18881","127.0.0.1",11033)
            d.maintenance=owner
            @contextmanager
            def serving_owner():
                service=create_profile_server(identity,port=0)
                service.game_dispatch=d
                thread=Thread(target=service.serve_forever,kwargs={"poll_interval":0.02},daemon=True)
                thread.start()
                try:yield service.server_port
                finally:service.shutdown();service.server_close();thread.join()
            with serving_owner() as port:
                code,headers,data=http(port,"GET",image_route)
                self.assertEqual((code,headers["Content-Type"],data),(200,"image/png",p.read_bytes()))
                self.assertEqual(http(port,"HEAD",image_route)[2],b"")
                self.assertEqual(http(port,"GET",image_route,headers={"Authorization":"example"})[0],403)
                self.assertEqual(http(port,"GET",image_route+"../foo")[0],404)
                data=json.loads(http(port,"POST","/dispatch/get_notice_list")[2])["data"]
                blocks=data[1]["content"]
                self.assertEqual([r["orderId"] for r in blocks],[1,2,3])
                self.assertEqual(blocks[1]["imageUrl"],d.origin+"/private-game"+image_route)
                self.assertEqual((blocks[0]["text"],blocks[2]["imageHeight"]),("更新正文",1))
                class Channels:
                    @contextmanager
                    def open(self,p):
                        assert p==port
                        with socket.create_connection(("127.0.0.1",p),timeout=4) as channel:yield channel
                selected=SelectedFeed(type("Remote",(),dict(http_port=port,channels=Channels()))())
                proxy=GameDispatch("http://127.0.0.1:18882","127.0.0.1",11033)
                proxy.maintenance=selected
                with listening(proxy) as proxy_port:
                    self.assertEqual(http(proxy_port,"GET",image_route)[0],404)
                    self.assertEqual(http(proxy_port,"GET","/private-game"+image_route)[2],p.read_bytes())
                    owner.edit_notice(a,images=[])
                    self.assertNotIn("images",owner.feed()["notices"][1])
                    self.assertEqual(http(proxy_port,"GET","/private-game"+images[1]["url"])[0],404)
                    self.assertEqual(http(proxy_port,"GET","/private-game"+image_route)[0],200)
                    owner.withdraw(b)
                    self.assertEqual(http(proxy_port,"GET","/private-game"+image_route)[0],404)
                    self.assertRaises(ValueError,owner.edit_notice,b,title="已撤回")

    def test_shared_release_verification_and_withdrawal(self):
        from realm_admin import execute
        from state_lock import StateBusy, exclusive_state
        with TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/"realm"; state.mkdir()
            identity=LocalIdentity(state/"identity.sqlite3")
            owner=Maintenance(identity)
            payload=b"package-a"*4096; archive=root/"build.zip"; archive.write_bytes(payload)
            first=owner.publish(archive,"1.2.0",hashlib.sha256(payload).hexdigest(),"v1")
            service=create_profile_server(identity,port=0)
            thread=Thread(target=service.serve_forever,kwargs={"poll_interval":0.02},daemon=True)
            thread.start()
            def public():
                code,_,raw=http(service.server_port,"GET","/private/maintenance")
                self.assertEqual(code,200)
                return json.loads(raw)["release"]
            def admin():
                return execute(state,"maintenance.list",{},None)["release"]
            try:
                count=8; start=Barrier(count)
                def query(number):
                    start.wait(5)
                    return (admin if number%2 else public)()
                with patch("hashlib.file_digest",wraps=hashlib.file_digest) as hashed:
                    with ThreadPoolExecutor(max_workers=count) as workers:
                        self.assertEqual(list(workers.map(query,range(count))),[first]*count)
                    self.assertEqual(hashed.call_count,1)
                    with owner.open_release(first["sha256"])[0] as stream:
                        self.assertEqual(stream.read(),payload)
                    self.assertEqual(hashed.call_count,1)
                    with exclusive_state(state,purpose="profiles"):
                        self.assertRaises(StateBusy,admin)
                    file=owner.blobs/first["sha256"]; original=file.stat()
                    file.write_bytes(b"x"*len(payload))
                    os.utime(file,ns=(original.st_atime_ns,original.st_mtime_ns))
                    self.assertIsNone(admin()); self.assertIsNone(public())
                    self.assertRaises(ValueError,owner.open_release,first["sha256"])
                    self.assertEqual(hashed.call_count,2)
                    replacement=root/"fixed";replacement.write_bytes(payload)
                    os.utime(replacement,ns=(original.st_atime_ns,original.st_mtime_ns))
                    os.replace(replacement,file)
                    self.assertEqual(public(),first); self.assertEqual(admin(),first)
                    self.assertEqual(hashed.call_count,3)
                    # A metadata-only size change must also fail on a previous hash hit.
                    self.assertRaises(ValueError,owner.verified_stream,{**first,"size":first["size"]+1})
                    archive.write_bytes(b"package-b"*4096)
                    second=owner.publish(archive,"1.3.0",hashlib.sha256(archive.read_bytes()).hexdigest(),"v2")
                    self.assertEqual(admin(),second); self.assertEqual(public(),second)
                    self.assertEqual(hashed.call_count,4)
                    self.assertRaises(FileNotFoundError,owner.open_release,first["sha256"])
                    with owner.open_release(second["sha256"])[0] as already_open:
                        owner.withdraw_release()
                        self.assertIsNone(admin()); self.assertIsNone(public())
                        self.assertRaises(FileNotFoundError,owner.open_release,second["sha256"])
                        self.assertEqual(already_open.read(),archive.read_bytes())
                    self.assertEqual(hashed.call_count,4)
            finally:
                service.shutdown();service.server_close();thread.join()

    def test_bounded_realm_verifiers(self):
        import realm_maintenance as maintenance
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            # A referenced in-flight verifier remains the same even after its LRU slot is evicted.
            active=maintenance._realm_verifier(root/"first")
            for i in range(maintenance._MAX_VERIFIERS+1):
                maintenance._realm_verifier(root/str(i))
            self.assertEqual(len(maintenance._verifiers),maintenance._MAX_VERIFIERS)
            self.assertIs(maintenance._realm_verifier(root/"first"),active)

    def test_publication(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); identity=LocalIdentity(root/"identity.sqlite3")
            owner=Maintenance(identity); service=create_profile_server(identity,port=0)
            port=service.server_port
            d=GameDispatch(f"http://127.0.0.1:{port}","127.0.0.1",11033)
            d.maintenance=service.maintenance
            service.game_dispatch=d
            thread=Thread(target=service.serve_forever,kwargs={"poll_interval":0.03},daemon=True)
            thread.start()
            try:
                def get(method,url,**kw):
                    code,_,raw=http(port,method,url,**kw)
                    return code,json.loads(raw)
                self.assertEqual(get("GET","/private/maintenance"),(200,{"notices":[],"release":None}))
                n=owner.announce("芳汀的公告","集合地点\n今天开放调查")
                self.assertRaises(ValueError,owner.announce,"<invalid>","markup")
                self.assertEqual(get("GET","/private/maintenance",headers={"Cookie":"unneeded=test"})[0],403)
                self.assertEqual(get("POST","/private/maintenance")[0],404)
                item=get("POST","/private-game/dispatch/get_notice_list")[1]["data"][0]
                self.assertEqual((item["type"],item["title"],item["content"][0]["text"]),(0,"芳汀的公告","集合地点\n今天开放调查"))
                self.assertTrue(item["beforeLoginPopup"] and item["afterLoginPopup"])
                self.assertFalse(item["isPopup"])
                self.assertEqual(get("POST","/dispatch/get_notice_url_list")[1]["data"],[])
                artifact=root/"开放空间更新.zip";artifact.write_bytes(b"published public bytes" * 196608)
                digest=hashlib.sha256(artifact.read_bytes()).hexdigest()
                self.assertRaises(ValueError,owner.publish,artifact,"1.2.0","0"*64,"v2 notes")
                self.assertIsNone(get("GET","/private/maintenance")[1]["release"])
                release=owner.publish(artifact,"1.2.0",digest,"更新调查")
                self.assertEqual(Maintenance(identity).feed()["release"],release)
                route=release["url"]
                self.assertEqual(route,"/private/releases/"+digest)
                self.assertEqual(get("GET","/private/maintenance")[1]["release"]["filename"],artifact.name)
                self.assertEqual(http(port,"HEAD",route)[2],b"")
                code,headers,received=http(port,"GET",route)
                self.assertEqual((code,len(received),hashlib.sha256(received).hexdigest()),(200,release["size"],digest))
                self.assertEqual(get("GET",route+"../other")[0],404)
                self.assertEqual(get("GET",route,headers={"Authorization":"synthetic"})[0],403)
                for invalid in ["1.1.12","1.2.0","bad"]:
                    self.assertRaises(ValueError,owner.publish,artifact,invalid,digest,"v2 notes")
                self.assertRaises(ValueError,checked_feed,{"notices":[],"release":dict(release,url="https://external.invalid/x")})
                class Channels:
                    @contextmanager
                    def open(self,p):
                        assert p==port
                        with socket.create_connection(("127.0.0.1",p),timeout=4) as c:yield c
                remote=type("Remote",(),dict(http_port=port,channels=Channels()))()
                selected=SelectedFeed(remote)
                data=selected.feed()
                self.assertEqual(data,owner.feed())
                self.assertEqual(native_notices(data)[0]["title"],"私服版本 1.2.0")
                self.assertEqual(get("POST","/private-game/dispatch/get_notice_list")[1]["data"][0]["title"],"私服版本 1.2.0")
                # An altered published file is not streamed even when the size is unchanged.
                with (owner.blobs/digest).open("r+b") as f:f.write(b"x")
                self.assertEqual(get("GET",route)[0],503)
                self.assertIsNone(get("GET","/private/maintenance")[1]["release"])
                owner.withdraw(n);owner.withdraw_release()
                self.assertIsNone(Maintenance(identity).feed()["release"])
                self.assertEqual(get("POST","/dispatch/get_notice_list")[1]["data"],[])
                self.assertEqual(get("GET",route)[0],404)
                self.assertRaises(ValueError,owner.publish,artifact,"1.1.1",digest,"older")
            finally:
                service.shutdown();service.server_close();thread.join()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources")
    parser.parse_known_args()
    unittest.main(argv=[__file__])

