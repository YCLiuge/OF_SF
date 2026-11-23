using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("开放空间私服安装程序")]
[assembly: AssemblyVersion("1.1.12.0")]

namespace OverField.NativeInstaller {
    internal sealed class Manifest {
        public string product {get;set;}
        public Dictionary<string,string> files {get;set;}
    }
    internal sealed class Installer {
        const string Product="OverField.PrivateNativeKit";
        const string Marker=".overfield-native-install.json";
        static readonly StringComparison Ignore=StringComparison.OrdinalIgnoreCase;
        static string Hex(byte[] bytes) {return BitConverter.ToString(bytes).Replace("-","").ToLowerInvariant();}
        static string Hash(Stream file) {using(var sha=SHA256.Create())return Hex(sha.ComputeHash(file));}
        static string HashFile(string p) {using(var file=File.OpenRead(p))return Hash(file);}
        static void RejectLinks(string p) {
            string current=p;
            while(!String.IsNullOrEmpty(current)){
                try {
                    if((File.GetAttributes(current)&FileAttributes.ReparsePoint)!=0)
                        throw new IOException("安装路径包含链接，请选择普通目录。 "+current);
                } catch(FileNotFoundException) {} catch(DirectoryNotFoundException) {}
                current=Path.GetDirectoryName(current);
            }
        }
        static string Under(string root,string name) {
            if(String.IsNullOrEmpty(name)||Path.IsPathRooted(name)||name.IndexOfAny(new[]{':','\\','\0'})>=0 ||
               name.Split('/').Any(s=>s=="" || s=="." || s==".." || s.EndsWith(" ") || s.EndsWith(".")))
                throw new InvalidDataException("安装包中的文件名无效。");
            var result=Path.GetFullPath(Path.Combine(root,name.Replace('/',Path.DirectorySeparatorChar)));
            if(!result.StartsWith(root+Path.DirectorySeparatorChar,Ignore))throw new InvalidDataException("文件路径超出目标目录。");
            RejectLinks(result);return result;
        }
        static string ValidateDestination(string directory){
            if(String.IsNullOrWhiteSpace(directory) || !Path.IsPathRooted(directory))throw new ArgumentException("请选择绝对安装路径。");
            var root=Path.GetFullPath(directory).TrimEnd(Path.DirectorySeparatorChar,Path.AltDirectorySeparatorChar);
            if(String.IsNullOrEmpty(Path.GetDirectoryName(root)))throw new ArgumentException("请勿安装到磁盘根目录。");
            RejectLinks(root);
            for(string p=root;p!=null;p=Path.GetDirectoryName(p)){
                if(File.Exists(Path.Combine(p,"launcher.exe")) && File.Exists(Path.Combine(p,@"launcher_Data\data.unity3d")))
                    throw new IOException("请选择原版游戏安装目录以外的位置。");
            }
            return root;
        }
        static Manifest Parse(string text){
            var m=new JavaScriptSerializer().Deserialize<Manifest>(text);
            if(m==null || m.product!=Product || m.files==null)throw new InvalidDataException("安装清单无效。");
            return m;
        }
        static string EmbeddedManifest(){
            using(var r=new StreamReader(Assembly.GetExecutingAssembly().GetManifestResourceStream("OverField.Manifest"),Encoding.UTF8))return r.ReadToEnd();
        }
        public static void Run(string directory,Action<string> progress){
            string root=ValidateDestination(directory);
            string label=Hash(new MemoryStream(Encoding.UTF8.GetBytes(root.ToUpperInvariant()))).Substring(0,28);
            using(var guard=new Mutex(false,@"Local\OverField.NativeInstall."+label)){
                bool entered=false;
                try{
                    try{entered=guard.WaitOne(0);}catch(AbandonedMutexException){entered=true;}
                    if(!entered)throw new IOException("另一个安装程序正在处理此目录。");
                    Install(root,progress);
                }finally{if(entered)guard.ReleaseMutex();}
            }
        }
        sealed class Change{
            public string name,before,after,backup;
        }
        static void Install(string root,Action<string> progress){
            var text=EmbeddedManifest();
            var manifest=Parse(text);
            string marker=Under(root,Marker);
            var prior=new Dictionary<string,string>();
            string oldMarker=null;
            if(File.Exists(marker)){
                var read=File.ReadAllText(marker,Encoding.UTF8);prior=Parse(read).files;oldMarker=HashFile(marker);
            }
            var hashes=new Dictionary<string,string>(manifest.files,StringComparer.OrdinalIgnoreCase);
            var changes=new List<Change>();
            foreach(var item in hashes){
                string dst=Under(root,item.Key), existing=null;
                if(File.Exists(dst)){
                    existing=HashFile(dst);
                    if(existing==item.Value)continue;
                    string known;
                    if(!prior.TryGetValue(item.Key,out known) || existing!=known)
                        throw new IOException("目标已有不同内容，已保留该文件。 "+dst);
                }else if(Directory.Exists(dst))throw new IOException("目标中有同名目录。 "+dst);
                changes.Add(new Change{name=item.Key,before=existing,after=item.Value});
            }
            byte[] bytes=new UTF8Encoding(false).GetBytes(text);string wanted=Hash(new MemoryStream(bytes));
            if(oldMarker!=wanted)changes.Add(new Change{name=Marker,before=oldMarker,after=wanted});
            if(changes.Count==0){progress("现有安装已是本版。");return;}
            Directory.CreateDirectory(root);
            string temp=Path.Combine(root,".of-install-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(temp);
            bool preserve=false;
            var done=new List<Change>();
            try{
                var needs=new HashSet<string>(changes.Select(c=>c.name),StringComparer.OrdinalIgnoreCase);
                var seen=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                using(var pack=Assembly.GetExecutingAssembly().GetManifestResourceStream("OverField.Payload"))
                using(var zip=new ZipArchive(pack,ZipArchiveMode.Read)){
                    foreach(var e in zip.Entries){
                        string hash;
                        if(!hashes.TryGetValue(e.FullName,out hash) || !seen.Add(e.FullName))throw new InvalidDataException("载荷与安装清单不一致。");
                        if(!needs.Contains(e.FullName))continue;
                        string dest=Under(temp,e.FullName);
                        Directory.CreateDirectory(Path.GetDirectoryName(dest));
                        using(var src=e.Open())using(var dst=new FileStream(dest,FileMode.CreateNew,FileAccess.Write,FileShare.None))src.CopyTo(dst);
                        if(HashFile(dest)!=hash)throw new InvalidDataException("文件校验失败。 "+e.FullName);
                    }
                }
                if(seen.Count!=hashes.Count)throw new InvalidDataException("安装包缺少资源。");
                File.WriteAllBytes(Under(temp,Marker),bytes);
                progress("正在写入已校验的程序文件…");
                foreach(var c in changes){
                    string dst=Under(root,c.name),src=Under(temp,c.name);
                    Directory.CreateDirectory(Path.GetDirectoryName(dst));
                    if(c.before!=null){
                        if(HashFile(dst)!=c.before)throw new IOException("安装期间文件已变更。 "+dst);
                        c.backup=Path.Combine(temp,"backup-"+Guid.NewGuid().ToString("N"));
                        File.Replace(src,dst,c.backup,true);
                    }else File.Move(src,dst);
                    done.Add(c);
                }
            }catch(Exception error){
                foreach(var c in Enumerable.Reverse(done)){
                    try{
                        string dst=Under(root,c.name);
                        if(HashFile(dst)!=c.after){preserve=true;continue;}
                        if(c.backup==null)File.Delete(dst);else File.Replace(c.backup,dst,null,true);
                    }catch(IOException){preserve=true;}
                    catch(UnauthorizedAccessException){preserve=true;}
                }
                if(preserve)throw new IOException("安装被中断，原文件备份保存在 "+temp,error);
                throw;
            }finally{
                if(!preserve){
                    try{RejectLinks(temp);Directory.Delete(temp,true);}
                    catch(IOException){progress("临时文件待清理。 "+temp);}
                    catch(UnauthorizedAccessException){progress("临时文件待清理。 "+temp);}
                }
            }
        }
    }

    internal sealed class SetupWindow: Form {
        TextBox path=new TextBox();
        Label status=new Label();
        Button go=new Button(),browse=new Button();
        bool busy=false,finished=false;
        public SetupWindow(){
            Text="开放空间 · 私服安装";StartPosition=FormStartPosition.CenterScreen;
            AutoScaleMode=AutoScaleMode.Dpi;AutoScaleDimensions=new SizeF(96,96);
            Font=new Font("Microsoft YaHei UI",9);BackColor=Color.FromArgb(247,249,248);
            ClientSize=new Size(610,242);MinimumSize=new Size(575,280);
            var grid=new TableLayoutPanel{Dock=DockStyle.Fill,Padding=new Padding(22,14,22,14),ColumnCount=2,RowCount=5};
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,85));
            foreach(int h in new[]{55,27,34,48})grid.RowStyles.Add(new RowStyle(SizeType.Absolute,h));
            grid.RowStyles.Add(new RowStyle(SizeType.Percent,100));Controls.Add(grid);
            var heading=new Label{Text="安装《开放空间》私服套件",Font=new Font(Font.FontFamily,16,FontStyle.Bold),
                Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleLeft};
            grid.Controls.Add(heading,0,0);grid.SetColumnSpan(heading,2);
            var note=new Label{Text="安装目录",Dock=DockStyle.Fill,TextAlign=ContentAlignment.BottomLeft};
            grid.Controls.Add(note,0,1);
            path.Dock=DockStyle.Fill;
            path.Text=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"联网私服");
            grid.Controls.Add(path,0,2);browse.Text="浏览…";browse.Dock=DockStyle.Fill;grid.Controls.Add(browse,1,2);
            browse.Click+=delegate {using(var dlg=new FolderBrowserDialog()){dlg.Description="选择私服套件安装位置";if(Directory.Exists(path.Text))dlg.SelectedPath=path.Text;if(dlg.ShowDialog(this)==DialogResult.OK)path.Text=dlg.SelectedPath;}};
            go.Text="安装";go.Width=128;go.Height=30;go.Anchor=AnchorStyles.Right;
            go.FlatStyle=FlatStyle.Flat;go.BackColor=Color.FromArgb(19,82,67);go.ForeColor=Color.White;
            grid.Controls.Add(go,0,3);grid.SetColumnSpan(go,2);AcceptButton=go;
            status.Text="首次使用时，可选择已有的国服 PC 客户端或配套的原版 ISO / ZIP。";
            status.Dock=DockStyle.Fill;status.AutoEllipsis=true;grid.Controls.Add(status,0,4);grid.SetColumnSpan(status,2);
            go.Click+=async delegate{await Execute();};
            FormClosing+=delegate(object sender,FormClosingEventArgs e){if(busy)e.Cancel=true;};
        }
        async Task Execute(){
            if(busy)return;
            string dest=path.Text.Trim();
            try{
                if(finished){Process.Start(new ProcessStartInfo(Path.Combine(dest,"开放空间私服.exe"),"--settings"){WorkingDirectory=dest,UseShellExecute=true});Close();return;}
                busy=true;browse.Enabled=go.Enabled=false;path.ReadOnly=true;UseWaitCursor=true;
                status.Text="正在检查文件并准备安装…";
                await Task.Run(()=>Installer.Run(dest,message=> BeginInvoke((Action)delegate{status.Text=message;})));
                finished=true;go.Text="打开区服设置";status.Text="安装完成。可打开区服设置，选择客户端或安装介质。";
            }catch(Exception e){status.Text=e.Message;MessageBox.Show(this,e.Message,"安装提示",MessageBoxButtons.OK,MessageBoxIcon.Information);}
            finally{busy=false;go.Enabled=true;browse.Enabled=!finished;path.ReadOnly=finished;UseWaitCursor=false;}
        }
    }
    static class Program{
        [STAThread]static int Main(string[] args){
            if(args.Length==2 && args[0]=="--install"){
                try{Installer.Run(args[1],s=>Console.WriteLine(s));return 0;}
                catch(Exception e){Console.Error.WriteLine(e.Message);return 1;}
            }
            Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);Application.Run(new SetupWindow());return 0;
        }
    }
}

