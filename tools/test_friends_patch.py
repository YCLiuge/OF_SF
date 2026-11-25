"""Verify a distributable ZIP and exercise its real Windows player UI in a fresh profile."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import zipfile

from package_friends_patch import GUIDE, INDEX, LOCAL_TEXTURES, MARKER, TITLE, allowed_name, build, sha_file


PROBE = r"""
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

class FriendsProbe {
    const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    static T Field<T>(Form form,string name) {
        return (T)form.GetType().GetField(name,Private).GetValue(form);
    }
    static void Check(bool value,string reason) { if(!value)throw new Exception(reason); }
    static IEnumerable<Control> All(Control root) {
        yield return root;
        foreach(Control child in root.Controls)foreach(var part in All(child))yield return part;
    }
    static void Masked(Form window,string host,string ca) {
        foreach(var elem in All(window)){
            var displayed=new List<string>{elem.Text,elem.AccessibleName,elem.AccessibleDescription};
            var combo=elem as ComboBox;
            if(combo!=null)foreach(var item in combo.Items)displayed.Add(item.ToString());
            foreach(string value in displayed)if(!String.IsNullOrEmpty(value)){
                Check(value.IndexOf(host,StringComparison.OrdinalIgnoreCase)<0,"Host exposed in ordinary controls");
                if(ca!="")Check(value.IndexOf(ca,StringComparison.OrdinalIgnoreCase)<0,"CA path exposed in ordinary controls");
            }
        }
    }
    static Button Button(Form root,string title) {
        foreach(var item in All(root))if(item is Button && item.Text==title)return (Button)item;
        throw new Exception("Expected player action not found");
    }
    static void Wait(Func<bool> complete) {
        var t=Stopwatch.StartNew();
        while(!complete() && t.ElapsedMilliseconds<30000){Application.DoEvents();Thread.Sleep(15);}
        Check(complete(),"Player action did not finish");
    }
    [STAThread] static int Main(string[] args) {
        Application.EnableVisualStyles();
        var type=Assembly.LoadFrom(args[0]).GetType("OfSfPlayer.PlayerWindow",true);
        var info=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(args[1]));
        var host=new Uri((string)info["endpoint"]).Host;
        string mode=args[2];
        foreach(bool picker in new[]{false,true}){
            using(var w=(Form)Activator.CreateInstance(type,Private,null,new object[]{picker},null)){
                w.ShowInTaskbar=false; w.Opacity=0; w.Show();
                var opts=Field<ComboBox>(w,"servers");
                foreach(string n in new[]{"account","password","nickname"})Check(Field<TextBox>(w,n).Text=="","Unexpected saved player");
                if(!picker && mode!="existing"){
                    Check(opts.Items.Count==0,"New player ZIP has a preselected realm");
                    var read=type.GetMethod("ImportInvitation",Private);
                    var declined=(Task<bool>)read.Invoke(w,new object[]{args[1],new Func<string,bool>(s=>false)});
                    Wait(()=>declined.IsCompleted);Check(!declined.Result && opts.Items.Count==0,"Declined invite changed the realm");
                    var accepted=(Task<bool>)read.Invoke(w,new object[]{args[1],new Func<string,bool>(s=>s.IndexOf(host)<0)});
                    Wait(()=>accepted.IsCompleted);Check(accepted.Result,"Invite was not imported");
                }
                Check(opts.DropDownStyle==ComboBoxStyle.DropDownList && opts.Text.StartsWith("我的区服 "),"Realm selector does not show an alias");
                string ca=(string)type.GetProperty("ActiveCa",Private).GetValue(w,null);
                Check((string)type.GetProperty("ActiveAddress",Private).GetValue(w,null)==(string)info["endpoint"],"Saved connection differs");
                Masked(w,host,ca);
                if(!picker && (mode=="tls" || mode=="account")){
                    Button(w,"测试连接").PerformClick();Wait(()=>!Field<bool>(w,"busy"));
                    Check(Field<TextBox>(w,"output").Text.Contains("TLS 证书和区服账号接口验证通过"),"TLS check failed");
                    Masked(w,host,ca);
                }
                if(!picker && mode=="account"){
                    Field<TextBox>(w,"account").Text="ui_"+Guid.NewGuid().ToString("N").Substring(0,12);
                    Field<TextBox>(w,"password").Text=Guid.NewGuid().ToString("N");
                    Button(w,"仅创建账号").PerformClick();Wait(()=>!Field<bool>(w,"busy"));
                    Check(Field<TextBox>(w,"output").Text.Contains("账号已创建"),"Register without entering game failed");
                    Check(Field<TextBox>(w,"password").Text=="","Registration retained a password");
                    bool shown=false; using(var tick=new System.Windows.Forms.Timer{Interval=50}){
                        tick.Tick+=delegate{
                            foreach(Form popup in Application.OpenForms)if(popup.GetType().FullName=="OfSfImport.ImportWindow"){
                                Masked(popup,host,ca);shown=true;tick.Stop();popup.Close();break;
                            }
                        };
                        tick.Start();Button(w,"首次进游戏前导入存档").PerformClick();
                        Wait(()=>!Field<bool>(w,"busy"));Check(shown,"Backup screen did not open after first registration");
                    }
                }
                Masked(w,host,ca);
            }
        }
        return 0;
    }
}
"""


def run_probe(root, invitation, mode, work):
    src = work / "Probe.cs"
    src.write_text(PROBE, encoding="utf-8-sig")
    probe = root / "FriendsProbe.exe"
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(compiler), "/nologo", "/target:exe", "/platform:x64", "/out:" + str(probe),
                    "/r:System.Windows.Forms.dll", "/r:System.Web.Extensions.dll", str(src)], check=True)
    try:
        subprocess.run([str(probe), str(root / TITLE), str(invitation), mode], check=True, timeout=95, cwd=work)
    finally:
        probe.unlink(missing_ok=True)


def fixture(path, endpoint, pem=""):
    path.write_text(json.dumps({"format": "OF_SF-Realm-Invitation", "version": 1,
                                "endpoint": endpoint, "caPem": pem}), encoding="utf-8")
    return path


def rejected_invites(root, temp):
    command = [str(root / "OverField-Server/runtime-win64/python.exe"), "-B",
               str(root / "client_tools/realm_invitation.py"), "ui"]
    def action(**message):
        output = subprocess.check_output(command, input=json.dumps(message).encode("utf-8"))
        return json.loads(output)
    file = fixture(temp / "validation.ofinvite", "https://first.invalid")
    data = json.loads(file.read_bytes())
    digest = action(action="inspect", path=str(file))["digest"]
    fixture(file, "https://second.invalid")
    assert not action(action="import", path=str(file), digest=digest)["ok"]
    for altered in ({**data, "account": "test"}, {**data, "endpoint": "https://user@host.invalid"},
                    {**data, "caPem": "-----BEGIN PRIVATE KEY-----"}, {**data, "version": True}):
        file.write_text(json.dumps(altered), encoding="utf8")
        assert not action(action="inspect", path=str(file))["ok"]
    assert not (root / "player-settings.json").exists()


def verify(source, friends, invitation, connect):
    if os.name != "nt":
        raise SystemExit("Run WinForms tests on Windows x64")
    with TemporaryDirectory(prefix="of-friends-qa-") as work:
        temp = Path(work)
        extract = temp / "新玩家 portable"
        with zipfile.ZipFile(source) as generic, zipfile.ZipFile(friends) as bundled:
            names = set(bundled.namelist())
            original = set(generic.namelist())
            if (len(names) != len(bundled.namelist()) or names != (original - LOCAL_TEXTURES) | {GUIDE} or
                    not all(allowed_name(name) for name in names - {GUIDE})):
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
        rebuilt = temp / "rebuilt.zip"
        build(source, sha_file(source), rebuilt)
        if sha_file(rebuilt) != sha_file(friends):
            raise AssertionError("Friends ZIP was not reproducible")
        rejected_invites(extract, temp)
        # Loose legacy files beside an otherwise clean installation must not set a hidden target.
        (extract / "realm-preset.json").write_text(json.dumps({"version": 1, "publicTls": "https://unsigned.invalid",
                                                             "caFile": "realm-ca.pem"}), encoding="utf8")
        (extract / "realm-ca.pem").write_text("unindexed", encoding="ascii")
        active = invitation or fixture(temp / "synthetic.ofinvite", "https://private-choice.invalid:18443")
        run_probe(extract, active, "tls" if connect else "ui", temp)
        # An existing install's currently selected connection also stays private on both screens.
        custom = "https://old-settings.invalid:18443"
        prefs = extract / "player-settings.json"
        prefs.write_text(json.dumps({"publicTls": custom, "caFile": "", "servers": [{"publicTls": custom, "caFile": ""}]}),
                         encoding="utf-8")
        run_probe(extract, fixture(temp / "existing.ofinvite", custom), "existing", temp)
        isolated_user = temp / "private-windows-user"
        env = dict(os.environ, LOCALAPPDATA=str(isolated_user))
        response = subprocess.check_output([str(extract / "OverField-Server/runtime-win64/python.exe"), "-B",
            str(extract / "client_tools/realm_invitation.py"), "ui"], env=env,
            input=json.dumps({"action": "export", "publicTls": custom}).encode())
        exported = Path(json.loads(response)["path"])
        assert exported.parent == isolated_user / "OF_SF" / "PrivateConnections"
        assert json.loads(exported.read_bytes()) == {"format": "OF_SF-Realm-Invitation", "version": 1,
                                                     "endpoint": custom, "caPem": ""}
        prefs.unlink()
        legacy = "https://legacy-choice.invalid"
        (extract / "realm-preset.json").write_text(json.dumps({"version": 1, "publicTls": legacy,
                                                              "caFile": "realm-ca.pem"}), encoding="utf8")
        old_index = json.loads((extract / INDEX).read_bytes())
        for name in ("realm-preset.json", "realm-ca.pem"):
            old_index["files"][name] = sha_file(extract / name)
        (extract / INDEX).write_text(json.dumps(old_index), encoding="utf8")
        run_probe(extract, fixture(temp / "original-legacy.ofinvite", legacy), "existing", temp)
        print(f"PASS {len(names)} ZIP entries, unchanged shared files and byte-identical rebuild; fresh + existing WinForms"
              f" startup/picker, declined and accepted private invite, masked controls, blank player fields, TLS={connect}")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--source", type=Path, required=True)
    cli.add_argument("--friends", type=Path, required=True)
    cli.add_argument("--invitation", type=Path, help="Locally stored, privately supplied invite (never archived)")
    cli.add_argument("--check-connection", action="store_true", help="Send read-only TLS test to the privately invited endpoint")
    opt = cli.parse_args()
    if opt.check_connection and not opt.invitation:
        cli.error("--check-connection needs a private --invitation")
    verify(opt.source.resolve(), opt.friends.resolve(), opt.invitation.resolve() if opt.invitation else None,
           opt.check_connection)
