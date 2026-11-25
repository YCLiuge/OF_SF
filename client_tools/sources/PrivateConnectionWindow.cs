using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace OfSfPlayer {
    using Data = Dictionary<string,object>;

    internal sealed partial class PlayerWindow {
        Data SelectedRealm {
            get {int i=servers.SelectedIndex; return i>=0 && i<entries.Count ? entries[i] : null;}
        }
        string ActiveAddress {get{return Get(SelectedRealm,"publicTls");}}
        string ActiveCa {get{return Get(SelectedRealm,"caFile");}}

        bool ManagedLegacyPreset(){
            // Upgrade an original, indexed legacy bundle; ignore loose files beside a new release.
            var manifestFile=Path.Combine(root,"player-files.json");
            if(!File.Exists(manifestFile))return false;
            var manifest=new JavaScriptSerializer().Deserialize<Data>(File.ReadAllText(manifestFile));
            object value;
            if(manifest==null || !manifest.TryGetValue("files",out value))return false;
            var files=value as Data;
            if(files==null)return false;
            using(var sha=SHA256.Create())foreach(string name in new[]{"realm-preset.json","realm-ca.pem"}){
                var file=new FileInfo(Path.Combine(root,name));var expected=Get(files,name);
                if(expected.Length!=64 || !file.Exists || file.Length>65536)return false;
                using(var input=file.OpenRead()){
                    var digest=BitConverter.ToString(sha.ComputeHash(input)).Replace("-","");
                    if(!String.Equals(expected,digest,StringComparison.OrdinalIgnoreCase))return false;
                }
            }
            return true;
        }
        void UpdateConnectionStatus(){
            connectionStatus.Text = ActiveAddress=="" ? "首次使用请导入服主单独发送的区服邀请。"
                : (ActiveCa=="" ? "已配置，使用系统证书验证。" : "已配置，使用本区服专用 CA。");
        }
        void Remember(string address,string ca,bool select){
            if(String.IsNullOrWhiteSpace(address))return;
            int index=entries.FindIndex(row=>String.Equals(Get(row,"publicTls"),address,StringComparison.OrdinalIgnoreCase));
            var rowData=new Data{{"publicTls",address},{"caFile",ca}};
            if(index<0){
                entries.Add(rowData);index=entries.Count-1;servers.Items.Add("我的区服 "+(index+1));
            }else entries[index]=rowData;
            if(select)servers.SelectedIndex=index;
            UpdateConnectionStatus();
        }
        static string Hide(string message,string sensitive){
            return String.IsNullOrEmpty(sensitive)? message :
                Regex.Replace(message,Regex.Escape(sensitive),"[已隐藏]",RegexOptions.IgnoreCase|RegexOptions.CultureInvariant);
        }
        string PrivateMessage(string text){
            foreach(var row in entries){
                string url=Get(row,"publicTls"),ca=Get(row,"caFile");
                text=Hide(text,url);text=Hide(text,ca);
                Uri uri;if(Uri.TryCreate(url,UriKind.Absolute,out uri))text=Hide(text,uri.Host);
            }
            return Regex.Replace(text,@"https?://[^\s，。；）]+","[区服地址已隐藏]",RegexOptions.IgnoreCase);
        }
        bool PrivateControls(Control rootControl){
            if(PrivateMessage(rootControl.Text)!=rootControl.Text)return false;
            var choices=rootControl as ComboBox;
            if(choices!=null)foreach(var item in choices.Items)if(PrivateMessage(item.ToString())!=item.ToString())return false;
            foreach(Control child in rootControl.Controls)if(!PrivateControls(child))return false;
            return true;
        }
        async Task<Data> ConnectionOperation(Data message){
            string response=await Execute("realm_invitation.py","ui",message,true);
            var doc=new JavaScriptSerializer().Deserialize<Data>(response);
            if(Get(doc,"ok")!="True")throw new IOException("无法使用这份连接资料。请检查邀请文件、HTTPS 地址或公开 CA。");
            return doc;
        }
        internal async Task<bool> ImportInvitation(string path, Func<string,bool> confirm){
            var inspected=await ConnectionOperation(new Data{{"action","inspect"},{"path",path}});
            string fingerprint=Get(inspected,"fingerprint");
            string prompt="邀请含本区服的连接地址，文件以明文保存。请仅导入信任的服主私下交给你的邀请。";
            if(fingerprint!="")prompt+="\n\n区服 CA 的 SHA-256 指纹\n"+fingerprint;
            else prompt+="\n\n此区服使用系统信任的 CA。";
            if(!confirm(prompt))return false;
            var result=await ConnectionOperation(new Data{{"action","import"},{"path",path},{"digest",Get(inspected,"digest")}});
            Remember(Get(result,"publicTls"),Get(result,"caFile"),true);
            Line("已保存本区服连接。可点「测试连接」，或继续注册、导入存档和登录。");
            return true;
        }
        async void SaveSelectedRealm(){
            if(busy || ActiveAddress=="")return;
            busy=true;grid.Enabled=false;
            try{
                await ConnectionOperation(new Data{{"action","manual"},{"publicTls",ActiveAddress},{"caFile",ActiveCa}});
                Line("已选用"+servers.Text+"。");
            }catch(Exception err){Line(err.Message);}
            finally{busy=false;grid.Enabled=true;}
        }
        async void ChooseInvitation(){
            if(busy)return;
            using(var file=new OpenFileDialog{Filter="区服邀请 (*.ofinvite)|*.ofinvite",CheckFileExists=true}){
                if(file.ShowDialog(this)!=DialogResult.OK)return;
                busy=true;grid.Enabled=false;
                try{
                    await ImportInvitation(file.FileName, message=>MessageBox.Show(this,message,"确认导入区服邀请",
                        MessageBoxButtons.YesNo,MessageBoxIcon.Question)==DialogResult.Yes);
                }catch(Exception err){Line(err.Message);}
                finally{busy=false;grid.Enabled=true;}
            }
        }
        async void EditConnection(){
            if(busy)return;
            using(var editor=new Form{Text="手动编辑本机连接资料",StartPosition=FormStartPosition.CenterParent,
                    FormBorderStyle=FormBorderStyle.FixedDialog,MaximizeBox=false,MinimizeBox=false,ClientSize=new Size(625,220),Font=Font}){
                var layout=new TableLayoutPanel{Dock=DockStyle.Fill,Padding=new Padding(14),ColumnCount=3,RowCount=4};
                layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,86));
                layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
                layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,85));
                foreach(int height in new[]{44,38,38,40})layout.RowStyles.Add(new RowStyle(SizeType.Absolute,height));
                var help=Label("此页会显示本机的私有连接资料。仅向获准加入区服的人分享。");
                layout.Controls.Add(help,0,0);layout.SetColumnSpan(help,3);
                var url=Box();url.Text=ActiveAddress;
                var ca=Box();ca.Text=ActiveCa;
                url.TextChanged+=delegate{ca.Clear();};
                layout.Controls.Add(Label("HTTPS 地址"),0,1);layout.Controls.Add(url,1,1);layout.SetColumnSpan(url,2);
                layout.Controls.Add(Label("CA 文件"),0,2);layout.Controls.Add(ca,1,2);
                var browse=new Button{Text="选择 PEM",Dock=DockStyle.Fill};layout.Controls.Add(browse,2,2);
                browse.Click+=delegate{using(var d=new OpenFileDialog{Filter="公开 CA (*.pem;*.crt)|*.pem;*.crt",CheckFileExists=true}){
                    if(d.ShowDialog(editor)==DialogResult.OK)ca.Text=d.FileName;
                }};
                var buttons=new FlowLayoutPanel{Dock=DockStyle.Fill,FlowDirection=FlowDirection.RightToLeft};
                var accept=new Button{Text="保存连接",AutoSize=true,DialogResult=DialogResult.OK};
                var cancel=new Button{Text="取消",AutoSize=true,DialogResult=DialogResult.Cancel};
                var export=new Button{Text="导出已保存区服邀请",AutoSize=true,Enabled=ActiveAddress!=""};
                export.Click+=async delegate{
                    if(url.Text.Trim()!=ActiveAddress || ca.Text.Trim()!=ActiveCa){
                        MessageBox.Show(editor,"请先保存连接，再导出该区服的邀请。");return;
                    }
                    if(MessageBox.Show(editor,"邀请以明文包含区服地址和可选的公开 CA。请仅私下交给获准加入的玩家。是否导出到本机私有目录？",
                        "导出区服邀请",MessageBoxButtons.YesNo)!=DialogResult.Yes)return;
                    foreach(Control button in buttons.Controls)button.Enabled=false;
                    try{
                        var result=await ConnectionOperation(new Data{{"action","export"},{"publicTls",ActiveAddress}});
                        MessageBox.Show(editor,"邀请已保存到本机。"+Environment.NewLine+Get(result,"path"),"区服邀请");
                    }catch(Exception err){MessageBox.Show(editor,PrivateMessage(err.Message));}
                    finally{if(!editor.IsDisposed)foreach(Control button in buttons.Controls)button.Enabled=true;}
                };
                buttons.Controls.Add(accept);buttons.Controls.Add(cancel);buttons.Controls.Add(export);
                layout.Controls.Add(buttons,0,3);layout.SetColumnSpan(buttons,3);
                editor.Controls.Add(layout);editor.AcceptButton=accept;editor.CancelButton=cancel;
                if(editor.ShowDialog(this)!=DialogResult.OK)return;
                busy=true;grid.Enabled=false;
                try{
                    var save=await ConnectionOperation(new Data{{"action","manual"},{"publicTls",url.Text.Trim()},{"caFile",ca.Text.Trim()}});
                    Remember(Get(save,"publicTls"),Get(save,"caFile"),true);Line("本机区服连接已保存。");
                }catch(Exception err){Line(err.Message);}
                finally{busy=false;grid.Enabled=true;}
            }
        }
    }
}
