"""Local-only tests for private profiles, separate game tickets and relocation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, closing
from http.client import HTTPConnection
import json
from pathlib import Path
import shutil
import tempfile
import sqlite3
import hashlib
import subprocess
import sys
from threading import Thread
import unittest

from local_identity import APPLICATION_ID, InvalidProfile, InvalidTicket, LocalIdentity, NicknameUnavailable
from private_profiles import REALM, create_profile_server


@contextmanager
def serve(identity, **kwargs):
    service = create_profile_server(identity, port=0, **kwargs)
    thread = Thread(target=service.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield service.server_port
    finally:
        service.shutdown()
        service.server_close()
        thread.join()


def request(port, verb, path, payload=None, extra=None):
    conn=HTTPConnection("127.0.0.1", port, timeout=4)
    headers={"Content-Type":"application/json", **(extra or {})}
    raw = json.dumps(payload).encode("utf-8") if payload is not None else None
    try:
        conn.request(verb, path, raw, headers)
        response=conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


class IdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"identity.sqlite3"
        self.now=1000
        self.ids=LocalIdentity(self.path,clock=lambda:self.now)

    def test_sessions(self):
        a, key = self.ids.create_profile()
        b, other = self.ids.create_profile()
        with self.assertRaises(InvalidProfile): self.ids.issue_ticket(a, other)
        with self.assertRaises(InvalidProfile): self.ids.issue_ticket(b, key)
        ticket=self.ids.issue_ticket(a,key)
        raw=self.path.read_bytes()
        self.assertNotIn(ticket.encode("ascii"),raw)
        self.assertNotIn(key.encode("ascii"),raw)
        self.assertEqual(self.ids.resolve_ticket(ticket),a)
        with self.assertRaises(InvalidTicket): self.ids.resolve_ticket(ticket)
        with self.assertRaises(InvalidTicket): self.ids.resolve_ticket(key)

    def test_expires_including_boundary(self):
        a,key=self.ids.create_profile()
        t=self.ids.issue_ticket(a,key)
        self.now+=90
        with self.assertRaises(InvalidTicket): self.ids.resolve_ticket(t)
        new=self.ids.issue_ticket(a,key)
        self.now+=89
        self.assertEqual(self.ids.resolve_ticket(new),a)

    def test_names_and_legacy_migration(self):
        slot,key=self.ids.create_profile("  海边  ")
        self.assertEqual(self.ids.profile_name(slot), "海边")
        with self.assertRaises(NicknameUnavailable):
            self.ids.create_profile("海边")
        with self.assertRaises(ValueError):
            self.ids.create_profile(" ")
        old=Path(self.tmp.name)/"legacy.sqlite"
        with closing(sqlite3.connect(old)) as db, db:
            db.execute("CREATE TABLE profiles(slot TEXT PRIMARY KEY,secret_hash BLOB NOT NULL) WITHOUT ROWID")
            db.execute("CREATE TABLE tickets(ticket_hash BLOB PRIMARY KEY,slot TEXT NOT NULL,expires INTEGER NOT NULL) WITHOUT ROWID")
            db.execute("INSERT INTO profiles VALUES(?,?)",(slot,hashlib.sha256(key.encode()).digest()))
            db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            db.execute("PRAGMA user_version=1")
        migrated=LocalIdentity(old)
        generated=migrated.profile_name(slot)
        self.assertTrue(generated.strip())
        self.assertEqual(migrated.resolve_ticket(migrated.issue_ticket(slot,key)),slot)
        self.assertEqual(LocalIdentity(old).profile_name(slot),generated)

    def test_private_cli(self):
        directory=Path(self.tmp.name)/"provision"
        file=Path(self.tmp.name)/"profile.json"
        service=Path(__import__("private_profiles").__file__).resolve()
        args=[sys.executable,"-B",str(service),"--state",str(directory),"--nickname","海星","--create-profile",str(file)]
        result=subprocess.run(args,capture_output=True,check=True)
        data=json.loads(file.read_bytes())
        self.assertEqual(data["nickname"],"海星")
        self.assertNotIn(data["profile_key"].encode(),result.stdout+result.stderr)
        self.assertNotEqual(subprocess.run(args,capture_output=True).returncode,0)
        with serve(LocalIdentity(directory/"identity.sqlite3"),allow_registration=False) as port:
            self.assertEqual(request(port,"POST","/local/v1/profiles",{})[0],403)
            status,session=request(port,"POST","/local/v1/sessions",{k:data[k] for k in ("slot","profile_key")})
            self.assertEqual((status,session["nickname"]),(200,"海星"))

    def test_parallel_consume(self):
        a,key=self.ids.create_profile()
        t=self.ids.issue_ticket(a,key)
        def consume(_):
            try:
                return self.ids.resolve_ticket(t)
            except InvalidTicket:
                return None
        with ThreadPoolExecutor(max_workers=8) as workers:
            results=list(workers.map(consume,range(8)))
        self.assertEqual(results.count(a),1)
        self.assertEqual(results.count(None),7)

    def test_cold_relocation(self):
        a,key=self.ids.create_profile()
        used=self.ids.issue_ticket(a,key)
        self.assertEqual(self.ids.resolve_ticket(used),a)
        relocated=Path(self.tmp.name)/"relocated"
        relocated.mkdir()
        path=relocated/self.path.name
        shutil.copyfile(self.path,path)  # All writers have closed cleanly before copying.
        recovered=LocalIdentity(path, clock=lambda:self.now)
        with self.assertRaises(InvalidTicket): recovered.resolve_ticket(used)
        self.assertEqual(recovered.resolve_ticket(recovered.issue_ticket(a,key)),a)
        foreign=LocalIdentity(Path(self.tmp.name)/"other.sqlite3")
        with self.assertRaises(InvalidProfile): foreign.issue_ticket(a,key)

    def test_http_flow(self):
        with serve(self.ids,game_host="game.test",game_port=22222,allowed_hosts=["profiles.test"]) as port:
            st,region=request(port,"GET","/local/v1/region",extra={"Host":"profiles.test"})
            self.assertEqual((st,region["realm"],region["game_host"],region["game_port"]),(200,REALM,"game.test",22222))
            self.assertEqual(region["game_protocol"],"overfield-packethead-protobuf")
            st,profile=request(port,"POST","/local/v1/profiles",{"nickname":"海风"})
            self.assertEqual((st,profile["nickname"]),(201,"海风"))
            self.assertEqual(request(port,"POST","/local/v1/profiles",{"nickname":"海风"})[0],409)
            auth={k:profile[k] for k in ("slot","profile_key")}
            st,session=request(port,"POST","/local/v1/sessions",auth)
            self.assertEqual(st,200)
            self.assertEqual(session["region"],region)
            self.assertEqual(self.ids.resolve_ticket(session["ticket"]),profile["slot"])
            # These requests reject headers before reading a body.
            for host in ("other.example","127.0.0.1@other","fake.invalid:443","[::1]@example"):
                self.assertEqual(request(port,"POST","/local/v1/profiles",extra={"Host":host})[0],403)
            self.assertEqual(request(port,"POST","/local/v1/profiles",extra={"Origin":"null"})[0],403)
            self.assertEqual(request(port,"POST","/local/v1/profiles",{"extra":"x"})[0],400)
            self.assertEqual(request(port,"POST","/local/v1/sessions",{"provider":"third-party"})[0],400)
            self.assertEqual(request(port,"POST","/local/v1/sessions",{"slot":"unknown","profile_key":"invalid"})[0],401)
            self.assertEqual(request(port,"POST","/local/v1/sessions",{"slot":"x"*3000})[0],413)
        with serve(self.ids,allow_registration=False) as port:
            self.assertEqual(request(port,"POST","/local/v1/profiles",{})[0],403)
            self.assertEqual(request(port,"POST","/local/v1/sessions",auth)[0],200)


if __name__=="__main__":
    unittest.main()

