using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

using System.Windows.Forms;

namespace OverField.PrivateLauncher {
    using Data=Dictionary<string,object>;

    internal static class PrivateService {
        internal static string Text(Data data,string key) {
            object value;return data!=null && data.TryGetValue(key,out value) && value!=null?Convert.ToString(value):"";
        }
        internal static bool Okay(Data data) {
            object value;return data!=null && data.TryGetValue("ok",out value) && value is bool && (bool)value;
        }
        internal static string ServerName(Data server) {
            string remote=Text(server,"ssh"),port=Text(server,"sshPort");
            if(remote==""&&server.ContainsKey("viaSsh"))return "SSH · 尚未填写服务器";
            return remote==""?Text(server,"url"):"SSH · "+remote+(port!=""?"（端口 "+port+"）":"");
        }
        internal static string Error(Data data) {
            switch(Text(data,"code")){
                case "nickname_taken":return "这个游戏名已有人使用，请换一个再试。";
                case "username_taken":return "这个账号名已被使用，可以登录或换一个账号名。";
                case "invalid_credentials":return "账号或密码不正确。";
                case "rate_limited":return "尝试次数过多，请稍后再试。";
                case "registration_closed":return "服主已关闭新账号注册。";
                case "invalid_profile":case "invalid_proof":return "档案所有权验证失败，请选择此私服导出的档案。";
                case "profile_already_bound":case "already_bound":return "这个角色已绑定私服账号。";
                case "invalid_nickname":return "游戏名须为 1 到 16 个可显示字符。";
                case "invalid_username":return "账号须为 3 到 40 位，可用英文字母、数字、点、下划线和短横线。";
                case "invalid_password":return "密码须为 8 到 128 个字符。";
                case "release_changed":return "区服发布已变更，请刷新版本后再保存。";
                case "untrusted_release":return "该版本没有提供同一私服下可校验的更新文件。";
                case "need_new_file":return "请选择新的保存路径。";
                case "release_integrity":return "下载内容与发布的文件大小或 SHA-256 不符。";
                case "maintenance_unavailable":return "该私服目前没有提供公告与版本信息。";
                case "transport":return Text(data,"message");
                default:return "请求未完成，请检查输入和私服端版本。";
            }
        }
        internal static Task<Data> Request(string home,Data server,string action,Data values) {
            var form=new Data(values);form["server"]=new Data(server);form["action"]=action;
            return Task.Run(delegate {
                string python=Path.Combine(home,@"OverField-Server\runtime-win64\python.exe");
                string script=Path.Combine(home,@"client_tools\private_account_api.py");
                var command=new ProcessStartInfo(python){WorkingDirectory=home,UseShellExecute=false,
                    CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true,
                    StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
                command.ArgumentList.Add("-B");command.ArgumentList.Add(script);
                command.EnvironmentVariables["PYTHONIOENCODING"]="utf-8";command.EnvironmentVariables["PYTHONUTF8"]="1";
                var serializer=new LauncherJson{MaxJsonLength=action=="notice-image"?6*1024*1024:1048576};
                using(var child=Process.Start(command)){
                    var result=child.StandardOutput.ReadToEndAsync();var error=child.StandardError.ReadToEndAsync();
                    byte[] raw=Encoding.UTF8.GetBytes(serializer.Serialize(form));
                    try {child.StandardInput.BaseStream.Write(raw,0,raw.Length);child.StandardInput.BaseStream.Flush();}
                    finally {Array.Clear(raw,0,raw.Length);form.Clear();child.StandardInput.Close();}
                    child.WaitForExit();Task.WaitAll(result,error);
                    if(child.ExitCode!=0)throw new IOException("私服连接工具未能完成请求。");
                    return serializer.Deserialize<Data>(result.Result);
                }
            });
        }
        internal static void Finish(Form owner,Task<Data> work,Action<Data> done) {
            work.ContinueWith(task=>{
                if(owner.IsDisposed||!owner.IsHandleCreated)return;
                owner.BeginInvoke((Action)delegate {
                    if(owner.IsDisposed)return;
                    if(task.IsFaulted) done(new Data{{"ok",false},{"code","transport"},{"message","私服连接失败，请检查服务和连接设置。"}});
                    else done(task.Result);
                });
            });
        }
        internal static Label Label(string text) {
            return new Label{Text=text,Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleLeft};
        }
        internal static Button Button(string label) {
            return new Button{Text=label,AutoSize=true,Height=32,FlatStyle=FlatStyle.System};
        }
    }

    internal sealed class PrivateAccountWindow:Form {
        readonly string home;readonly Data server;
        readonly ComboBox action=new ComboBox();
        readonly TextBox user=new TextBox(),password=new TextBox(),nickname=new TextBox(),profile=new TextBox();
        readonly Button browse=PrivateService.Button("选择档案…"),submit=PrivateService.Button("登录");
        readonly Label status=PrivateService.Label(""),nickLabel=PrivateService.Label("游戏名"),proofLabel=PrivateService.Label("角色档案");
        readonly TableLayoutPanel grid=new TableLayoutPanel();bool working;
        internal Data Session {get;private set;}
        internal PrivateAccountWindow(string directory,Data destination,string initialProfile) {
            home=directory;server=destination;
            Text="开放空间 · 私服账号";Font=new Font("Microsoft YaHei UI",9);
            AutoScaleMode=AutoScaleMode.Dpi;AutoScaleDimensions=new SizeF(96,96);StartPosition=FormStartPosition.CenterParent;
            ClientSize=new Size(540,376);MinimumSize=new Size(500,364);
            grid.Dock=DockStyle.Fill;grid.Padding=new Padding(18,12,18,10);grid.ColumnCount=3;grid.RowCount=10;
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,74));grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,90));
            foreach(int h in new[]{39,32,34,34,34,34,38,29,34})grid.RowStyles.Add(new RowStyle(SizeType.Absolute,h));
            grid.RowStyles.Add(new RowStyle(SizeType.Percent,100));Controls.Add(grid);
            var title=PrivateService.Label("私服账号");title.Font=new Font(Font.FontFamily,17,FontStyle.Bold);Row(title,0);
            Row(PrivateService.Label("当前区服　"+PrivateService.ServerName(server)),1);
            grid.Controls.Add(PrivateService.Label("操作"),0,2);action.Items.AddRange(new string[]{"登录","注册新账号","绑定已有角色"});
            action.DropDownStyle=ComboBoxStyle.DropDownList;action.Dock=DockStyle.Fill;grid.Controls.Add(action,1,2);grid.SetColumnSpan(action,2);
            Field("私服账号",user,3);Field("私服密码",password,4);password.UseSystemPasswordChar=true;
            grid.Controls.Add(nickLabel,0,5);nickname.Dock=DockStyle.Fill;nickname.AccessibleName="唯一游戏名";
            grid.Controls.Add(nickname,1,5);grid.SetColumnSpan(nickname,2);
            grid.Controls.Add(proofLabel,0,6);profile.Dock=DockStyle.Fill;profile.Text=initialProfile;profile.AccessibleName="已有角色的私服所有权凭证";
            grid.Controls.Add(profile,1,6);grid.Controls.Add(browse,2,6);
            browse.Click+=delegate{using(var dlg=new OpenFileDialog{Filter="私服角色档案 (*.json)|*.json",CheckFileExists=true,Title="选择从本私服导出的原角色档案"}){
                if(File.Exists(profile.Text))dlg.FileName=profile.Text;if(dlg.ShowDialog(this)==DialogResult.OK)profile.Text=dlg.FileName;}};
            var hint=PrivateService.Label("国服原版客户端进入游戏时仍会完成渠道验证。");Row(hint,7);
            grid.Controls.Add(submit,1,8);submit.Anchor=AnchorStyles.Right;submit.Click+=delegate{Send();};AcceptButton=submit;
            status.AutoEllipsis=false;Row(status,9);
            action.SelectedIndexChanged+=delegate{Mode();};action.SelectedIndex=0;
            FormClosing+=delegate(object sender,FormClosingEventArgs e){if(working)e.Cancel=true;};
        }
        void Row(Control ctl,int row){grid.Controls.Add(ctl,0,row);grid.SetColumnSpan(ctl,3);}
        void Field(string label,TextBox box,int row) {
            grid.Controls.Add(PrivateService.Label(label),0,row);box.AccessibleName=label;box.Dock=DockStyle.Fill;
            grid.Controls.Add(box,1,row);grid.SetColumnSpan(box,2);
        }
        void Mode() {
            bool register=action.SelectedIndex!=0,bind=action.SelectedIndex==2;
            nickname.Visible=nickLabel.Visible=register;profile.Visible=browse.Visible=proofLabel.Visible=bind;
            grid.RowStyles[5].Height=register?34:0;grid.RowStyles[6].Height=bind?38:0;
            nickLabel.Text=bind?"游戏名":"游戏名（可选）";
            submit.Text=bind?"验证并绑定":register?"注册":"登录";
            status.Text=bind?"填原游戏名可保留它，或填写新的唯一游戏名。":register?"游戏名留空将在原版首次登录时创建。":"请输入本私服的账号与密码。";
        }
        void EnableForm(bool enable) {
            working=!enable;action.Enabled=user.Enabled=password.Enabled=nickname.Enabled=profile.Enabled=browse.Enabled=submit.Enabled=enable;
            UseWaitCursor=!enable;
        }
        void Send() {
            if(working)return;
            bool reg=action.SelectedIndex!=0,bind=action.SelectedIndex==2;
            string account=user.Text.Trim(),name=nickname.Text.Trim();
            if(!Regex.IsMatch(account,@"^[A-Za-z0-9_.-]{3,40}$")){status.Text="账号须为 3–40 位英文、数字、点、下划线或短横线。";user.Focus();return;}
            if(password.Text.Length==0 || (reg && (password.Text.Length<8 || password.Text.Length>128))){
                status.Text="请输入私服密码；注册密码须为 8–128 个字符。";password.Focus();return;}
            if(reg && ((bind && String.IsNullOrWhiteSpace(name)) || StringInfo.ParseCombiningCharacters(name).Length>16)){
                status.Text="游戏名须为 1 到 16 个可显示字符。";nickname.Focus();return;}
            if(bind && !File.Exists(profile.Text)){status.Text="请先选择自己在该私服的角色档案。";return;}
            var form=new Data{{"username",account},{"password",password.Text}};
            if(reg)form["nickname"]=name;if(bind)form["profile"]=Path.GetFullPath(profile.Text);
            EnableForm(false);status.Text="正在联系选定的私服…";
            PrivateService.Finish(this,PrivateService.Request(home,server,bind?"bind":reg?"register":"login",form),delegate(Data result){
                EnableForm(true);
                if(!PrivateService.Okay(result)){
                    status.Text=PrivateService.Error(result);string reason=PrivateService.Text(result,"code");
                    if(bind&&reason=="invalid_credentials")status.Text="角色档案所有权验证失败，请选择本私服导出的个人档案。";
                    if(reason=="nickname_taken"){nickname.Focus();nickname.SelectAll();}
                    if(reason=="username_taken"){user.Focus();user.SelectAll();}
                    return;
                }
                Session=result;password.Clear();DialogResult=DialogResult.OK;Close();
            });
            form.Clear();
        }
    }
    internal sealed class MaintenanceWindow:Form {
        readonly string home;readonly Data server;
        readonly Button refresh=PrivateService.Button("刷新"),download=PrivateService.Button("保存更新包…");
        readonly ListBox titles=new ListBox { Dock=DockStyle.Fill, IntegralHeight=false, AccessibleName="公告列表" };
        readonly FlowLayoutPanel detail=new FlowLayoutPanel { Dock=DockStyle.Fill, AutoScroll=true, WrapContents=false,
            FlowDirection=FlowDirection.TopDown, Padding=new Padding(12), BackColor=SystemColors.Window };
        readonly Label status=PrivateService.Label("");
        readonly List<Data> entries=new List<Data>();
        readonly System.Threading.SemaphoreSlim fetchSlot=new System.Threading.SemaphoreSlim(1,1);
        int selection;
        Data release;
        internal MaintenanceWindow(string location,Data address) {
            home=location;server=address;Text="开放空间 · 区服公告";Font=new Font("Microsoft YaHei UI",9);
            StartPosition=FormStartPosition.CenterParent;ClientSize=new Size(760,490);MinimumSize=new Size(540,340);
            var panel=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=3,RowCount=3,Padding=new Padding(15)};
            panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));panel.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,100));panel.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,76));
            panel.RowStyles.Add(new RowStyle(SizeType.Absolute,40));panel.RowStyles.Add(new RowStyle(SizeType.Percent,100));panel.RowStyles.Add(new RowStyle(SizeType.Absolute,30));Controls.Add(panel);
            panel.Controls.Add(PrivateService.Label("当前区服　"+PrivateService.ServerName(server)),0,0);panel.Controls.Add(download,1,0);panel.Controls.Add(refresh,2,0);download.Enabled=false;download.Click+=delegate{Download();};
            var split=new SplitContainer{Dock=DockStyle.Fill,Size=new Size(680,360),SplitterDistance=184};
            split.Panel1.Controls.Add(titles);split.Panel2.Controls.Add(detail);panel.Controls.Add(split,0,1);panel.SetColumnSpan(split,3);
            panel.Controls.Add(status,0,2);panel.SetColumnSpan(status,3);
            titles.SelectedIndexChanged+=delegate{ShowEntry();};detail.ClientSizeChanged+=delegate{FitContent();};
            refresh.Click+=delegate{Read();};Shown+=delegate{Read();};
        }
        void ClearDetail() {
            while(detail.Controls.Count>0) {
                var control=detail.Controls[0];detail.Controls.Remove(control);control.Dispose();
            }
        }
        void FitContent() {
            int width=Math.Max(80,detail.ClientSize.Width-detail.Padding.Horizontal-SystemInformation.VerticalScrollBarWidth-8);
            foreach(Control control in detail.Controls) {
                var picture=control as PictureBox;
                if(picture!=null && picture.Image!=null) {
                    int w=Math.Min(width,picture.Image.Width);
                    picture.Size=new Size(w,Math.Max(1,(int)((long)picture.Image.Height*w/picture.Image.Width)));
                } else if(control is Label) control.MaximumSize=new Size(width,0);
            }
        }
        void AddText(string value,bool title=false) {
            var text=new Label{Text=value,AutoSize=true,UseMnemonic=false,Margin=new Padding(0,0,0,12)};
            if(title){
                var headingFont=new Font(Font,FontStyle.Bold);text.Font=headingFont;
                text.Disposed+=delegate{headingFont.Dispose();};
            }
            detail.Controls.Add(text);FitContent();
        }
        async Task<Data> FetchImage(int generation,Data image) {
            await fetchSlot.WaitAsync();
            try {
                if(IsDisposed||generation!=selection)return null;
                return await PrivateService.Request(home,server,"notice-image",new Data{{"sha256",PrivateService.Text(image,"sha256")}});
            } finally {fetchSlot.Release();}
        }
        async void ShowEntry() {
            int generation=++selection;
            ClearDetail();
            int index=titles.SelectedIndex;
            if(index<0||index>=entries.Count)return;
            var entry=entries[index];
            AddText(titles.Items[index].ToString(),true);
            if(entry==release) {
                AddText(PrivateService.Text(entry,"notes"));AddText("SHA-256　"+PrivateService.Text(entry,"sha256"));return;
            }
            AddText(PrivateService.Text(entry,"content"));
            object raw;
            if(!entry.TryGetValue("images",out raw)||!(raw is IEnumerable))return;
            foreach(var item in (IEnumerable)raw) {
                var image=item as Data;
                if(image==null)continue;
                try {
                    var response=await FetchImage(generation,image);
                    if(IsDisposed||generation!=selection)return;
                    if(!PrivateService.Okay(response)){AddText("公告图片暂时无法载入。");continue;}
                    using(var stream=new MemoryStream(Convert.FromBase64String(PrivateService.Text(response,"image"))))
                    using(var source=Image.FromStream(stream,false,true)) {
                        double scale=Math.Min(1,1024.0/Math.Max(source.Width,source.Height));
                        var preview=new Bitmap(source,new Size(Math.Max(1,(int)(source.Width*scale)),Math.Max(1,(int)(source.Height*scale))));
                        var picture=new PictureBox{Image=preview,SizeMode=PictureBoxSizeMode.Zoom,AccessibleName="公告配图",Margin=new Padding(0,0,0,12)};
                        picture.Disposed+=delegate{preview.Dispose();};
                        detail.Controls.Add(picture);FitContent();
                    }
                } catch(Exception ex) when (ex is IOException || ex is InvalidOperationException || ex is ArgumentException ||
                                             ex is OutOfMemoryException || ex is System.Runtime.InteropServices.ExternalException ||
                                             ex is System.Text.Json.JsonException || ex is System.ComponentModel.Win32Exception || ex is FormatException) {
                    if(IsDisposed||generation!=selection)return;
                    AddText("公告图片暂时无法载入。");
                }
            }
        }
        void Download(){
            if(release==null)return;
            using(var dlg=new SaveFileDialog{Title="将已校验的私服更新包保存为新文件",OverwritePrompt=true,FileName=Path.GetFileName(PrivateService.Text(release,"filename"))}){
                if(dlg.ShowDialog(this)!=DialogResult.OK)return;
                if(File.Exists(dlg.FileName)){status.Text="请为更新包选择一个新文件名。";return;}
                refresh.Enabled=download.Enabled=false;status.Text="正在从选定的私服下载并校验…";
                PrivateService.Finish(this,PrivateService.Request(home,server,"download",new Data{{"sha256",PrivateService.Text(release,"sha256")},{"destination",dlg.FileName}}),delegate(Data result){
                    refresh.Enabled=download.Enabled=true;
                    status.Text=PrivateService.Okay(result)?"文件大小和 SHA-256 已验证，更新包已保存。":PrivateService.Error(result);
                });
            }
        }
        void Read(){
            string previous=titles.SelectedIndex>=0 && titles.SelectedIndex<entries.Count?PrivateService.Text(entries[titles.SelectedIndex],"id"):"";
            selection++;titles.Items.Clear();entries.Clear();ClearDetail();
            refresh.Enabled=download.Enabled=false;release=null;status.Text="正在读取公告…";
            PrivateService.Finish(this,PrivateService.Request(home,server,"maintenance",new Data()),delegate(Data result){
                refresh.Enabled=true;
                if(!PrivateService.Okay(result)){status.Text=PrivateService.Error(result);return;}
                object doc;Data data=result.TryGetValue("maintenance",out doc)?doc as Data:null;
                object source;
                int rowIndex=0;
                if(data!=null&&data.TryGetValue("notices",out source)&&source is IEnumerable)
                    foreach(object row in (IEnumerable)source){var a=row as Data;if(a==null)continue;
                        if(previous==PrivateService.Text(a,"id"))rowIndex=entries.Count;
                        entries.Add(a);titles.Items.Add(PrivateService.Text(a,"title"));}
                if(data!=null&&data.TryGetValue("release",out source)&&source is Data){
                    release=(Data)source;download.Enabled=true;
                    entries.Add(release);titles.Items.Add("版本　"+PrivateService.Text(release,"version"));
                }
                status.Text=entries.Count==0?"该区服暂无公告或版本发布。":"";
                if(entries.Count>0)titles.SelectedIndex=rowIndex;
            });
        }
    }
}

