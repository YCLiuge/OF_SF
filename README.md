# OF_SF

OF_SF 是适配国服 TapTap PC 1.1.12 的区服服务端，支持 Windows x64 和 Linux x86-64。玩家使用独立发行的 Windows 登陆器进入原版 Unity 客户端。

## 玩家接入

1. 下载并双击 Windows 登陆器，也可完整解压 ZIP 后启动。填写服主提供的 IP 或域名，可指定端口，默认 18443。自签证书首次连接时按服主提供的指纹或 CA 验证。
2. 选择注册或登录。首次核对账号时按提示输入图形验证码。选择游戏安装目录或渠道启动器目录，登陆器会核验客户端版本。TapTap 国服 1.1.12 已验证进图。B站国服 1.1.12.0 已验证安装识别、资源热更和广场活动切换。Steam 国际版 Build 26178963 已验证基版和 11 月 25 日热更的原版资源，分别含 9,809 和 9,814 个资源文件。两个渠道的区服登录仍待验证。首次准备隔离客户端时建议预留至少 3 GiB 同卷空间。
3. 已有备份请在首次进游戏前导入，预览并核对 UID、角色名、地图和主线。游戏内按住 **Alt** 唤出指针。要回官服，可从 TapTap 原入口启动。原客户端的发行方在线渠道验证仍有可能出现。

[下载 Windows 登陆器](https://github.com/YCLiuge/OF_SF_Launcher/releases/latest/download/OF_SF-Launcher-Windows-x64.exe) · [登陆器源码与玩家说明](https://github.com/YCLiuge/OF_SF_Launcher) · [个人进度导入](OverField-Server/docs/个人进度导入.md)

## 开服与迁移

解压对应系统的服务端包，进入 `OverField-Server`。发行包已附运行时。

| 操作 | Windows PowerShell | Linux |
| --- | --- | --- |
| 启动 | `.\Start-Server.cmd start` | `./start.sh start` |
| 查看状态 | `.\Start-Server.cmd health` | `./start.sh health` |
| 正常停服 | `.\Start-Server.cmd stop` | `./start.sh stop` |
| 停服后备份 | `.\Start-Server.cmd backup realm.zip` | `./start.sh backup realm.zip` |
| 恢复到空数据目录 | `.\Start-Server.cmd restore realm.zip` | `./start.sh restore realm.zip` |

给外部玩家开服时，再启动玩家 TLS 网关，对外映射它的 TCP 端口，发送与证书匹配的地址和公开 CA。节点要求使用 IP 时，证书必须包含该 IP。独立部署默认数据目录为 `state/realm`，本机启动器使用 `State/realm`。两平台的备份可互相恢复。[公网接入与注册](OverField-Server/docs/公网玩家接入.md) · [本机部署](OverField-Server/部署说明.md) · [SSH 接入](OverField-Server/docs/远程玩法.md)

游戏写入 SQLite WAL。服主可以从停服备份刷新 PostgreSQL 查询副本，用独立账号统计角色与仓库。副本按备份更新，游戏继续在区服处理登录、扣费和发奖。[PostgreSQL 部署与校验](OverField-Server/docs/PostgreSQL查询副本.md)

在本机管理台的「概览」可查看当前游戏连接数，在线调整准入上限（默认 96，可设 1–512）。调低上限时，已连接玩家可继续游戏。通过「活动」编辑活动排期，保存后可回读生效配置。

## 功能与覆盖

复现玩家反馈或交接开服工作时，先查[玩法与区服维护索引](OverField-Server/docs/玩法排查索引.md)。按现象可找到对应的数据表、客户端界面、服务端消息和回归测试。

| 模块 | 当前支持 |
| --- | --- |
| 角色与进度 | 瓶中小径新手教学和战斗、序章任务、角色成长、编队、装备和仓库。支持 207 个头像，可从个人备份接续头像、穿搭、挂件和染色。[剧情对照](OverField-Server/docs/剧情对照.md) |
| 招募与道具 | 常驻、新手自选、可设置 UP 池、单抽、十连和保底。虚拟礼包点击发放，角色初始获得 47 种代币各 100 万及可用收藏。[招募配置](OverField-Server/docs/招募卡池.md) · [代币与收藏](OverField-Server/docs/初始代币与收藏.md) · [礼包与仓库](OverField-Server/docs/私服礼包与仓库.md) |
| 家园与多人 | 家具布置、座位、访问和点赞，附近、世界与私聊，附近玩家之间学习动作，同服组队及部分多人小游戏。[频道与家园](OverField-Server/docs/频道与家园设置.md) |
| 探索与活动 | 普通灰物可直接掉物，带礼物标识的遭遇获胜后生成礼盒。区域装置、世界挑战、挂机补给、调查和间隙、152 个可扫荡关卡、联合演算、通行证、祈愿树每日任务手动领奖与月亮碎片兑换、签到、每日甜点，以及可排期的罗塔广场海上舞台。[鸟居前与寻找玩偶](OverField-Server/docs/剧情对照.md#鸟居前与寻找玩偶) · [活动复刻](OverField-Server/docs/活动复刻核对.md) · [活动管理](OverField-Server/docs/活动管理.md) · [祈愿树排期](OverField-Server/docs/祈愿树排期.md) |
| 生活互动 | 钓鱼、伐木、采矿、采花、捉虫，以及使用灰物塔捕获宠物。鱼种、工具、掉落及进度的核对范围见[生活互动](OverField-Server/docs/生活互动.md)。 |
| 服主管理 | 多条图文公告、版本附件、兑换码、游戏内 GM 授权、本机网页管理台、CLI 与 OpenAPI 接口。[公告与版本](OverField-Server/docs/服主公告与版本发布.md) · [游戏内 GM](OverField-Server/docs/游戏内管理.md) · [管理接口](OverField-Server/docs/本机管理接口.md) |

公网测试账号已核对采集、捉虫和钓鱼结算，退出重登后背包、工具耐久和图鉴与逐次结算一致。

已核对 303 种客户端请求与响应编号。294 种业务请求和 2 种握手已接入。剩余 2 种待核实，另有 5 种按区服策略关闭。带礼物标识的遭遇出现率和部分扫荡奖励采用社区规则，服主可调。[逐项验证结果](OverField-Server/docs/客户端请求对照.md) · [玩法缺口与上游对照](OverField-Server/docs/上游对照.md) · [原生请求边界](OverField-Server/docs/原生请求边界.md)

## 开发与许可

OF_SF 提供原生玩法基线，overfiled-mod 可以按服主发布的目录展示指南、打开已安装的小游戏。格式校验和发布使用 `of.py content validate` 和 `of.py content publish`。原客户端维持原生消息和存档流程。[统一协议与接入方法](OverField-Server/docs/统一协议.md) · [原生档案](contracts/v1/native-profile.json)

服务端使用 Python、原生 Protobuf 和 SQLite WAL，Windows 完整启动器使用 .NET 10。协议与部分玩法源自 byzp/of-ps，OF_SF 自行维护账号、事务、频道广播、存档迁移和运维入口。构建时从已有发行包导入对应版本的运行时及游戏资源。[本轮性能审查](OverField-Server/docs/性能审查.md) · [四份客户端与 Unity 编辑器工具](OverField-Server/docs/客户端构建对照.md) · [架构与性能实测](tools/BUILD.md#登录聊天与移动的实测) · [源码构建](tools/BUILD.md) · [上游起始提交](https://github.com/byzp/of-ps/tree/fa2142b8521e802beb23fc6ed538205b9f4f1513) · [运行时与来源](OverField-Server/docs/RUNTIME.txt)

上游及其修改部分附 [AGPL-3.0](OverField-Server/LICENSE-of-ps.txt)，仓库原有 [GPL-3.0](LICENSE) 保留。

## 免责声明

OF_SF 仅供技术学习与交流。使用者应自行承担因使用本项目产生的后果，与作者无关。相关游戏素材及商标的权利归各自权利人所有。若您认为项目内容侵犯了您的权益，请通过本仓库的 [Issues](https://github.com/YCLiuge/OF_SF/issues) 联系作者并指出相关内容，我们会及时核实并删除侵权内容。
