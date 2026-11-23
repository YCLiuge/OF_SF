using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

using System.Windows.Forms;


namespace OverField.PrivateLauncher {
    internal sealed class LaunchWindow : Form {
        private readonly string home;
        private readonly TextBox source = new TextBox();
        private readonly TableLayoutPanel grid = new TableLayoutPanel();
        private readonly ComboBox mode = new ComboBox();
        private readonly TextBox ssh = new TextBox();
        private readonly TextBox sshPort = new TextBox();
        private readonly TextBox profile = new TextBox();
        private readonly CheckBox pair = new CheckBox();
        private readonly Button profileBrowse = new Button();
        private readonly TableLayoutPanel remote = new TableLayoutPanel();
        private readonly TextBox address = new TextBox();
        private readonly Button browse = new Button();
        private readonly Button save = new Button();
        private readonly Button play = new Button();
        private readonly Label status = new Label();
        private readonly TextBox log = new TextBox();
        private readonly ProgressBar progress = new ProgressBar();
        private readonly ComboBox servers=new ComboBox();
        private readonly Button accounts=new Button(),bulletins=new Button(),gifts=new Button();
        private Dictionary<string,object> privateSession;
        private string sessionServer;
        private bool accountPreferred;
        private DateTime sessionUntil;
        private bool refreshing;
        private readonly bool inGame;
        private bool busy;
        private bool automatic;

        internal LaunchWindow(string directory, bool settings) : this(directory,settings,false) {}
        internal LaunchWindow(string directory, bool settings, bool picker) {
            inGame=picker;
            home = directory;
            Text = picker?"开放空间 · 选择服务器":"开放空间 · 私服启动器";
            StartPosition = FormStartPosition.CenterScreen;
            AutoScaleMode = AutoScaleMode.Dpi;
            AutoScaleDimensions = new SizeF(96,96);
            Font = new Font("Microsoft YaHei UI", 9F);
            BackColor = Color.FromArgb(248,250,249);
            ForeColor = Color.FromArgb(26,49,45);
            ClientSize = new Size(665,430);
            MinimumSize = new Size(610,456);
            try { Icon=Icon.ExtractAssociatedIcon(Application.ExecutablePath); }
            catch (IOException) { }
            grid.Dock=DockStyle.Fill;grid.ColumnCount=2;grid.RowCount=12;
            grid.Padding=new Padding(25,12,25,14);
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,96));
            foreach (int h in new [] {48,25,35,24,35,24,35,0,43,25,5})
                grid.RowStyles.Add(new RowStyle(SizeType.Absolute,h));
            grid.RowStyles.Add(new RowStyle(SizeType.Percent,100));
            Controls.Add(grid);
            var header = new Label {
                Text="选择服务器",AutoSize=true,Anchor=AnchorStyles.Left,Font=new Font(Font.FontFamily,18F,FontStyle.Bold)
            };
            var top=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=2,RowCount=1,Margin=new Padding(0)};
            top.RowStyles.Add(new RowStyle(SizeType.Percent,100));top.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,168));top.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            top.Controls.Add(header,0,0);
            servers.AccessibleName="保存的服务器地址";servers.DropDownStyle=ComboBoxStyle.DropDownList;
            servers.Anchor=AnchorStyles.Left|AnchorStyles.Right;top.Controls.Add(servers,1,0);PutRow(grid,top,0);
            PutRow(grid,Caption("国服 PC 1.1.12 客户端目录或安装介质（ISO / ZIP）"),1);
            source.Dock=DockStyle.Fill;source.Margin=new Padding(3,5,4,1);
            source.AccessibleName="客户端目录、ISO 或原版客户端 ZIP";
            grid.Controls.Add(source,0,2);
            SetupButton(browse,"浏览…",false);
            browse.Dock=DockStyle.Fill;
            grid.Controls.Add(browse,1,2);
            PutRow(grid,Caption("连接方式"),3);
            mode.DropDownStyle=ComboBoxStyle.DropDownList;mode.AccessibleName="连接方式";
            mode.Items.AddRange(new string[]{"本机自动启动区服","使用本机已运行的网关","连接远端私服（SSH）"});
            mode.Dock=DockStyle.Fill;mode.Margin=new Padding(3,5,3,1);PutRow(grid,mode,4);
            PutRow(grid,Caption("本机网关地址"),5);
            address.Dock=DockStyle.Fill;address.Margin=new Padding(3,5,3,1);
            address.AccessibleName="位于本机回环地址的网关";
            PutRow(grid,address,6);
            BuildRemote();
            PutRow(grid,remote,7);
            var actions = new FlowLayoutPanel {
                Dock=DockStyle.Fill,Margin=new Padding(0),FlowDirection=FlowDirection.RightToLeft,WrapContents=false
            };
            SetupButton(play,picker?"应用并重启":"进入游戏",true);
            SetupButton(save,"保存地址",false);
            SetupButton(accounts,"私服账号",false);SetupButton(bulletins,"区服公告",false);
            actions.Controls.Add(play);actions.Controls.Add(save);actions.Controls.Add(accounts);actions.Controls.Add(bulletins);
            SetupButton(gifts,"私服礼包",false);actions.Controls.Add(gifts);
            gifts.Click+=delegate {
                var current=SelectedKey()==sessionServer?privateSession:null;
                using(var window=new GiftWindow(home,SelectedService(),current,owner=>{
                    LoginAccount(owner);return SelectedKey()==sessionServer?privateSession:null;
                }))window.ShowDialog(this);
            };
            accounts.Click+=delegate{OpenAccount();};
            bulletins.Click+=delegate{using(var news=new MaintenanceWindow(home,SelectedService()))news.ShowDialog(this);};
            PutRow(grid,actions,8);
            status.Text="选择客户端目录和区服地址。";
            status.Dock=DockStyle.Fill;status.TextAlign=ContentAlignment.MiddleLeft;
            status.AutoEllipsis=true;status.AccessibleName="启动状态";
            PutRow(grid,status,9);
            progress.Dock=DockStyle.Fill;progress.Margin=new Padding(3,0,3,0);
            progress.Style=ProgressBarStyle.Marquee;progress.MarqueeAnimationSpeed=0;progress.Visible=false;
            PutRow(grid,progress,10);
            log.Multiline=true;log.ReadOnly=true;log.ScrollBars=ScrollBars.Vertical;
            log.WordWrap=true;log.Dock=DockStyle.Fill;log.TabStop=false;
            log.BorderStyle=BorderStyle.FixedSingle;log.BackColor=Color.White;
            log.Margin=new Padding(3,7,3,0);log.AccessibleName="运行记录";
            PutRow(grid,log,11);
            AcceptButton=play;
            var choices=new ContextMenuStrip();
            choices.Items.Add("选择客户端或安装介质所在目录…",null,delegate{BrowseSource();});
            choices.Items.Add("选择 ISO 或客户端 ZIP…",null,delegate{
                using(var dialog=new OpenFileDialog{Title="选择原版《开放空间》1.1.12 安装介质",Filter="原版安装介质 (*.iso;*.zip)|*.iso;*.zip",CheckFileExists=true,Multiselect=false}){
                    if(File.Exists(source.Text))dialog.FileName=source.Text;
                    if(dialog.ShowDialog(this)==DialogResult.OK)source.Text=dialog.FileName;
                }
            });
            browse.Click+=delegate { choices.Show(browse,0,browse.Height); };
            save.Click+=delegate { RunAction(false); };
            play.Click+=delegate { RunAction(true); };
            mode.SelectedIndexChanged+=delegate { UpdateMode(); };
            source.Text=DefaultSource(home);
            address.Text="http://127.0.0.1:18881";
            mode.SelectedIndex=0;
            ReadSettings(settings || picker);
            ReloadBook(true);
            if (mode.SelectedIndex==1 || picker)automatic=false;
            if(picker)status.Text="保存地址可供以后使用。应用并重启将返回原版游戏选服。";
            servers.SelectedIndexChanged+=delegate {
                if(refreshing || busy)return;
                var selected=servers.SelectedItem as Choice;
                if(selected!=null)ApplyChoice(selected.Values);
                else if(servers.SelectedIndex>=0) {
                    mode.SelectedIndex=2;ssh.Clear();sshPort.Clear();profile.Clear();pair.Checked=false;
                    ssh.Select();status.Text="可填写主机、user@host:端口 或 ssh:// 地址，然后登录私服账号或选择服主导出的档案。";
                }
            };
            Shown+=delegate {
                if(picker) {
                    TopMost=true;Activate();
                    var timer=new System.Windows.Forms.Timer{Interval=1600};
                    timer.Tick+=delegate { timer.Stop();timer.Dispose();if(!IsDisposed)TopMost=false; };
                    timer.Start();
                }
                if (automatic) RunAction(true);
                else { address.Select();address.SelectionStart=address.Text.Length;address.SelectionLength=0; }
            };
            FormClosing+=delegate(object sender,FormClosingEventArgs e) {
                if (busy && e.CloseReason==CloseReason.UserClosing) {
                    e.Cancel=true;
                    status.Text="正在执行请求，完成后可以关闭窗口。";
                }
            };
        }

        private sealed class Choice {
            public readonly Dictionary<string,object> Values;
            private readonly string text;
            public Choice(Dictionary<string,object> row) {
                Values=row;string target=Setting(row,"remoteSsh"),port=Setting(row,"remoteSshPort");
                object local;bool auto=row.TryGetValue("localServer",out local) && local is bool && (bool)local;
                text=target.Length>0?"SSH · "+target+(port.Length>0?"　端口 "+port:""):(auto?"本机区服 · ":"已有网关 · ")+Setting(row,"url");
            }
            public override string ToString(){return text;}
        }
        private void ApplyChoice(Dictionary<string,object> values) {
            address.Text=Setting(values,"url");
            ssh.Text=Setting(values,"remoteSsh");sshPort.Text=Setting(values,"remoteSshPort");
            profile.Text=Setting(values,"remoteProfile");
            object option;pair.Checked=values.TryGetValue("remotePairFirst",out option) && option is bool && (bool)option;
            bool managed=values.TryGetValue("localServer",out option) && option is bool && (bool)option;
            mode.SelectedIndex=ssh.Text.Length>0?2:(managed?0:1);
        }
        private void ReloadBook(bool usePreferred) {
            refreshing=true;
            try {
                servers.Items.Clear();
                var path=Path.Combine(home,"server-addresses.json");
                if(File.Exists(path)) {
                    var serializer=new LauncherJson();
                    var data=serializer.Deserialize<List<Dictionary<string,object>>>(File.ReadAllText(path,Encoding.UTF8));
                    if(data!=null)foreach(var item in data) {
                        if(item==null || String.IsNullOrWhiteSpace(Setting(item,"url")))continue;
                        servers.Items.Add(new Choice(item));
                    }
                }
                if(servers.Items.Count>0) {
                    servers.SelectedIndex=0;
                    if(usePreferred)ApplyChoice(((Choice)servers.Items[0]).Values);
                } else {
                    var current=new Dictionary<string,object>{{"url",address.Text},{"localServer",mode.SelectedIndex==0}};
                    if(mode.SelectedIndex==2) {
                        current["remoteSsh"]=ssh.Text;current["remoteSshPort"]=sshPort.Text;
                        current["remoteProfile"]=profile.Text;current["remotePairFirst"]=pair.Checked;
                    }
                    servers.Items.Add(new Choice(current));servers.SelectedIndex=0;
                }
                servers.Items.Add("添加服务器地址…");
            } catch(Exception error) { Append("无法读取已保存的地址。"+error.Message); }
            finally {refreshing=false;}
        }
        private void BuildRemote() {
            remote.Dock=DockStyle.Fill;remote.Margin=new Padding(0);remote.ColumnCount=4;remote.RowCount=4;
            remote.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,76));
            remote.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            remote.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,84));
            remote.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,82));
            foreach(int height in new[]{34,34,29,25}) remote.RowStyles.Add(new RowStyle(SizeType.Absolute,height));
            var serverLabel=Caption("服务器地址");serverLabel.TextAlign=ContentAlignment.MiddleLeft;
            var portLabel=Caption("SSH 端口");portLabel.TextAlign=ContentAlignment.MiddleRight;
            var fileLabel=Caption("私服档案");fileLabel.TextAlign=ContentAlignment.MiddleLeft;
            ssh.AccessibleName="服务器 IP、主机别名、user@host:端口 或 ssh://user@host:端口";ssh.Dock=DockStyle.Fill;
            sshPort.AccessibleName="可选 SSH 端口";sshPort.Dock=DockStyle.Fill;
            profile.AccessibleName="从远端私服导出的个人档案 JSON";profile.Dock=DockStyle.Fill;
            remote.Controls.Add(serverLabel,0,0);remote.Controls.Add(ssh,1,0);
            remote.Controls.Add(portLabel,2,0);remote.Controls.Add(sshPort,3,0);
            remote.Controls.Add(fileLabel,0,1);remote.Controls.Add(profile,1,1);remote.SetColumnSpan(profile,2);
            SetupButton(profileBrowse,"浏览…",false);profileBrowse.Dock=DockStyle.Fill;profileBrowse.Margin=new Padding(3,1,3,4);
            remote.Controls.Add(profileBrowse,3,1);
            profileBrowse.Click+=delegate {
                using(var dialog=new OpenFileDialog{Title="选择此远端私服导出的个人档案",Filter="私服个人档案 (*.json)|*.json",CheckFileExists=true,Multiselect=false}){
                    if(File.Exists(profile.Text))dialog.FileName=profile.Text;
                    if(dialog.ShowDialog(this)==DialogResult.OK)profile.Text=dialog.FileName;
                }
            };
            pair.Text="首次将通过本机验证的玩家与此远端个人档案配对";pair.AutoSize=true;pair.Anchor=AnchorStyles.Left;
            remote.Controls.Add(pair,0,2);remote.SetColumnSpan(pair,4);
            var note=Caption("例如 user@192.168.1.8:2222。使用已信任的 SSH 主机。私服账号可直接登录，个人档案可选。");
            note.TextAlign=ContentAlignment.MiddleLeft;remote.Controls.Add(note,0,3);remote.SetColumnSpan(note,4);
        }
        private void UpdateMode() {
            bool enabled=mode.SelectedIndex==2;
            remote.Visible=enabled;remote.Enabled=!busy && enabled;
            grid.RowStyles[7].Height=enabled?126:0;
            var minimum=SizeFromClientSize(new Size(600,enabled?540:408));
            MinimumSize=minimum;
            int height=enabled?562:430;
            if(ClientSize.Height!=height)ClientSize=new Size(ClientSize.Width,height);
        }
        private static string Setting(Dictionary<string,object> data,string key) {
            object val;return data.TryGetValue(key,out val)&&val!=null?Convert.ToString(val):String.Empty;
        }
        private static Label Caption(string text) {
            return new Label { Text=text,Dock=DockStyle.Fill,TextAlign=ContentAlignment.BottomLeft };
        }
        private static void PutRow(TableLayoutPanel grid,Control control,int row) {
            grid.Controls.Add(control,0,row);grid.SetColumnSpan(control,2);
        }
        private static void SetupButton(Button b,string text,bool primary) {
            b.Text=text;b.AutoSize=false;b.Size=new Size(102,32);b.UseVisualStyleBackColor=false;
            b.Margin=new Padding(3,4,3,4);b.FlatStyle=FlatStyle.Flat;
            b.FlatAppearance.BorderColor=primary?Color.FromArgb(19,82,67):Color.FromArgb(162,185,177);
            b.BackColor=primary?Color.FromArgb(19,82,67):Color.White;
            b.ForeColor=primary?Color.White:Color.FromArgb(25,58,49);
        }
        private const string IsoName="OverField-1.1.12-LocalInstaller-Win64.iso";
        private const string ZipName="OverField-TapTap-Client-Archive-2026-10-21.zip";
        private static string DefaultSource(string location) {
            foreach (var drive in DriveInfo.GetDrives()) {
                if (drive.DriveType != DriveType.Fixed) continue;
                string retail = Path.Combine(drive.Name, @"TapTap\PC Games\176228");
                if (File.Exists(Path.Combine(retail, "launcher.exe"))) return retail;
            }
            var folder=new DirectoryInfo(location);
            for(int level=0;level<2&&folder!=null;level++,folder=folder.Parent)
                foreach(var name in new[]{IsoName,ZipName}) {
                    var path=Path.Combine(folder.FullName,name);
                    if(File.Exists(path))return path;
                }
            return String.Empty;
        }
        private void BrowseSource() {
            using(var dialog=new FolderBrowserDialog {
                Description="选择含 launcher.exe 的原版目录，或含原版 ISO / ZIP 的目录",ShowNewFolderButton=false
            }) {
                if (Directory.Exists(source.Text)) dialog.SelectedPath=source.Text;
                if (dialog.ShowDialog(this)==DialogResult.OK) source.Text=dialog.SelectedPath;
            }
        }
        private void ReadSettings(bool forceSettings) {
            string path=Path.Combine(home,"client-config.json");
            if (!File.Exists(path)) return;
            try {
                var serializer=new LauncherJson();
                var values=serializer.Deserialize<Dictionary<string,object>>(File.ReadAllText(path,Encoding.UTF8));
                object orig,endpoint,runLocal;
                if (values==null || !values.TryGetValue("source",out orig) || !(orig is string) ||
                    !values.TryGetValue("url",out endpoint) || !(endpoint is string) ||
                    String.IsNullOrWhiteSpace((string)orig) || String.IsNullOrWhiteSpace((string)endpoint))
                    throw new InvalidDataException("配置中缺少客户端目录或私服地址。");
                source.Text=(string)orig;address.Text=(string)endpoint;
                ssh.Text=Setting(values,"remoteSsh");profile.Text=Setting(values,"remoteProfile");
                sshPort.Text=Setting(values,"remoteSshPort");
                object savedPair;pair.Checked=values.TryGetValue("remotePairFirst",out savedPair) && savedPair is bool && (bool)savedPair;
                mode.SelectedIndex=ssh.Text.Length>0?2:(!values.TryGetValue("localServer",out runLocal) || !(runLocal is bool) || (bool)runLocal ? 0 : 1);
                Uri configured;
                if (!Uri.TryCreate(address.Text,UriKind.Absolute,out configured) || !configured.IsLoopback) {
                    mode.SelectedIndex=2;address.Text="http://127.0.0.1:18881";
                    status.Text="请选择远端私服的 SSH 目标。";return;
                }
                object preference;
                accountPreferred=values.TryGetValue("privateAccountLogin",out preference) && preference is bool && (bool)preference;
                automatic=!forceSettings && (accountPreferred || !(mode.SelectedIndex==2 && profile.Text.Trim().Length==0));
                status.Text=!forceSettings&&mode.SelectedIndex==2&&profile.Text.Trim().Length==0 ? "请先登录所选远端私服的账号。" : forceSettings ? "可以保存新设置，或直接进入游戏。" : "正在使用已保存的区服设置…";
            } catch (Exception e) {
                status.Text="未能读取已保存的设置。";
                Append(e.Message);
            }
        }
        private Dictionary<string,object> SelectedService(){
            var config=new Dictionary<string,object>{{"url",address.Text.Trim()},{"localServer",mode.SelectedIndex==0},{"source",source.Text.Trim()}};
            if(mode.SelectedIndex==2){
                config["viaSsh"]=true;config["ssh"]=ssh.Text.Trim();if(sshPort.Text.Trim().Length!=0)config["sshPort"]=sshPort.Text.Trim();
            }
            return config;
        }
        private string SelectedKey(){return new LauncherJson().Serialize(SelectedService());}
        private void ForgetAccount(){
            if(privateSession!=null)privateSession.Clear();
            privateSession=null;sessionServer=null;accounts.Text="私服账号";
        }
        private void OpenAccount(){
            if(privateSession!=null || accountPreferred){
                var choices=new ContextMenuStrip();
                choices.Items.Add(privateSession==null?"登录私服账号":"切换私服账号",null,delegate{LoginAccount();});
                choices.Items.Add("改用原有角色",null,delegate{
                    ForgetAccount();accountPreferred=false;status.Text="已选择原有角色。点击「进入游戏」应用。";
                });
                choices.Show(accounts,0,accounts.Height);return;
            }
            LoginAccount();
        }
        private void LoginAccount(IWin32Window owner=null){
            using(var form=new PrivateAccountWindow(home,SelectedService(),profile.Text)){
                if(form.ShowDialog(owner??this)!=DialogResult.OK || form.Session==null)return;
                ForgetAccount();privateSession=form.Session;accountPreferred=true;sessionServer=SelectedKey();
                if(Int32.TryParse(PrivateService.Text(privateSession,"gift_expires_in"),out int giftTime))
                    privateSession["gift_until_ticks"]=DateTime.UtcNow.AddSeconds(Math.Clamp(giftTime,0,3600)).Ticks;
                int seconds;Int32.TryParse(PrivateService.Text(privateSession,"expires_in"),out seconds);
                sessionUntil=DateTime.UtcNow.AddSeconds(Math.Max(0,Math.Min(86400,seconds)));
                var display=PrivateService.Text(privateSession,"nickname");accounts.Text="私服账号 ✓";if(String.IsNullOrEmpty(display))display="首次进入时起名";
                status.Text=mode.SelectedIndex!=1?"已登录私服角色「"+display+"」。进入游戏时仍需本机渠道验证。"
                    :"已登录所选私服角色「"+display+"」。切换到本机自动或 SSH 区服模式可进入原版游戏。";
            }
        }
        private void SetBusy(bool active) {
            busy=active;
            source.ReadOnly=active;address.ReadOnly=active;
            foreach (var control in new Button[]{browse,save,play,accounts,bulletins,gifts}) control.Enabled=!active;
            servers.Enabled=!active;mode.Enabled=!active;remote.Enabled=!active && mode.SelectedIndex==2;
            progress.Visible=active;
            progress.MarqueeAnimationSpeed=active?35:0;
            UseWaitCursor=active;
        }
        private void Append(string line) {
            if (String.IsNullOrWhiteSpace(line)) return;
            if (log.TextLength>32000) log.Text=log.Text.Substring(log.TextLength-16000);
            log.AppendText(line+Environment.NewLine);
        }
        private ProcessStartInfo Command(string verb,string orig,string url,int selectedMode,bool privatePlay) {
            string python=Path.Combine(home,@"OverField-Server\runtime-win64\python.exe");
            string script=Path.Combine(home,@"client_tools\native_launch.py");
            if (!File.Exists(python) || !File.Exists(script))
                throw new FileNotFoundException("套件文件不完整，请从解压后的套件根目录打开启动器。");
            var args=new List<string> {"-B",script,verb,"--home",home,"--source",orig,"--endpoint",url};
            if(selectedMode==0) args.Add("--local-server");
            if(privatePlay)args.Add("--account-stdin");
            if(selectedMode==2) {
                string target=ssh.Text.Trim(), port=sshPort.Text.Trim(), document=profile.Text.Trim();
                if(String.IsNullOrWhiteSpace(target) || target.Length>330 || Regex.IsMatch(target,@"\s"))
                    throw new ArgumentException("请输入服务器 IP、SSH 别名或 user@host:端口。");
                if(!privatePlay && document.Length!=0 && (!Path.IsPathRooted(document) || !File.Exists(document) ||
                    !String.Equals(Path.GetExtension(document),".json",StringComparison.OrdinalIgnoreCase)))
                    throw new ArgumentException("请选择此远端私服导出的 JSON 个人档案。");
                if(!privatePlay && document.Length==0 && (verb=="launch" || verb=="relaunch"))
                    throw new ArgumentException("请先点「私服账号」登录该远端区服，或选择服主导出的个人档案。");
                args.Add("--remote-ssh");args.Add(target);
                if(!privatePlay && document.Length!=0){args.Add("--remote-profile");args.Add(Path.GetFullPath(document));}
                if(port.Length!=0) {
                    int number;
                    if(!Int32.TryParse(port,out number) || number<1 || number>65535)
                        throw new ArgumentException("SSH 端口必须介于 1 和 65535，也可以留空使用 SSH 配置。");
                    args.Add("--remote-ssh-port");args.Add(number.ToString());
                }
                if(!privatePlay && document.Length!=0 && pair.Checked)args.Add("--remote-pair-first");
            }
            var cmd=new ProcessStartInfo(python) {
                WorkingDirectory=home,CreateNoWindow=true,UseShellExecute=false,
                RedirectStandardOutput=true,RedirectStandardError=true,RedirectStandardInput=privatePlay,
                StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8
            };
            foreach (string argument in args) cmd.ArgumentList.Add(argument);
            cmd.EnvironmentVariables["PYTHONIOENCODING"]="utf-8";
            cmd.EnvironmentVariables["PYTHONUTF8"]="1";
            cmd.EnvironmentVariables["PYTHONUNBUFFERED"]="1";
            return cmd;
        }
        private void RunAction(bool launch) {
            if (busy) return;
            try {
                var orig=Path.GetFullPath(source.Text.Trim());
                var url=address.Text.Trim();
                if(File.Exists(orig) && !String.Equals(Path.GetFileName(orig),IsoName,StringComparison.OrdinalIgnoreCase) &&
                   !String.Equals(Path.GetFileName(orig),ZipName,StringComparison.OrdinalIgnoreCase))
                    throw new ArgumentException("请选择对应版本的原版 ISO、客户端 ZIP 或安装目录。");
                Uri uri;
                if (!Uri.TryCreate(url,UriKind.Absolute,out uri) || !(uri.Scheme==Uri.UriSchemeHttp || uri.Scheme==Uri.UriSchemeHttps) ||
                    !uri.IsLoopback || String.IsNullOrEmpty(uri.Host) || !String.IsNullOrEmpty(uri.UserInfo) || uri.AbsolutePath!="/" ||
                    url.IndexOfAny(new char[]{'\\','?','#','@','\r','\n','\t',' '})!=-1)
                    throw new ArgumentException("请输入本机回环网关根地址，例如 http://127.0.0.1:18881。");
                if(launch && accountPreferred && privateSession==null){
                    LoginAccount();
                    if(privateSession==null){status.Text="请登录私服账号，或在「私服账号」菜单选择「改用原有角色」。";return;}
                }
                bool privatePlay=launch && privateSession!=null;
                if(privatePlay && (sessionServer!=SelectedKey() || DateTime.UtcNow>=sessionUntil)){
                    ForgetAccount();throw new ArgumentException("服务器已更改或私服会话已过期，请重新登录私服账号。");
                }
                if(privatePlay&&mode.SelectedIndex==1)
                    throw new ArgumentException("所选本机现有网关由外部管理。用此账号进图请选择本机自动或 SSH 区服。");
                string ticket=privatePlay?PrivateService.Text(privateSession,"ticket"):null;
                string slot=privatePlay && mode.SelectedIndex==2?PrivateService.Text(privateSession,"slot"):null;
                var cmd=Command(launch?(inGame?"relaunch":"launch"):(inGame?"bookmark":"prepare"),orig,url,mode.SelectedIndex,privatePlay);
                SetBusy(true);log.Clear();
                status.Text=launch?(inGame?"正在正常关闭当前私服游戏，再打开新的区服选择…":"正在准备并启动原版客户端…"):"正在保存服务器地址…";
                Task.Run(delegate {
                    using(var proc=new Process()) {
                        proc.StartInfo=cmd;
                        DataReceivedEventHandler handler=delegate(object sender,DataReceivedEventArgs ev) {
                            if (ev.Data!=null && !IsDisposed && IsHandleCreated) {
                                string line=ev.Data;
                                BeginInvoke((Action)delegate { Append(line); });
                            }
                        };
                        proc.OutputDataReceived+=handler;proc.ErrorDataReceived+=handler;
                        proc.Start();
                        if(privatePlay) {
                            var proof=new Dictionary<string,string>{{"ticket",ticket}};
                            if(slot!=null)proof["slot"]=slot;
                            byte[] bytes=Encoding.UTF8.GetBytes(new LauncherJson().Serialize(proof));proof.Clear();
                            try{proc.StandardInput.BaseStream.Write(bytes,0,bytes.Length);proc.StandardInput.BaseStream.Flush();}
                            finally{Array.Clear(bytes,0,bytes.Length);proc.StandardInput.Close();ticket=null;}
                        }
                        proc.BeginOutputReadLine();proc.BeginErrorReadLine();proc.WaitForExit();
                        return proc.ExitCode;
                    }
                }).ContinueWith(done => {
                    if (IsDisposed || !IsHandleCreated) return;
                    BeginInvoke((Action)delegate {
                        SetBusy(false);
                        if (done.IsFaulted) {
                            status.Text=launch?"原版客户端未能启动。":"设置未能保存。";
                            Append(done.Exception.GetBaseException().Message);
                        } else if (done.Result!=0) {
                            status.Text=String.Format("请求未完成（退出码 {0}），请查看运行记录。",done.Result);
                        } else if (launch) {
                            status.Text="原版客户端已启动。";Close();
                        } else {
                            ReloadBook(true);
                            status.Text=inGame?"设置已保存。可应用并重启，或关闭本窗口返回游戏。":"设置已保存。下次双击启动器将启动原版客户端。";
                        }
                    });
                });
            } catch(Exception e) {
                SetBusy(false);
                status.Text=launch?"原版客户端未能启动。":"设置未能保存。";
                Append(e.Message);
            }
        }
    }

    internal static class Program {
        [STAThread]
        private static void Main(string[] args) {
            ApplicationConfiguration.Initialize();
            bool settings=Array.Exists(args,a=>String.Equals(a,"--settings",StringComparison.OrdinalIgnoreCase));
            string home=Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory);
            bool picker=Array.Exists(args,a=>String.Equals(a,"--realm-picker",StringComparison.OrdinalIgnoreCase));
            Application.Run(new LaunchWindow(home,settings,picker));
        }
    }
}

