"""Compare old and indexed name reads on synthesized players in a disposable OF_SF game database."""
import argparse
import json
import math
from pathlib import Path
import sqlite3
import statistics
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns


def run(server,count,rounds):
    platform="win64" if sys.platform=="win32" else "linux-x86_64"
    sys.path[:0]=[str(server/d) for d in ("app","app/upstream","vendor/"+platform,"vendor/common")]
    from local_identity import LocalIdentity
    from realm_database import open_game_database
    from utils import db
    import player_name_index as index
    from social_query import name_key,name_digest
    with TemporaryDirectory() as scratch:
        root=Path(scratch)
        open_game_database(LocalIdentity(root/"identity.sqlite3"),root)
        conn=db.conn
        try:
            player=lambda i:f"玩家Ｆ{i:07}"
            with conn:
                conn.executemany("INSERT INTO players(player_id,player_name) VALUES(?,?)",((i,player(i)) for i in range(1,count+1)))
                conn.executemany("INSERT INTO private_slots(player_id,slot) VALUES(?,?)",((i,f"ofp_{i:032x}") for i in range(1,count+1)))
            conn.create_function("private_name_key",1,name_key,deterministic=True)
            old_friend="""SELECT player_id FROM private_slots JOIN players USING(player_id)
                WHERE private_name_key(player_name)=? LIMIT 1"""
            old_signup="""SELECT p.player_name,s.slot FROM players p LEFT JOIN private_slots s
                ON s.player_id=p.player_id WHERE p.player_name IS NOT NULL AND p.player_name!=''"""
            def friend_old(value):
                row=conn.execute(old_friend,(name_key(value),)).fetchone()
                return row[0] if row else None
            def signup_old(value):
                desired=index.signup(value)
                return any(other!="new-slot" and index.signup(raw)==desired for raw,other in conn.execute(old_signup))
            def measure(function):
                targets=(player(count//2),player(count),"找不到的人")
                results=[function(key) for key in targets]
                ms=[]
                for i in range(rounds):
                    start=perf_counter_ns()
                    assert function(targets[i%3])==results[i%3]
                    ms.append((perf_counter_ns()-start)/1e6)
                ordered=sorted(ms)
                return results,{"p50_ms":round(statistics.median(ms),4),"p95_ms":round(ordered[math.ceil(.95*len(ordered))-1],4)}
            def plan(fn):
                lines=[]
                conn.set_trace_callback(lines.append)
                try:fn("找不到的人")
                finally:conn.set_trace_callback(None)
                return [r[3] for r in conn.execute("EXPLAIN QUERY PLAN "+lines[-1])]
            def writes():
                batch=min(1000,count)
                output={}
                for column,rows in (("level",[(15,i) for i in range(1,batch+1)]),
                                    ("player_name",[("新版"+str(i),i) for i in range(1,batch+1)])):
                    samples=[]
                    for _ in range(5):
                        conn.execute("SAVEPOINT nickname_probe")
                        start=perf_counter_ns()
                        conn.executemany(f"UPDATE players SET {column}=? WHERE player_id=?",rows)
                        samples.append((perf_counter_ns()-start)/1e6)
                        conn.execute("ROLLBACK TO nickname_probe")
                        conn.execute("RELEASE nickname_probe")
                    output[column]=round(statistics.median(samples),3)
                return output
            output={"players":count,"rounds_per_lookup":rounds,"sqlite":sqlite3.sqlite_version,"queries":{}}
            expected={}
            for kind,fn in (("friend",friend_old),("registration",signup_old)):
                expected[kind],before=measure(fn)
                output["queries"][kind]={"old":before,"old_plan":plan(fn)}
            output["writes"]={"rows_per_batch":min(1000,count),"samples_per_batch":5,"old_p50_ms":writes()}
            def allocated():
                return conn.execute("PRAGMA page_count").fetchone()[0]*conn.execute("PRAGMA page_size").fetchone()[0]
            size=allocated()
            begin=perf_counter_ns()
            index.setup(conn)
            output["backfill_ms"]=round((perf_counter_ns()-begin)/1e6,2)
            output["additional_database_bytes"]=allocated()-size
            output["writes"]["indexed_p50_ms"]=writes()
            for kind,fn in (("friend",lambda value:index.find(conn,name_digest(value),friends=True)),
                            ("registration",lambda value:index.played_by_other(conn,"new-slot",index.signup(value)))):
                result,after=measure(fn)
                assert result==expected[kind]
                output["queries"][kind].update(indexed=after,indexed_plan=plan(fn))
            return output
        finally:
            conn.close()
            db.conn=None


if __name__=="__main__":
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--server",type=Path,default=Path(__file__).resolve().parents[1]/"OverField-Server")
    cli.add_argument("--players",type=int,default=50000)
    cli.add_argument("--rounds",type=int,default=30)
    args=cli.parse_args()
    if args.players<2 or args.rounds<3:cli.error("Select at least two players and three queries")
    print(json.dumps(run(args.server.resolve(),args.players,args.rounds),indent=2))
