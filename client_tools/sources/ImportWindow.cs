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

    internal sealed class ImportWindow : Form {
        readonly Func<string,Data,Task<Data>> request;
        readonly string token;
        readonly TextBox source = new TextBox{Dock=DockStyle.Fill,AccessibleName="备份 ZIP、JSON 或结果目录"};
        readonly TextBox details = new TextBox{Dock=DockStyle.Fill,Multiline=true,ScrollBars=ScrollBars.Vertical,ReadOnly=true,AccessibleName="导入预览"};
        readonly Button inspect = Button("预览备份"), apply=Button("确认导入"), undo=Button("撤销上次导入");
        readonly Button file = Button("选择文件…"), folder=Button("选择目录…");
        string preview;
        bool working;
        static Button Button(string label){return new Button{Text=label,AutoSize=true,Height=31};}
        static string Value(Data doc,string key){object val;return doc.TryGetValue(key,out val) && val!=null?Convert.ToString(val):"";}
        static bool True(Data doc,string key){object val;return doc.TryGetValue(key,out val) && val is bool && (bool)val;}
        internal ImportWindow(string server,string nickname,string ticket,Func<string,Data,Task<Data>> send) {
            request=send;token=ticket;
            Text="OF_SF · 导入角色进度";Font=new Font("Microsoft YaHei UI",9);
            StartPosition=FormStartPosition.CenterParent;AutoScaleMode=AutoScaleMode.Dpi;
            ClientSize=new Size(665,415);MinimumSize=new Size(560,410);
            var grid=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=6,Padding=new Padding(16)};
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            foreach(int size in new[]{32,28,36,38})grid.RowStyles.Add(new RowStyle(SizeType.Absolute,size));
            grid.RowStyles.Add(new RowStyle(SizeType.Percent,100));grid.RowStyles.Add(new RowStyle(SizeType.Absolute,41));Controls.Add(grid);
            grid.Controls.Add(new Label{Text="目标区服　"+server+"　　角色　"+(String.IsNullOrWhiteSpace(nickname)?"当前私服账号":nickname),
                Dock=DockStyle.Fill,AutoEllipsis=true,TextAlign=ContentAlignment.MiddleLeft},0,0);
            grid.Controls.Add(new Label{Text="选择全进度备份 ZIP、snapshot.json 或其所在目录。",Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleLeft},0,1);
            grid.Controls.Add(source,0,2);
            var select=Row();select.Controls.Add(file);select.Controls.Add(folder);select.Controls.Add(inspect);grid.Controls.Add(select,0,3);
            grid.Controls.Add(details,0,4);
            var confirm=Row();confirm.Controls.Add(apply);confirm.Controls.Add(undo);grid.Controls.Add(confirm,0,5);
            apply.Enabled=false;AcceptButton=inspect;
            file.Click+=delegate{using(var dlg=new OpenFileDialog{Title="选择玩家备份",Filter="玩家备份 (*.zip;*.json)|*.zip;*.json",CheckFileExists=true}){
                if(dlg.ShowDialog(this)==DialogResult.OK)source.Text=dlg.FileName;}};
            folder.Click+=delegate{using(var dlg=new FolderBrowserDialog{Description="选择包含 snapshot.json 的结果目录",ShowNewFolderButton=false}){
                if(dlg.ShowDialog(this)==DialogResult.OK)source.Text=dlg.SelectedPath;}};
            source.TextChanged+=delegate{preview=null;apply.Enabled=false;details.Clear();};
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
        void Busy(bool active){
            working=active;file.Enabled=folder.Enabled=inspect.Enabled=undo.Enabled=!active;
            source.ReadOnly=active;apply.Enabled=!active && preview!=null;UseWaitCursor=active;
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
            if(action=="import-apply" && preview==null)return;
            if(action=="import-undo" && MessageBox.Show(this,"将当前私服角色恢复到上次导入前保存的状态？",
                    "撤销导入",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes)return;
            var form=new Data{{"import_ticket",token}};
            if(action=="import-preview"){
                preview=null;form["path"]=source.Text.Trim();details.Text="正在读取本地游戏数据并向目标区服生成预览…";
            }else if(action=="import-apply")form["preview_id"]=preview;
            Busy(true);
            try {
                var reply=await request(action,form);
                if(!True(reply,"ok")){details.Text=Error(reply);if(action!="import-undo")preview=null;return;}
                if(action=="import-preview"){
                    bool eligible=True(reply,"eligible");
                    preview=eligible?Value(reply,"preview_id"):null;
                    details.Text=Summary(reply,eligible?"仍未写入存档。核对这些项目后可点击「确认导入」。":"当前角色暂时不能应用这份备份。");
                }else{
                    preview=null;
                    details.Text=Summary(reply,action=="import-apply"?"导入已写入目标私服账号。首次进入游戏前可撤销。":"已恢复导入前的存档。");
                }
            }catch(Exception){details.Text="与所选区服的连接中断，请重新预览以核对存档状态。";preview=null;}
            finally{form.Clear();Busy(false);}
        }
    }
}
