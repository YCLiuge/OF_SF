using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace OverField.PrivateLauncher {
    using Data = Dictionary<string,object>;

    internal sealed class GiftWindow : Form {
        private readonly string home;
        private readonly Data server;
        private readonly Func<IWin32Window,Data> signIn;
        private Data session;
        private readonly TextBox filter = new TextBox { Dock=DockStyle.Fill, PlaceholderText="按礼包名称或分类搜索", AccessibleName="搜索礼包" };
        private readonly ListView offers = new ListView { Dock=DockStyle.Fill, View=View.Details, FullRowSelect=true,
            MultiSelect=false, HideSelection=false, AccessibleName="可领取的私服礼包" };
        private readonly Label identity = new Label { Dock=DockStyle.Fill, TextAlign=ContentAlignment.MiddleLeft, AutoEllipsis=true };
        private readonly Label status = new Label { Dock=DockStyle.Fill, TextAlign=ContentAlignment.MiddleLeft, AutoEllipsis=true };
        private readonly Button login = PrivateService.Button("登录私服");
        private readonly Button claim = PrivateService.Button("领取到角色");
        private readonly List<Data> catalog = new List<Data>();
        private bool working;

        internal GiftWindow(string directory, Data destination, Data signedIn, Func<IWin32Window,Data> loginAction) {
            home=directory;server=destination;session=signedIn;signIn=loginAction;
            Text="开放空间 · 私服礼包";Font=new Font("Microsoft YaHei UI",9);StartPosition=FormStartPosition.CenterParent;
            ClientSize=new Size(646,498);MinimumSize=new Size(490,374);
            var grid=new TableLayoutPanel { Dock=DockStyle.Fill, Padding=new Padding(16), ColumnCount=2, RowCount=5 };
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,116));
            foreach(int height in new[]{32,36})grid.RowStyles.Add(new RowStyle(SizeType.Absolute,height));
            grid.RowStyles.Add(new RowStyle(SizeType.Percent,100));grid.RowStyles.Add(new RowStyle(SizeType.Absolute,32));
            grid.RowStyles.Add(new RowStyle(SizeType.Absolute,39));Controls.Add(grid);
            grid.Controls.Add(identity,0,0);grid.Controls.Add(login,1,0);grid.Controls.Add(filter,0,1);grid.SetColumnSpan(filter,2);
            offers.Columns.Add("礼包名称",360);offers.Columns.Add("分类",165);
            grid.Controls.Add(offers,0,2);grid.SetColumnSpan(offers,2);
            grid.Controls.Add(status,0,3);grid.SetColumnSpan(status,2);grid.Controls.Add(claim,1,4);
            filter.TextChanged+=delegate{UpdateList();};offers.SelectedIndexChanged+=delegate{State();};
            login.Click+=delegate{var fresh=signIn(this);if(fresh!=null)session=fresh;State();};
            claim.Click+=delegate{Redeem();};
            FormClosing+=delegate(object sender,FormClosingEventArgs e){if(working && e.CloseReason==CloseReason.UserClosing)e.Cancel=true;};
            Shown+=delegate{LoadOffers();};State();
        }

        private bool Valid() {
            return session!=null && PrivateService.Text(session,"gift_ticket")!="" &&
                Int64.TryParse(PrivateService.Text(session,"gift_until_ticks"),out long until) && DateTime.UtcNow.Ticks<until;
        }
        private void State() {
            string account=!Valid()?"请登录私服账号":PrivateService.Text(session,"nickname");
            if(account=="")account="首次进入原客户端后设置游戏名";
            identity.Text=PrivateService.ServerName(server)+"　·　"+account;
            login.Text=Valid()?"切换账号":"登录私服";login.Enabled=!working;
            claim.Enabled=!working && Valid() && offers.SelectedItems.Count==1;
            filter.Enabled=offers.Enabled=!working;
        }
        private void UpdateList() {
            string text=filter.Text.Trim();offers.BeginUpdate();
            try {
                offers.Items.Clear();
                foreach(var gift in catalog) {
                    string title=PrivateService.Text(gift,"title"),section=PrivateService.Text(gift,"section");
                    if(text!="" && !(title.Contains(text,StringComparison.OrdinalIgnoreCase) || section.Contains(text,StringComparison.OrdinalIgnoreCase)))continue;
                    var row=new ListViewItem(title){Tag=gift};row.SubItems.Add(section);offers.Items.Add(row);
                }
            } finally { offers.EndUpdate(); }
            State();
        }
        private async void LoadOffers() {
            working=true;status.Text="正在读取所选私服的礼包目录…";State();
            try {
                var response=await PrivateService.Request(home,server,"gifts", new Data());
                if(!PrivateService.Okay(response)){status.Text=Message(response);return;}
                catalog.Clear();
                if(response.TryGetValue("gifts",out object values) && values is IEnumerable rows)
                    foreach(var value in rows)if(value is Data gift)catalog.Add(gift);
                UpdateList();
                status.Text=catalog.Count==0?"该区服暂无可领取礼包。":"选择礼包后，可直接发放到当前私服角色。";
            } catch(Exception) {status.Text="无法读取礼包目录，请检查所选私服及服务端版本。";}
            finally {working=false;State();}
        }
        private static string Message(Data result) {
            return PrivateService.Text(result,"code") switch {
                "gift_login_required" => "礼包登录已过期，请重新登录当前私服账号。",
                "gift_ticket_used" => "上次领取已经处理。请重试同一礼包或重新登录。",
                "gift_character_required" => "请先用此私服账号进入原版客户端并完成起名，再领取礼包。",
                "gift_unavailable" => "这个礼包暂时无法领取，请查看区服的领取条件。",
                "gift_service_unavailable" => "所选区服没有启用礼包服务。",
                _ => PrivateService.Error(result)
            };
        }
        private async void Redeem() {
            if(!Valid() || working || offers.SelectedItems.Count!=1)return;
            var selected=(Data)offers.SelectedItems[0].Tag;
            string key=selected["shop"]+"/"+selected["grid"]+"/"+selected["pool"];
            string prior=PrivateService.Text(session,"gift_pending_offer");
            if(prior!="" && prior!=key){status.Text="上次领取尚未确认，请先重试同一礼包。";return;}
            if(prior==""){
                session["gift_pending_offer"]=key;session["gift_pending_id"]=Guid.NewGuid().ToString("N");
            }
            var input=new Data{{"ticket",session["gift_ticket"]},{"request_id",session["gift_pending_id"]},
                               {"shop",selected["shop"]},{"grid",selected["grid"]},{"pool",selected["pool"]}};
            working=true;status.Text="正在发放到当前私服角色…";State();
            try {
                var response=await PrivateService.Request(home,server,"claim-gift",input);
                if(PrivateService.Okay(response)) {
                    session["gift_ticket"]=response["gift_ticket"];
                    session["gift_until_ticks"]=DateTime.UtcNow.AddSeconds(Convert.ToInt32(response["gift_expires_in"])).Ticks;
                    status.Text="已领取「"+PrivateService.Text(selected,"title")+"」，物品已发放到当前私服角色。";
                } else {
                    string code=PrivateService.Text(response,"code");
                    status.Text=Message(response);
                    if(code=="transport"){status.Text+=" 请重试同一礼包以确认到账。";return;}
                    if(code=="gift_login_required")session.Remove("gift_ticket");
                }
                session.Remove("gift_pending_offer");session.Remove("gift_pending_id");
            } catch(Exception) {
                status.Text="尚未确认到账。请重试同一礼包以查询领取结果。";
            } finally {input.Clear();working=false;State();}
        }
    }
}
