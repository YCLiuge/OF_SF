"""Build an isolated PostgreSQL cluster, exercise a native synthetic save, and compare reporting queries."""
import argparse
from contextlib import closing, contextmanager
import json
import os
import select
from pathlib import Path
from statistics import median
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
import zipfile


def psql(sql, expect=True):
    result=subprocess.run(["psql","-X","--no-password","-Atq","-v","ON_ERROR_STOP=1","-c",sql],
                          capture_output=True,text=True,encoding="utf8")
    if expect and result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip() if expect else result.returncode


@contextmanager
def postgres(root, bin_path):
    data=root/"cluster"; socket=root/"socket";socket.mkdir(mode=0o700)
    subprocess.run([str(bin_path/"initdb"),"-A","trust","-D",str(data),"--no-locale",
                    "--encoding=UTF8","-U","of_report_test"],stdout=subprocess.DEVNULL,check=True)
    os.environ.update(PGHOST=str(socket),PGPORT="5432",PGUSER="of_report_test",PGDATABASE="postgres",PGCLIENTENCODING="UTF8")
    for inherited in ("PGSERVICE","PGSERVICEFILE","PGPASSWORD","PGPASSFILE","PGHOSTADDR","PGOPTIONS"):
        os.environ.pop(inherited,None)
    ctl=[str(bin_path/"pg_ctl"),"-D",str(data),"-w"]
    # Private per-run Unix socket only, with no system PostgreSQL service configuration.
    subprocess.run([*ctl,"-l",str(root/"postgres.log"),"-o",f"-h '' -k {socket}","start"],stdout=subprocess.DEVNULL,check=True)
    try:
        yield
    finally:
        subprocess.run([*ctl,"-m","fast","stop"],stdout=subprocess.DEVNULL,check=True)


def seed(state,server,count):
    from local_game_service import LocalGameService
    from local_identity import LocalIdentity
    from test_game_realm import join_after_intro,login
    import realm_accounts
    identity=LocalIdentity(state/"identity.sqlite3")
    realm_accounts.initialize(identity)
    host=LocalGameService(identity,state,server/"resources",port=0)
    host.start()
    players=[]
    try:
        for i in range(count):
            if i%2==0:
                profile=realm_accounts.register(identity,{"username":f"report{i}","password":"synthetic-test-password",
                                                         "nickname":f"测试档{i}"},False)
                ticket=profile["ticket"]
            else:
                ticket=identity.issue_ticket(*identity.create_profile(f"测试档{i}"))
            sock,_,uid=login(host.port,ticket)
            with sock:
                join_after_intro(sock)
                players.append(uid)
    finally:
        host.stop()
    return players


def timing(sqlite_source, uid, player, targets):
    from proto.net_pb2 import ItemDetail
    queries=targets*4
    output={}
    for point in (False,True):
        si = "SELECT item_detail_blob FROM items WHERE item_id=?"
        pi = "SELECT count(*),coalesce(sum(quantity),0) FROM of_sf_report.inventory WHERE storage=0 AND item_id=$1 AND quantity IS NOT NULL"
        if point:
            si+=" AND player_id=?"
            pi+=" AND uid="+str(uid)
        sql="PREPARE report_q(bigint) AS "+pi+";\n" + "\n".join(f"EXECUTE report_q({item});" for item in queries)
        sqlite_samples=[];pg_samples=[]
        def sqlite_batch():
            answer=[]
            for item in queries:
                values=(item,player) if point else (item,)
                rows=[]
                for raw, in sqlite_source.execute(si,values):
                    main=ItemDetail.FromString(raw).main_item
                    if main.HasField("base_item"):
                        rows.append(main.base_item.num)
                answer.append(f"{len(rows)}|{sum(rows)}")
            return answer
        def pg_batch():
            # One psql process and one local connection for the entire batch.
            proc=subprocess.run(["psql","-X","--no-password","-Atq","-v","ON_ERROR_STOP=1"],input=sql+"\n",
                                text=True,encoding="utf8",capture_output=True,check=True)
            return proc.stdout.strip().splitlines()
        expected=sqlite_batch()
        for turn in range(5):
            for fn,samples in (((sqlite_batch,sqlite_samples),(pg_batch,pg_samples)) if not turn%2 else
                               ((pg_batch,pg_samples),(sqlite_batch,sqlite_samples))):
                started=perf_counter();answer=fn();samples.append((perf_counter()-started)*1000)
                assert answer==expected
        plan=psql("EXPLAIN(COSTS OFF) "+pi.replace("$1",str(queries[0])))
        output["player_and_item" if point else "item_across_players"]={
            "queries":len(queries),"sqlite_ms":round(median(sqlite_samples),2),
            "postgres_ms":round(median(pg_samples),2),"plan":plan.splitlines()}
    return output


def legacy_and_empty(root):
    import migrate
    from postgres_report import project
    from proto.net_pb2 import ItemDetail
    legacy=root/"legacy";legacy.mkdir()
    with closing(sqlite3.connect(legacy/"identity.sqlite3")) as conn,conn:
        conn.execute("CREATE TABLE profiles(slot TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO profiles VALUES('local-only')")
    with closing(sqlite3.connect(legacy/"game.sqlite")) as db,db:
        db.executescript("""CREATE TABLE players(player_id INTEGER PRIMARY KEY,player_name TEXT,level INTEGER,world_level INTEGER);
                         CREATE TABLE items(player_id INTEGER,item_id INTEGER,item_detail_blob BLOB);
                         CREATE TABLE items_s(player_id INTEGER,instance_id INTEGER,item_detail_blob BLOB);""")
    empty=root/"empty.zip";migrate.backup(legacy,empty)
    assert project("refresh",empty)["inventory"]==0 and project("verify",empty)["players"]==0
    detail=ItemDetail()
    detail.main_item.item_id=101
    detail.main_item.base_item.num=17
    with closing(sqlite3.connect(legacy/"game.sqlite")) as db,db:
        db.execute("INSERT INTO players VALUES(18,'old',7,0)")
        db.execute("INSERT INTO items VALUES(18,101,?)",(detail.SerializeToString(),))
    archive=root/"legacy.zip";migrate.backup(legacy,archive)
    assert project("refresh",archive)["players"]==1 and project("verify",archive)["accounts"]==0
    assert psql("SELECT uid,has_profile,has_account,scene IS NULL FROM of_sf_report.players")=="18|f|f|t"


def check_reader_snapshot(refresh, public):
    proc=subprocess.Popen(["stdbuf","-oL","psql","-X","--no-password","-Atq","-v","ON_ERROR_STOP=1"],
                          stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,encoding="utf8")
    def query(sql):
        proc.stdin.write(sql+"\n");proc.stdin.flush()
        if not select.select([proc.stdout],[],[],8)[0]:
            raise AssertionError("Reader timed out waiting for a consistent generation")
        return proc.stdout.readline().strip()
    sql=f"SELECT level FROM of_sf_report.players WHERE uid={public};"
    try:
        original=query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; "+sql)
        refresh()
        assert query(sql)==original and original!="42"
        assert query("COMMIT; "+sql)=="42"
    finally:
        proc.stdin.close()
        try: proc.wait(timeout=6)
        except subprocess.TimeoutExpired: proc.kill();proc.wait()
        proc.stdout.close()
    assert proc.returncode==0


def run(server,bin_path,count,output):
    sys.path[:0]=[str(server/x) for x in ("app","app/upstream","tests","vendor/linux-x86_64","vendor/common")]
    import migrate
    from postgres_report import project
    from proto.net_pb2 import ItemDetail
    with TemporaryDirectory(prefix="of-pg-test-") as tmp:
        root=Path(tmp)
        state=root/"state";state.mkdir()
        players=seed(state,server,count)
        public=4200000001;large=1 << 40
        with closing(sqlite3.connect(state/"game.sqlite")) as db,db:
            db.execute("INSERT OR REPLACE INTO realm_public_uids(player_id,uid) VALUES(?,?)",(players[0],public))
            nick="测试\\N\t自定义\n\\."
            db.execute("UPDATE players SET player_name=? WHERE player_id=?",(nick,players[0]))
            item=ItemDetail.FromString(db.execute("SELECT item_detail_blob FROM items WHERE player_id=? AND item_id=101",(players[0],)).fetchone()[0])
            item.main_item.base_item.num=large
            db.execute("UPDATE items SET item_detail_blob=? WHERE player_id=? AND item_id=101",(item.SerializeToString(),players[0]))
        first=root/"first.zip";migrate.backup(state,first)
        with postgres(root,bin_path):
            legacy_and_empty(root)
            result=project("refresh",first)
            assert result["players"]==count and result["profiles"]==count and result["accounts"]==(count+1)//2
            assert psql("SELECT count(*) FROM of_sf_report.players WHERE has_profile") == str(count)
            assert psql("SELECT count(*) FROM of_sf_report.players WHERE has_account") == str((count+1)//2)
            assert psql(f"SELECT quantity FROM of_sf_report.inventory WHERE uid={public} AND entry_id=101 AND storage=0")==str(large)
            assert psql(f"SELECT encode(convert_to(nickname,'UTF8'),'hex') FROM of_sf_report.players WHERE uid={public}")==nick.encode().hex()
            command=subprocess.run([sys.executable,"-B",str(server/"of.py"),"postgres-report","verify",str(first)],
                                   text=True,encoding="utf8",capture_output=True)
            assert command.returncode==0,command.stderr
            assert json.loads(command.stdout)["players"]==count
            original_xmin=psql("SELECT xmin FROM of_sf_report.snapshot")
            assert project("verify",first)["inventory"]==result["inventory"]
            project("refresh",first)
            assert psql("SELECT xmin FROM of_sf_report.snapshot")==original_xmin
            stable_xmin=psql(f"SELECT xmin FROM of_sf_report.inventory WHERE uid={public} AND storage=0 AND entry_id=102")
            # Abort after one player's update and before completing an inventory change.
            with closing(sqlite3.connect(state/"game.sqlite")) as db,db:
                db.execute("UPDATE players SET level=42 WHERE player_id=?",(players[0],))
                item.main_item.base_item.num-=1
                db.execute("UPDATE items SET item_detail_blob=? WHERE player_id=? AND item_id=101",
                           (item.SerializeToString(),players[0]))
            second=root/"second.zip";migrate.backup(state,second)
            psql("""CREATE FUNCTION public.reject_report() RETURNS trigger LANGUAGE plpgsql AS
                   $$ BEGIN RAISE EXCEPTION 'fixture failure'; END $$;
                   CREATE TRIGGER fail_load BEFORE INSERT ON of_sf_report.inventory
                   FOR EACH ROW EXECUTE FUNCTION public.reject_report();""")
            try:project("refresh",second)
            except (RuntimeError,OSError):pass
            else:raise AssertionError("Expected injected failure")
            assert psql("SELECT xmin FROM of_sf_report.snapshot")==original_xmin
            project("verify",first)
            psql("DROP TRIGGER fail_load ON of_sf_report.inventory")
            check_reader_snapshot(lambda:project("refresh",second),public)
            assert psql(f"SELECT xmin FROM of_sf_report.inventory WHERE uid={public} AND storage=0 AND entry_id=102")==stable_xmin
            assert psql(f"SELECT level FROM of_sf_report.players WHERE uid={public}")=="42"
            psql(f"UPDATE of_sf_report.inventory SET quantity=3 WHERE uid={public} AND storage=0 AND entry_id=101")
            try:project("verify",second)
            except (RuntimeError,OSError):pass
            else:raise AssertionError("Failed to detect a modified quantity")
            project("refresh",second)
            # A reader can inspect and independently verify but cannot update the committed report.
            psql("CREATE ROLE report_reader LOGIN; GRANT USAGE ON SCHEMA of_sf_report TO report_reader; "
                 "GRANT SELECT ON ALL TABLES IN SCHEMA of_sf_report TO report_reader;")
            os.environ["PGUSER"]="report_reader"
            project("verify",second)
            assert psql("DELETE FROM of_sf_report.players",expect=False)!=0
            assert psql("SELECT count(*) FROM information_schema.tables WHERE table_schema='of_sf_report'")=="3"
            os.environ["PGUSER"]="of_report_test"
            with closing(sqlite3.connect(state/"game.sqlite")) as db:
                targets=[v[0] for v in db.execute("SELECT item_id FROM items WHERE player_id=? ORDER BY item_id LIMIT 60",(players[0],))]
                timings=timing(db,public,players[0],targets)
            assert "of_sf_inventory_item" in str(timings["item_across_players"]["plan"])
            result.update(postgresql=psql("SHOW server_version"),bench=timings,uid_above_int32=True,
                          balance_above_uint32=True,read_only_role=True,rollback=True,replay_no_rewrite=True,
                          concurrent_reader_snapshot=True)
            print(json.dumps(result,indent=2))
            if output:output.write_text(json.dumps(result,indent=2),encoding="utf8")


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--server",type=Path,required=True);p.add_argument("--pg-bin",type=Path,required=True)
    p.add_argument("--clients",type=int,default=24);p.add_argument("--output",type=Path)
    a=p.parse_args()
    if a.clients<2 or os.geteuid()==0:p.error("Run as an ordinary Linux user with two or more clients")
    run(a.server.resolve(),a.pg_bin.resolve(),a.clients,a.output)
