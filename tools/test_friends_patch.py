"""Check the portable friends release in a fresh directory, optionally testing its public TLS endpoint."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import zipfile

from package_friends_patch import (CERTIFICATE, GUIDE, INDEX, LOCAL_TEXTURES, MARKER, PRESET, TITLE,
                                  allowed_name, build, canonical_endpoint, public_ca, sha_file)


# The probe runs next to the real packaged executable so AppDomain.BaseDirectory matches a normal launch.
PROBE = r"""
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;

class FriendsProbe {
    const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    static T Field<T>(Form form, string name) {
        return (T)form.GetType().GetField(name, Private).GetValue(form);
    }
    static void Check(bool value, string explanation) {
        if(!value) throw new Exception(explanation);
    }
    [STAThread] static int Main(string[] args) {
        Application.EnableVisualStyles();
        var type=Assembly.LoadFrom(args[0]).GetType("OfSfPlayer.PlayerWindow", true);
        foreach(bool picker in new bool[]{false,true}){
            using(var form=(Form)Activator.CreateInstance(type, Private, null, new object[]{picker}, null)){
                form.ShowInTaskbar=false; form.Opacity=0; form.Show();
                var servers=Field<ComboBox>(form,"servers");
                var cert=Field<TextBox>(form,"cert");
                string expectedCa=args[2]=="-"?"":args[2];
                Check(servers.Text==args[1] && servers.Items.Contains(args[1]),"Selected realm or dropdown entry differs");
                Check(cert.Text==expectedCa,"Selected CA differs");
                foreach(string n in new string[]{"account","password","nickname"})
                    Check(Field<TextBox>(form,n).Text=="","Unexpected saved player field");
                Check(!Field<CheckBox>(form,"register").Checked,"Unexpected saved registration choice");
                if(!picker && args[3]=="tls"){
                    type.GetMethod("Run",Private).Invoke(form,new object[]{"test"});
                    var timer=Stopwatch.StartNew();
                    while(Field<bool>(form,"busy") && timer.ElapsedMilliseconds<30000) {
                        Application.DoEvents(); Thread.Sleep(20);
                    }
                    var result=Field<TextBox>(form,"output").Text;
                    Check(result.Contains("TLS 证书和区服账号接口验证通过"),"Realm TLS check failed: "+result);
                }
                servers.Text="https://other.invalid";
                Check(cert.Text=="","CA followed an unrelated realm");
                servers.Text=args[1];
                Check(cert.Text==expectedCa,"Reselecting preset lost its CA");
            }
        }
        return 0;
    }
}
"""


def verify(source, friends, connect):
    if os.name != "nt":
        raise SystemExit("Run this UI test on Windows x64")
    with TemporaryDirectory(prefix="of-friends-qa-") as work:
        temp = Path(work)
        extract = temp / "fresh portable 朋友"
        with zipfile.ZipFile(source) as generic, zipfile.ZipFile(friends) as bundled:
            names = set(bundled.namelist())
            added = {PRESET, CERTIFICATE, GUIDE}
            original = set(generic.namelist())
            if (len(names) != len(bundled.namelist()) or names != (original - LOCAL_TEXTURES) | added or
                    not all(allowed_name(name) for name in names - added)):
                raise AssertionError("Unexpected friends package members")
            for name in (names & original) - {INDEX}:
                if bundled.read(name) != generic.read(name):
                    raise AssertionError("Changed shared file: " + name)
            manifest = json.loads(bundled.read(INDEX))["files"]
            if set(manifest) != names - {MARKER, INDEX}:
                raise AssertionError("Incorrect manifest coverage")
            for name, digest in manifest.items():
                if hashlib.sha256(bundled.read(name)).hexdigest() != digest:
                    raise AssertionError("Manifest mismatch: " + name)
            bundled.extractall(extract)
        ca = extract / CERTIFICATE
        preset = json.loads((extract / PRESET).read_text(encoding="utf-8"))
        address = canonical_endpoint(preset["publicTls"])
        if preset != {"version": 1, "publicTls": address, "caFile": CERTIFICATE} or public_ca(ca) != ca.read_bytes():
            raise AssertionError("Unexpected preset or noncanonical public CA")
        rebuilt = temp / "rebuilt.zip"
        build(source, sha_file(source), address, ca, rebuilt)
        if sha_file(rebuilt) != sha_file(friends):
            raise AssertionError("Second build was not byte identical")
        prefs = extract / "player-settings.json"
        if prefs.exists():
            raise AssertionError("Personal player settings in release")
        src = temp / "Probe.cs"
        src.write_text(PROBE, encoding="utf-8-sig")
        runner = extract / "FriendsProbe.exe"
        csc = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        subprocess.run([str(csc), "/nologo", "/target:exe", "/platform:x64", "/out:" + str(runner),
                        "/r:System.Windows.Forms.dll", str(src)], check=True)
        command = [str(runner), str(extract / TITLE)]
        unrelated = temp / "different launch directory"
        unrelated.mkdir()
        subprocess.run(command + [address, str(ca), "tls" if connect else "ui"], check=True, timeout=40, cwd=unrelated)
        if prefs.exists():
            raise AssertionError("First-run display or connection test wrote player preferences")
        chosen = "https://local-choice.invalid"
        prefs.write_text(json.dumps({"publicTls": chosen, "caFile": "", "servers": [{"publicTls": chosen, "caFile": ""}]}),
                         encoding="utf-8")
        subprocess.run(command + [chosen, "-", "ui"], check=True, timeout=15, cwd=unrelated)
        print(f"PASS {len(names)} files; shared files unchanged; byte-identical rebuild; fresh WinForms launcher and picker;"
              f" account fields empty; different cwd, per-realm CA, saved choice precedence; public TLS checked={connect}")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--source", type=Path, required=True)
    cli.add_argument("--friends", type=Path, required=True)
    cli.add_argument("--check-connection", action="store_true", help="Perform the actual launcher's read-only TLS connection test")
    opt = cli.parse_args()
    verify(opt.source.resolve(), opt.friends.resolve(), opt.check_connection)
