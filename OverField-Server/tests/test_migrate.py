"""Regression checks for the portable state migration CLI (standard library)."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from migrate import backup, restore
from state_lock import StateBusy, exclusive_state


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.source = self.work/"server"/"data"
        self.source.mkdir(parents=True)
        self.key = hashlib.sha256(b"independent test profile").digest()
        for name, row in [("identity.sqlite3", self.key), ("game.sqlite",b"position-1")]:
            with closing(sqlite3.connect(self.source/name)) as db:
                db.execute("CREATE TABLE state(id INTEGER PRIMARY KEY, value BLOB)")
                db.execute("INSERT INTO state VALUES (1,?)", (row,))
                db.commit()
        self.archive = self.work/"migration.zip"
        self.target = self.work/"different-machine"/"data"

    def contents(self, directory, name):
        with closing(sqlite3.connect(directory/name)) as db:
            return db.execute("SELECT value FROM state ORDER BY id").fetchall()

    def test_migrate(self):
        (self.source/"custom"/"empty").mkdir(parents=True)
        (self.source/"custom"/"auxiliary.bin").write_bytes(b"fixture\0")
        # A committed frame remains in a live WAL; no snapshot may lose it.
        with closing(sqlite3.connect(self.source/"game.sqlite")) as owner:
            self.assertEqual(owner.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
            owner.execute("PRAGMA wal_autocheckpoint=0")
            owner.execute("INSERT INTO state VALUES (2, ?)",(b"new-position",))
            owner.commit()
            self.assertGreater((self.source/"game.sqlite-wal").stat().st_size, 0)
            backup(self.source,self.archive)
        with ZipFile(self.archive) as z:
            self.assertFalse(any(f.endswith(("-wal","-shm","-journal")) for f in z.namelist()))
        self.target.mkdir(parents=True)
        restore(self.archive,self.target)
        self.assertEqual(self.contents(self.target,"game.sqlite"),[(b"position-1",),(b"new-position",)])
        self.assertEqual(self.contents(self.target,"identity.sqlite3"), [(self.key,)])
        self.assertEqual((self.target/"custom"/"auxiliary.bin").read_bytes(),b"fixture\0")
        self.assertTrue((self.target/"custom"/"empty").is_dir())
        with self.assertRaises(FileExistsError): restore(self.archive,self.target)
        with self.assertRaises(FileExistsError): backup(self.source,self.archive)
        with self.assertRaises(ValueError): backup(self.source,self.source/"new.zip")

    def rewrite(self, mutation):
        backup(self.source,self.archive)
        with ZipFile(self.archive) as z:
            members={n:z.read(n) for n in z.namelist()}
        manifest=json.loads(members.pop("manifest.json"))
        mutation(members,manifest)
        with ZipFile(self.archive,"w") as z:
            for n,v in members.items(): z.writestr(n,v)
            z.writestr("manifest.json",json.dumps(manifest))
        with self.assertRaises((ValueError,sqlite3.Error)): restore(self.archive,self.target)
        self.assertFalse(self.target.exists())

    def test_traversal(self):
        def malicious(members,manifest):
            name="../escaped"
            members["data/"+name]=b"x"
            manifest["files"][name]={"size":1,"sha256":hashlib.sha256(b"x").hexdigest()}
        self.rewrite(malicious)
        self.assertFalse((self.work/"escaped").exists())

    def test_file_checksum(self):
        def tamper(members,manifest):
            name="data/game.sqlite"
            content=bytearray(members[name]); content[-1]^=1; members[name]=bytes(content)
        self.rewrite(tamper)

    def test_invalid_db_correct_digest(self):
        def bad(members,manifest):
            b=b"invalid"
            members["data/game.sqlite"]=b
            manifest["files"]["game.sqlite"]={"size":len(b),"sha256":hashlib.sha256(b).hexdigest()}
        self.rewrite(bad)

    def test_casefold_and_reserved(self):
        def invalid(members,manifest):
            manifest["dirs"].extend(["one","One","com1"])
        self.rewrite(invalid)

    def test_bad_source_leaves_no_output(self):
        (self.source/"game.sqlite").write_bytes(b"invalid")
        with self.assertRaises(ValueError): backup(self.source,self.archive)
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.work.glob(".of-state-snapshot-*")),[])

    def test_busy_and_crash_release(self):
        module=Path(__file__).resolve().parent
        code = ("import sys,os;sys.path.insert(0,sys.argv[1]);"
                "from state_lock import exclusive_state;"
                "guard=exclusive_state(sys.argv[2]);guard.__enter__();"
                "print('locked',flush=True);sys.stdin.readline();os._exit(0)")
        with subprocess.Popen([sys.executable,"-B","-c",code,str(module),str(self.source)],
                              stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True) as holder:
            try:
                self.assertEqual(holder.stdout.readline().strip(),"locked")
                with self.assertRaises(StateBusy): backup(self.source,self.archive)
            finally:
                holder.stdin.write("\n");holder.stdin.flush()
                holder.wait(timeout=10)
        backup(self.source,self.archive)
        with exclusive_state(self.target):
            with self.assertRaises(StateBusy):restore(self.archive,self.target)
        restore(self.archive,self.target)
        self.assertEqual(self.contents(self.target,"identity.sqlite3"),[(self.key,)])


if __name__=="__main__":
    unittest.main()

