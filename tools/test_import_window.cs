// Run the shared shipping WinForms controls against an ephemeral TLS test realm.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using OfSfImport;
using Data = System.Collections.Generic.Dictionary<string,object>;

internal static class ImportWindowSmoke {
    static readonly JavaScriptSerializer json=new JavaScriptSerializer();
    static Data parameters;
    static T Find<T>(Control parent,Predicate<T> match) where T:Control {
        foreach(Control item in parent.Controls){
            var candidate=item as T;
            if(candidate!=null && match(candidate))return candidate;
            var child=Find(item,match);if(child!=null)return child;
        }
        return null;
    }
    static void Click(Button target){
        if(target==null || !target.Enabled)throw new InvalidOperationException("button unavailable");
        typeof(Button).GetMethod("OnClick",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(target,new object[]{EventArgs.Empty});
    }
    delegate bool EachWindow(IntPtr handle,IntPtr state);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] static extern bool EnumThreadWindows(uint thread,EachWindow callback,IntPtr state);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr handle,uint relation);
    [DllImport("user32.dll")] static extern IntPtr GetDlgItem(IntPtr handle,int id);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr handle,uint message,IntPtr wp,IntPtr lp);
    static void UndoChoice(Form owner,Button undo,int choice){
        // Answer only the Yes/No dialog owned by this test window on this thread.
        using(var timer=new System.Windows.Forms.Timer{Interval=25}){
            timer.Tick+=delegate{
                EnumThreadWindows(GetCurrentThreadId(),delegate(IntPtr window,IntPtr unused){
                    if(GetWindow(window,4)!=owner.Handle)return true;
                    var button=GetDlgItem(window,choice);
                    if(button==IntPtr.Zero)return true;
                    timer.Stop();PostMessage(button,0x00f5,IntPtr.Zero,IntPtr.Zero);return false;
                },IntPtr.Zero);
            };
            timer.Start();Click(undo);
        }
    }
    static void Wait(Func<bool> done){
        var clock=Stopwatch.StartNew();
        while(!done() && clock.ElapsedMilliseconds<15000){Application.DoEvents();Thread.Sleep(12);}
        if(!done())throw new TimeoutException("import window state did not complete");
    }
    static Task<Data> Request(string action,Data form){
        var args=new Data(form){{"server",parameters["server"]},{"action",action}};
        return Task.Run(delegate{
            var cmd=new ProcessStartInfo((string)parameters["python"],"-B \""+(string)parameters["api"]+"\""){
                UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true};
            using(var process=Process.Start(cmd)){
                var bytes=Encoding.UTF8.GetBytes(json.Serialize(args));
                process.StandardInput.BaseStream.Write(bytes,0,bytes.Length);process.StandardInput.Close();
                string result=process.StandardOutput.ReadToEnd();process.WaitForExit();
                if(process.ExitCode!=0)throw new IOException("test player account request failed");
                return new JavaScriptSerializer().Deserialize<Data>(result);
            }
        });
    }
    [STAThread] static int Main(){
        Application.EnableVisualStyles();
        parameters=json.Deserialize<Data>(Console.In.ReadToEnd());
        foreach(bool apply in new[]{false,true}){
            using(var form=new ImportWindow("localhost","测试角色",(string)parameters["ticket"],Request)){
                var hwnd=form.Handle;
                var source=Find<TextBox>(form,t=>!t.ReadOnly);
                var details=Find<TextBox>(form,t=>t.ReadOnly);
                var preview=Find<Button>(form,b=>b.Text=="预览备份");
                var write=Find<Button>(form,b=>b.Text=="确认导入");
                var undo=Find<Button>(form,b=>b.Text=="撤销上次导入");
                if(source==null || details==null || undo==null || write.Enabled)throw new Exception("invalid initial controls");
                source.Text=(string)parameters["backup"];
                Click(preview);Wait(()=>write.Enabled || preview.Enabled);
                if(!write.Enabled || !details.Text.Contains("确认导入"))throw new Exception("preview not eligible");
                if(apply){
                    Click(write);Wait(()=>preview.Enabled);
                    if(write.Enabled || !details.Text.Contains("导入已写入"))throw new Exception("apply did not complete");
                    UndoChoice(form,undo,7); // IDNO
                    if(!details.Text.Contains("导入已写入"))throw new Exception("cancelled undo modified progress");
                    UndoChoice(form,undo,6); // IDYES
                    Wait(()=>preview.Enabled);
                    if(!details.Text.Contains("已恢复导入前的存档"))throw new Exception("undo did not complete");
                }else{
                    source.Text+=" ";if(write.Enabled)throw new Exception("changed file kept preview");
                }
                form.Close();
            }
        }
        Console.WriteLine("PASS shared WinForms controls: file selection, preview, cancel, new preview, apply, cancel undo, confirm undo.");
        return 0;
    }
}
