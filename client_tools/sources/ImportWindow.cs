using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace OfSfImport {
    using Data = Dictionary<string,object>;

    internal sealed class IdentityChoices : TableLayoutPanel {
        sealed class Choice {
            public readonly string Mode, Label;
            public Choice(string mode, string label) { Mode=mode; Label=label; }
            public override string ToString() { return Label; }
        }
        readonly ComboBox uid=Selector("UID 继承方式"), name=Selector("角色名继承方式");
        readonly TextBox custom=new TextBox{Dock=DockStyle.Fill,MaxLength=32,AccessibleName="新的本服角色名"};
        readonly string localName;
        bool populated;
        public event EventHandler SelectionChanged;
        static ComboBox Selector(string label) {
            return new ComboBox{Dock=DockStyle.Fill,DropDownStyle=ComboBoxStyle.DropDownList,AccessibleName=label};
        }
        static string Mode(ComboBox list) {
            var selected=list.SelectedItem as Choice;
            return selected==null ? "" : selected.Mode;
        }
        static string Field(Data row,string key) {
            object value;
            return row.TryGetValue(key,out value) ? Convert.ToString(value) : "";
        }
        public IdentityChoices(string nickname) {
            localName=nickname;
            Dock=DockStyle.Fill;
            ColumnCount=3; RowCount=2;
            ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,78));
            ColumnStyles.Add(new ColumnStyle(SizeType.Percent,64));
            ColumnStyles.Add(new ColumnStyle(SizeType.Percent,36));
            for(int i=0;i<2;i++) RowStyles.Add(new RowStyle(SizeType.Percent,50));
            Controls.Add(new Label{Text="角色 UID",Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleLeft},0,0);
            Controls.Add(uid,1,0); SetColumnSpan(uid,2);
            Controls.Add(new Label{Text="角色名",Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleLeft},0,1);
            Controls.Add(name,1,1); Controls.Add(custom,2,1);
            uid.SelectedIndexChanged+=Changed;
            name.SelectedIndexChanged+=Changed;
            custom.TextChanged+=Changed;
            Visible=false;
        }
        void Changed(object sender,EventArgs args) {
            custom.Enabled=Mode(name)=="custom";
            if(SelectionChanged!=null) SelectionChanged(this,EventArgs.Empty);
        }
        static void AddOriginal(ComboBox combo,string status,string value) {
            if(status=="available") combo.Items.Add(new Choice("original","沿用备份中的 "+value));
            else if(status=="taken") combo.Items.Add(new Choice("",value+" 已被占用，请另选"));
            else if(status=="invalid") combo.Items.Add(new Choice("","此备份值不适用于本服，请另选"));
        }
        public void ShowIdentity(Data row) {
            populated=false;
            uid.Items.Clear(); name.Items.Clear(); custom.Clear();
            Visible=row!=null;
            if(row==null) return;
            AddOriginal(uid,Field(row,"uid_status"),Field(row,"uid"));
            uid.Items.Add(new Choice("new","使用本服分配的 UID"));
            AddOriginal(name,Field(row,"nickname_status"),Field(row,"nickname"));
            name.Items.Add(new Choice("local",String.IsNullOrWhiteSpace(localName)?"进入游戏时起名":"沿用本服角色名 "+localName));
            name.Items.Add(new Choice("custom","输入新的本服角色名"));
            uid.SelectedIndex=0; name.SelectedIndex=0; populated=true;
            Changed(this,EventArgs.Empty);
        }
        public bool CanApply {
            get { return !populated || (Mode(uid)!="" && Mode(name)!="" &&
                       (Mode(name)!="custom" || !String.IsNullOrWhiteSpace(custom.Text))); }
        }
        public Data Options() {
            if(!populated) return null;
            var choice=new Data{{"uid_mode",Mode(uid)},{"nickname_mode",Mode(name)}};
            if(Mode(name)=="custom") choice["nickname"]=custom.Text.Trim();
            return choice;
        }
    }

    internal sealed class ImportWindow : Form {
        readonly Func<string,Data,Task<Data>> request;
        readonly string token;
        readonly TextBox source = new TextBox{Dock=DockStyle.Fill,AccessibleName="备份 ZIP、JSON 或结果目录"};
        readonly TextBox details = new TextBox{Dock=DockStyle.Fill,Multiline=true,ScrollBars=ScrollBars.Vertical,ReadOnly=true,AccessibleName="导入预览"};
        readonly Button inspect = Button("预览备份"), apply=Button("确认导入"), undo=Button("撤销上次导入");
        readonly Button file = Button("选择文件…"), folder=Button("选择目录…");
        readonly IdentityChoices identityChoices;
        readonly RowStyle identityHeight=new RowStyle(SizeType.Absolute,0);
        internal string EffectiveNickname { get; private set; }
        string preview;
        bool working;
        static Button Button(string label){return new Button{Text=label,AutoSize=true,Height=31};}
        static string Value(Data doc,string key){object val;return doc.TryGetValue(key,out val) && val!=null?Convert.ToString(val):"";}
        static bool True(Data doc,string key){object val;return doc.TryGetValue(key,out val) && val is bool && (bool)val;}
        internal ImportWindow(string server,string nickname,string ticket,Func<string,Data,Task<Data>> send) {
            request=send;token=ticket;
            Text="OF_SF · 导入角色进度";Font=new Font("Microsoft YaHei UI",9);
            StartPosition=FormStartPosition.CenterParent;AutoScaleMode=AutoScaleMode.Dpi;
            ClientSize=new Size(705,500);MinimumSize=new Size(600,490);
            var grid=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=7,Padding=new Padding(16)};
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            foreach(int size in new[]{32,28,36,38})grid.RowStyles.Add(new RowStyle(SizeType.Absolute,size));
            grid.RowStyles.Add(identityHeight);grid.RowStyles.Add(new RowStyle(SizeType.Percent,100));grid.RowStyles.Add(new RowStyle(SizeType.Absolute,41));Controls.Add(grid);
            grid.Controls.Add(new Label{Text="目标区服　"+server+"　　角色　"+(String.IsNullOrWhiteSpace(nickname)?"当前私服账号":nickname),
                Dock=DockStyle.Fill,AutoEllipsis=true,TextAlign=ContentAlignment.MiddleLeft},0,0);
            grid.Controls.Add(new Label{Text="预览会向所选区服发送游戏 UID、角色名和可迁移进度。可选择备份 ZIP、JSON 或目录。",Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleLeft},0,1);
            grid.Controls.Add(source,0,2);
            var select=Row();select.Controls.Add(file);select.Controls.Add(folder);select.Controls.Add(inspect);grid.Controls.Add(select,0,3);
            identityChoices=new IdentityChoices(nickname);
            identityChoices.SelectionChanged+=delegate{UpdateApply();};
            grid.Controls.Add(identityChoices,0,4);
            grid.Controls.Add(details,0,5);
            var confirm=Row();confirm.Controls.Add(apply);confirm.Controls.Add(undo);grid.Controls.Add(confirm,0,6);
            apply.Enabled=false;AcceptButton=inspect;
            file.Click+=delegate{using(var dlg=new OpenFileDialog{Title="选择玩家备份",Filter="玩家备份 (*.zip;*.json)|*.zip;*.json",CheckFileExists=true}){
                if(dlg.ShowDialog(this)==DialogResult.OK)source.Text=dlg.FileName;}};
            folder.Click+=delegate{using(var dlg=new FolderBrowserDialog{Description="选择包含 snapshot.json 的结果目录",ShowNewFolderButton=false}){
                if(dlg.ShowDialog(this)==DialogResult.OK)source.Text=dlg.SelectedPath;}};
            source.TextChanged+=delegate{preview=null;SetIdentity(null);UpdateApply();details.Clear();};
            inspect.Click+=delegate{Run("import-preview");};apply.Click+=delegate{Run("import-apply");};undo.Click+=delegate{Run("import-undo");};
            FormClosing+=delegate(object sender,FormClosingEventArgs e){if(working)e.Cancel=true;};
        }
        internal static bool CheckLayout(){
            using(var dialog=new ImportWindow("localhost","临时角色","",null)){
                var handle=dialog.Handle;
                return dialog.IsHandleCreated && !dialog.apply.Enabled && dialog.inspect.Enabled && dialog.undo.Enabled
                    && dialog.source.AccessibleName.Length>0 && dialog.file.Enabled && dialog.folder.Enabled;
            }
        }
        static FlowLayoutPanel Row(){return new FlowLayoutPanel{Dock=DockStyle.Fill,WrapContents=false,Margin=new Padding(0)};}
        static Data ObjectField(Data document,string key) {
            object value;
            return document.TryGetValue(key,out value)?value as Data:null;
        }
        void SetIdentity(Data info) {
            identityChoices.ShowIdentity(info);
            identityHeight.Height=info==null?0:68;
        }
        void UpdateApply() { apply.Enabled=!working && preview!=null && identityChoices.CanApply; }
        void Busy(bool active){
            working=active;file.Enabled=folder.Enabled=inspect.Enabled=undo.Enabled=!active;
            source.ReadOnly=active;identityChoices.Enabled=!active;UpdateApply();UseWaitCursor=active;
        }
        static string Error(Data doc) {
            string detail=Value(doc,"message");
            if(detail!="")return detail;
            switch(Value(doc,"code")){
                case "import_login_required":case "invalid_import_ticket":case "import_ticket_expired":case "invalid_ticket":
                    return "导入会话已过期，请关闭此窗口并重新登录该私服账号。";
                case "invalid_preview":case "invalid_preview_id":case "import_preview_expired":case "preview_expired":
                    return "预览已过期，请重新预览备份。";
                case "player_online":case "import_player_online":case "character_online":return "请先让目标私服角色正常退出，再预览和导入。";
                case "nothing_to_undo":case "import_no_undo":case "no_import_to_undo":return "这个私服账号没有可撤销的导入。";
                case "import_progress_changed":return "角色已进入游戏或进度已变化，撤销已关闭。";
                case "import_character_has_progress":return "请选择一个注册后尚未进入游戏的私服账号。";
                case "import_uid_unavailable":return "原 UID 已被占用或不适用于此区服。请重新预览并选择本服 UID。";
                case "import_nickname_unavailable":return "角色名已被占用或不适用于此区服。请重新预览并选择其他角色名。";
                case "import_identity_conflict":return "导入期间角色身份的占用情况已变化，请重新预览。";
                case "invalid_nickname":return "请换一个符合区服命名规则的角色名。";
                case "invalid_import_identity":return "请选择可用的 UID 和角色名。";
                case "invalid_import_data":return "所选区服不能识别这份角色进度，请检查备份版本。";
                case "import_service_unavailable":return "区服导入服务暂时不可用，请稍后再试。";
                default:return "此操作未完成（"+Value(doc,"code")+"）。";
            }
        }
        static string Summary(Data doc,string headline){
            var value=new StringBuilder(headline);
            object raw;
            if(doc.TryGetValue("counts",out raw) && raw is IDictionary){
                foreach(DictionaryEntry item in (IDictionary)raw)value.AppendLine().Append(item.Key).Append("　").Append(item.Value);
            }
            if(doc.TryGetValue("warnings",out raw) && raw is IEnumerable && !(raw is string)){
                foreach(object note in (IEnumerable)raw)value.AppendLine().Append(note);
            }
            return value.ToString();
        }
        async void Run(string action){
            if(working)return;
            if(action=="import-preview" && String.IsNullOrWhiteSpace(source.Text)){details.Text="请先选择一个备份。";return;}
            if(action=="import-apply" && (preview==null || !identityChoices.CanApply))return;
            if(action=="import-undo" && MessageBox.Show(this,"将当前私服角色恢复到上次导入前保存的状态？",
                    "撤销导入",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes)return;
            var form=new Data{{"import_ticket",token}};
            if(action=="import-preview"){
                preview=null;SetIdentity(null);form["path"]=source.Text.Trim();details.Text="正在读取本地游戏数据并向目标区服生成预览…";
            }else if(action=="import-apply"){
                form["preview_id"]=preview;
                var choices=identityChoices.Options();
                if(choices!=null)form["identity"]=choices;
            }
            Busy(true);
            try {
                var reply=await request(action,form);
                if(!True(reply,"ok")){details.Text=Error(reply);if(action!="import-undo"){preview=null;SetIdentity(null);}return;}
                if(action=="import-preview"){
                    bool eligible=True(reply,"eligible");
                    preview=eligible?Value(reply,"preview_id"):null;
                    SetIdentity(eligible?ObjectField(reply,"identity"):null);
                    details.Text=Summary(reply,eligible?"仍未写入存档。核对这些项目后可点击「确认导入」。":"当前角色暂时不能应用这份备份。");
                }else{
                    preview=null;SetIdentity(null);
                    details.Text=Summary(reply,action=="import-apply"?"导入已写入目标私服账号。首次进入游戏前可撤销。":"已恢复导入前的存档。");
                    var actual=ObjectField(reply,"identity");
                    if(actual!=null) {
                        string nick=Value(actual,"nickname");
                        EffectiveNickname=nick;
                        details.AppendText(Environment.NewLine+"区服 UID　"+Value(actual,"uid")+"　　角色名　"+
                            (String.IsNullOrEmpty(nick)?"首次入场时设置":nick));
                    }
                }
            }catch(Exception){details.Text="与所选区服的连接中断，请重新预览以核对存档状态。";preview=null;}
            finally{form.Clear();Busy(false);}
        }
    }
}
