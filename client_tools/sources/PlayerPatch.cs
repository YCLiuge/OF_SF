using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Web.Script.Serialization;

namespace OfSfPlayer {
    using Data = Dictionary<string,object>;

    internal sealed partial class PlayerWindow : Form {
        readonly string root = AppDomain.CurrentDomain.BaseDirectory;
        readonly bool picker;
        readonly TextBox source = Box(), work = Box(), account = Box(), password = Box(), nickname = Box();
        readonly ComboBox servers = new ComboBox { Dock=DockStyle.Fill, DropDownStyle=ComboBoxStyle.DropDownList };
        readonly Label connectionStatus = Label("首次使用请导入服主单独发送的区服邀请。");
        readonly CheckBox register = new CheckBox { Text="创建新私服账号", AutoSize=true };
        readonly TextBox output = new TextBox { Multiline=true, ReadOnly=true, ScrollBars=ScrollBars.Vertical, Dock=DockStyle.Fill };
        readonly TableLayoutPanel grid = new TableLayoutPanel { Dock=DockStyle.Fill, ColumnCount=3, RowCount=12, Padding=new Padding(16) };
        readonly List<Data> entries = new List<Data>();
        bool busy;
        Data signedIn;
        string signedAddress,signedCA,signedUser;
        DateTime loginUntil,importUntil;
        static TextBox Box(){return new TextBox { Dock=DockStyle.Fill };}
        static string Get(Data doc,string name){object v;return doc!=null && doc.TryGetValue(name,out v) && v!=null?Convert.ToString(v):"";}
        static Label Label(string label){return new Label {Text=label, Dock=DockStyle.Fill, TextAlign=ContentAlignment.MiddleLeft};}
        void ButtonAt(string label,int row,Action click){
            var b=new Button {Text=label, Dock=DockStyle.Fill};b.Click+=delegate{click();};grid.Controls.Add(b,2,row);
        }
        void Field(string title,Control field,int row){grid.Controls.Add(Label(title),0,row);grid.Controls.Add(field,1,row);}
        void Span(Control field,int row){grid.Controls.Add(field,0,row);grid.SetColumnSpan(field,3);}
        void Browse(TextBox box){
            using(var dialog=new FolderBrowserDialog()){
                if(Directory.Exists(box.Text))dialog.SelectedPath=box.Text;
                if(dialog.ShowDialog(this)==DialogResult.OK)box.Text=dialog.SelectedPath;
            }
        }
        internal PlayerWindow(bool selectRealm){
            picker=selectRealm;Text="OF_SF · 玩家补丁";StartPosition=FormStartPosition.CenterScreen;
            Font=new Font("Microsoft YaHei UI",9);AutoScaleMode=AutoScaleMode.Dpi;
            ClientSize=new Size(740,540);MinimumSize=new Size(630,525);Controls.Add(grid);
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,103));grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,95));
            foreach(var h in new[]{36,32,32,32,32,32,32,32,32,40,26})grid.RowStyles.Add(new RowStyle(SizeType.Absolute,h));
            grid.RowStyles.Add(new RowStyle(SizeType.Percent,100));
            var title=Label("连接 OF_SF 区服");title.Font=new Font(Font.FontFamily,15,FontStyle.Bold);Span(title,0);
            Field("TapTap 游戏目录",source,1);ButtonAt("浏览…",1,delegate{Browse(source);});
            Field("工作位置",work,2);ButtonAt("浏览…",2,delegate{Browse(work);});
            Field("当前区服",servers,3);
            ButtonAt("导入区服邀请",3,ChooseInvitation);
            Field("连接状态",connectionStatus,4);
            ButtonAt("手动连接设置",4,EditConnection);
            Field("私服账号",account,5);grid.SetColumnSpan(account,2);
            Field("私服密码",password,6);password.UseSystemPasswordChar=true;grid.SetColumnSpan(password,2);
            Field("首次使用",register,7);
            ButtonAt("仅创建账号",7,delegate{register.Checked=true;Run("register");});
            Field("游戏名（可选）",nickname,8);nickname.Enabled=false;grid.SetColumnSpan(nickname,2);
            register.CheckedChanged+=delegate{nickname.Enabled=register.Checked;};
            var buttons=new FlowLayoutPanel{Dock=DockStyle.Fill,WrapContents=false};
            Add(buttons,picker?"应用并重启":"进入游戏",delegate{Run("play");});
            Add(buttons,"首次进游戏前导入存档",delegate{Run("import");});
            Add(buttons,"测试连接",delegate{Run("test");});
            Add(buttons,"清除补丁数据",delegate{Run("uninstall");});Span(buttons,9);
            Span(Label("有角色备份？请先创建本区账号并导入存档，再进入游戏。已有账号可直接登录。"),10);Span(output,11);
            FormClosing+=delegate(object s,FormClosingEventArgs e){if(busy)e.Cancel=true;};
            servers.SelectedIndexChanged+=delegate{UpdateConnectionStatus();};
            servers.SelectionChangeCommitted+=delegate{SaveSelectedRealm();};
            Restore();
        }
        void Add(FlowLayoutPanel flow,string text,Action click){
            var button=new Button{Text=text,AutoSize=true,Height=30};button.Click+=delegate{click();};flow.Controls.Add(button);
        }
        void Restore(){
            try{
                var file=Path.Combine(root,"player-settings.json");
                if(File.Exists(file)){
                    var prev=new JavaScriptSerializer().Deserialize<Data>(File.ReadAllText(file,Encoding.UTF8));
                    source.Text=Get(prev,"source");work.Text=Get(prev,"base");
                    object history;if(prev.TryGetValue("servers",out history) && history is IEnumerable){
                        foreach(var obj in (IEnumerable)history){var item=obj as Data;if(item!=null)Remember(Get(item,"publicTls"),Get(item,"caFile"),false);}
                    }
                    Remember(Get(prev,"publicTls"),Get(prev,"caFile"),true);
                }
                else{
                    var presetPath=Path.Combine(root,"realm-preset.json");
                    if(File.Exists(presetPath) && ManagedLegacyPreset()){
                        var preset=new JavaScriptSerializer().Deserialize<Data>(File.ReadAllText(presetPath,Encoding.UTF8));
                        var address=Get(preset,"publicTls");
                        Uri endpoint;
                        if(Get(preset,"version")!="1" || !Uri.TryCreate(address,UriKind.Absolute,out endpoint)
                            || endpoint.Scheme!=Uri.UriSchemeHttps || endpoint.UserInfo!="" || endpoint.AbsolutePath!="/"
                            || endpoint.Query!="" || endpoint.Fragment!="" || Get(preset,"caFile")!="realm-ca.pem")
                            throw new IOException("区服预设无效。");
                        var bundledCA=Path.Combine(root,"realm-ca.pem");
                        if(!File.Exists(bundledCA))throw new IOException("缺少随包区服证书。");
                        Remember(address,bundledCA,true);
                    }
                }
                if(source.Text=="")foreach(var drive in DriveInfo.GetDrives()){
                    if(drive.DriveType!=DriveType.Fixed)continue;
                    var game=Path.Combine(drive.Name,@"TapTap\PC Games\176228");
                    if(File.Exists(Path.Combine(game,"launcher.exe"))){source.Text=game;break;}
                }
            }catch(IOException){Line("无法读取区服设置，可重新导入邀请。");}
            catch(ArgumentException){Line("区服设置的文件格式无效。");}
            catch(InvalidOperationException){Line("区服设置的文件格式无效。");}
        }
        void Line(string value){if(!IsDisposed && !String.IsNullOrWhiteSpace(value))output.AppendText(PrivateMessage(value)+Environment.NewLine);}
        Task<string> Execute(string module,string action,Data data,bool json){
            return Task.Run(delegate{
                string python=Path.Combine(root,@"OverField-Server\runtime-win64\python.exe");
                string script=Path.Combine(root,"client_tools",module);
                if(!File.Exists(python)||!File.Exists(script))throw new IOException("请先完整解压玩家补丁。");
                var cmd=new ProcessStartInfo(python,"-B \""+script+"\" "+action){
                    WorkingDirectory=root, UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,
                    RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
                cmd.EnvironmentVariables["PYTHONUTF8"]="1";cmd.EnvironmentVariables["PYTHONUNBUFFERED"]="1";
                using(var proc=new Process()){
                    proc.StartInfo=cmd;
                    if(!json)proc.OutputDataReceived+=delegate(object sender,DataReceivedEventArgs e){if(e.Data!=null)BeginInvoke((Action<string>)Line,e.Data);};
                    proc.Start();var errors=proc.StandardError.ReadToEndAsync();
                    Task<string> result=null;if(json)result=proc.StandardOutput.ReadToEndAsync();else proc.BeginOutputReadLine();
                    var bytes=Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(data));
                    try{proc.StandardInput.BaseStream.Write(bytes,0,bytes.Length);}
                    finally{Array.Clear(bytes,0,bytes.Length);proc.StandardInput.Close();}
                    proc.WaitForExit();
                    if(proc.ExitCode!=0)throw new IOException(json?"私服操作失败。":errors.Result.Trim());
                    return json?result.Result:"";
                }
            });
        }
        async void Run(string action){
            if(busy)return;
            if(action=="uninstall" && MessageBox.Show(this,"清除本补丁的游戏副本、暂存角色数据和服务器地址？远程区服存档保存在服主的服务器。","清除补丁数据",MessageBoxButtons.YesNo)!=DialogResult.Yes)return;
            busy=true;grid.Enabled=false;output.Clear();
            string target=ActiveAddress, ca=ActiveCa;
            var conf=new Data{{"source",source.Text.Trim()},{"base",work.Text.Trim()},{"publicTls",target},{"caFile",ca}};
            try{
                if(action=="uninstall"){
                    await Execute("player_patch.py",action,conf,false);
                    entries.Clear();servers.Items.Clear();UpdateConnectionStatus();work.Clear();
                    if(signedIn!=null)signedIn.Clear();signedIn=null;
                    return;
                }
                if(target=="")throw new IOException("请先导入服主发来的区服邀请，也可打开手动连接设置。");
                if(action=="test"){
                    string result=await Execute("private_account_api.py","",new Data{{"action","maintenance"},{"server",conf}},true);
                    var doc=new JavaScriptSerializer().Deserialize<Data>(result);
                    if(Get(doc,"ok")!="True")throw new IOException(Error(doc));
                    Line("TLS 证书和区服账号接口验证通过。");return;
                }
                bool importing=action=="import";
                bool same=signedIn!=null && signedUser==account.Text && signedAddress==target && signedCA==ca;
                bool valid=same && DateTime.UtcNow<(importing?importUntil:loginUntil) && password.Text=="" && !register.Checked;
                if(!valid && (account.Text=="" || password.Text==""))throw new IOException("请填写本区服单独使用的账号和密码。");
                if(!importing && action!="register"){
                    Line("正在检查本机游戏文件。首次准备可能需要几分钟。");
                    await Execute("player_patch.py","prepare",conf,false);
                }
                if(!valid){
                    if(signedIn!=null)signedIn.Clear();signedIn=null;
                    Line("正在登录所选区服。");
                    var form=new Data{{"action",register.Checked?"register":"login"},{"server",conf},{"username",account.Text},{"password",password.Text},{"nickname",nickname.Text}};
                    string session;
                    try{session=await Execute("private_account_api.py","",form,true);}
                    finally{form.Clear();password.Clear();}
                    var receipt=new JavaScriptSerializer().Deserialize<Data>(session);
                    if(Get(receipt,"ok")!="True")throw new IOException(Error(receipt));
                    signedIn=receipt;signedUser=account.Text;signedAddress=target;signedCA=ca;
                    int ttl;Int32.TryParse(Get(receipt,"expires_in"),out ttl);loginUntil=DateTime.UtcNow.AddSeconds(Math.Max(0,Math.Min(ttl,86400)));
                    Int32.TryParse(Get(receipt,"import_expires_in"),out ttl);importUntil=DateTime.UtcNow.AddSeconds(Math.Max(0,Math.Min(ttl,900)));
                    register.Checked=false;
                }
                if(action=="register"){
                    Line("账号已创建。要接续原角色，请先点「首次进游戏前导入存档」，预览并确认后再进入游戏。想从头开始可直接点「进入游戏」。");return;
                }
                if(importing){
                    var token=Get(signedIn,"import_ticket");
                    if(token=="")throw new IOException("所选区服尚未提供角色进度导入。");
                    using(var dialog=new OfSfImport.ImportWindow(servers.Text,Get(signedIn,"nickname"),token,
                        async (operation,values)=>{
                            var message=new Data(values){{"action",operation},{"server",conf}};
                            string response=await Execute("private_account_api.py","",message,true);
                            return new JavaScriptSerializer().Deserialize<Data>(response);
                        })){
                        dialog.ShowDialog(this);
                        if(dialog.EffectiveNickname!=null) signedIn["nickname"]=dialog.EffectiveNickname;
                    }
                    return;
                }
                conf["account"]=new Data{{"slot",Get(signedIn,"slot")},{"ticket",Get(signedIn,"ticket")}};
                signedIn.Clear();signedIn=null;
                await Execute("player_patch.py",picker?"relaunch":"launch",conf,false);
                busy=false;Close();
            }catch(Exception error){Line(error.Message);}
            finally{conf.Clear();busy=false;if(!IsDisposed)grid.Enabled=true;}
        }
        internal bool ValidControls(){
            CreateControl();
            return IsHandleCreated && Text.Contains("OF_SF") && password.UseSystemPasswordChar && grid.Controls.Count>=20
                && servers.DropDownStyle==ComboBoxStyle.DropDownList && !nickname.Enabled && PrivateControls(this);
        }
        static string Error(Data doc){string message=Get(doc,"message");return message!=""?message:"所选区服未接受请求（"+Get(doc,"code")+"）。";}
    }
    internal static class PlayerProgram {
        [STAThread] static void Main(string[] args){
            Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
            using(var page=new PlayerWindow(Array.IndexOf(args,"--realm-picker")>=0)){
                if(Array.IndexOf(args,"--check-ui")>=0){
                    var windowHandle=page.Handle;
                    Environment.ExitCode=(page.ValidControls() && OfSfImport.ImportWindow.CheckLayout())?0:2;
                    return;
                }
                Application.Run(page);
            }
        }
    }
}
