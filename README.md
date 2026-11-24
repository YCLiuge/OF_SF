# OF_SF

OF_SF 提供 Windows 启动器和可迁移的区服，适用于国服 TapTap PC 1.1.12。游戏使用原版 Unity 客户端。角色、物品和进度保存在玩家选定的区服。

## 开始游玩

**连接朋友的区服**。下载并解压 Windows 玩家补丁，双击补丁窗口，选择包含 `launcher.exe` 的 TapTap 国服 PC 1.1.12 安装目录。输入服主提供的 `https://域名:端口` 和本服账号密码，点击「进入游戏」。首次可注册，可留空角色名进游戏起名。区服内角色名唯一。已安装的 11 月 7 日资源需完整，原客户端进图仍有发行方在线渠道验证。[玩家补丁说明](client_tools/PLAYER_PATCH.md)

首次准备需要原游戏同一卷的独立可写目录。本机实测复用 23.58 GiB 静态资源，独立工作目录实际新增约 2.34 GiB。「区服设置.cmd」可重新选服，原客户端直接沿用启动器选好的地址。退出后，从 TapTap 原入口即可启动官服。按住 **Alt** 可在游戏中唤出指针。

在新注册的账号首次进入游戏前，可以在「导入角色进度」选择全进度工具的 ZIP 或备份目录。先预览可恢复项目，再确认导入。首次进游戏前可撤销。[个人进度导入](OverField-Server/docs/个人进度导入.md)

**本机自己开服**。用 Windows 完整工具安装程序选择原游戏目录或配套 ISO。双击安装目录顶层的启动器，选择「本机自动启动区服」。旧版 SSH 接入仍可在区服设置里使用。

## 开服与迁移

解压对应架构的服务端包，进入 `OverField-Server` 目录运行命令。两个平台包各自带有运行时。

| 操作 | Windows x64（PowerShell） | Linux x86-64 |
| --- | --- | --- |
| 启动区服 | `.\Start-Server.cmd start` | `./start.sh start` |
| 检查状态（另开终端） | `.\Start-Server.cmd health` | `./start.sh health` |
| 正常停服 | `.\Start-Server.cmd stop` | `./start.sh stop` |
| 停服后备份 | `.\Start-Server.cmd backup realm.zip` | `./start.sh backup realm.zip` |
| 恢复到空存档目录 | `.\Start-Server.cmd restore realm.zip` | `./start.sh restore realm.zip` |

从本机自动开服搬家时，需指定启动器使用的 `State/realm`。独立服务端默认在自己的 `state/realm` 存档。Windows 与 Linux 的备份可互相恢复。给普通玩家提供地址时，另开终端运行玩家 TLS 网关，将证书域名和网关端口发给玩家。[公网玩家接入](OverField-Server/docs/公网玩家接入.md) · [部署与迁移](OverField-Server/部署说明.md) · [远端连接](OverField-Server/docs/远程玩法.md)

## 已实现的功能

| 模块 | 玩家或服主可用的功能 |
| --- | --- |
| 账号与社交 | 独立账号、唯一角色名、旧档绑定、公共频道和家园、家具限额、三个家园方案槽、家园点赞、附近与世界聊天、私聊及同服组队。支持资料可见性、好友备注、亲密度赠礼，以及前往在线玩家所在的广场频道或开放家园。家园家具可按实际座位安排探索员。 |
| 角色与物品 | 47 种代币各发放 100 万，每个角色发放一次。解锁可用家具、服饰、动作和聊天表情。探索背包与仓库、三个装备预案、强化、铭文、24 项养成材料速刷、152 个可扫荡关卡、角色升级与突破、技能等级和制作按角色存档。六类生活技能支持定时学习及星石加速。支持探索员成就领奖、映像升星与图鉴、宠物捕捉与出战、邮件领取与清理。 |
| 招募与礼包 | 服主可开启多个 UP 池、调整概率。支持常驻、新手自选、单抽、十连和历史。「罗塔优选」有独立的时装十连保底和个人愿望单。测试 UP「虎虎狮妹」可在新手自选中选取。124 个虚拟礼包可按商品领取，月卡支持每日领奖。 |
| 场景与玩法 | 新角色从「瓶中小径」开始，完成风车教学后进入广场。新角色已连续验证序章九节共 50 步。已接入广场 NPC 委托接取、额外剧情末步领奖、探索手册、收藏图鉴领奖、能力和地图装置升级、体力与精力购买、广场交互、三种限时营火、挂机补给、调查与间隙、部分秘境、世界等级试炼、联合演算、餐厅合成与领奖、活动签到、每日甜点、新人支援、通行证、星云树节点与祈愿树兑换。历史活动在原客户端中的入口受排期影响。 |
| 广场与节庆 | 服主可选海上舞台、活动页、开放时间和季节街机。可从 58 条舞台排期选择布景，支持年夜饭餐厅、节庆扭蛋、沙袋、活动礼包、锦鲤换码及摄影投稿。更换舞台后重开原客户端。 |
| 深渊与多人 | 深渊支持分难度组队、双队切换、计时、领奖与赛季榜。破坏气球、浮空冒险支持同服组队、就绪、物件计分与结算。原版棋盘可与机器人对局并结算游戏币积分。 |
| 外观与社交 | 服饰染色、调色、方案保存、房间装饰、照片墙与收藏、好友心愿单和赠礼、招募邀请与回流。 |
| 服主管理 | 发布多条图文公告，编辑或撤回单条公告，发布带校验值的版本附件。玩家可在启动器查看和保存。服主可通过 CLI 或本机 HTTP 管理端口调整卡池、活动和公告，也可指定本服玩家在游戏中通过聊天管理。 |

[截图所列活动与复刻](OverField-Server/docs/活动复刻核对.md) · [祈愿树排期](OverField-Server/docs/祈愿树排期.md) · [游戏内 GM](OverField-Server/docs/游戏内管理.md) · [客户端请求逐项对照](OverField-Server/docs/客户端请求对照.md) · [剧情逐项对照](OverField-Server/docs/剧情对照.md) · [广场入口实测](OverField-Server/docs/广场入口实测.md) · [活动管理](OverField-Server/docs/活动管理.md) · [玩法覆盖与已知缺口](OverField-Server/docs/上游对照.md) · [卡池配置](OverField-Server/docs/招募卡池.md) · [代币与收藏](OverField-Server/docs/初始代币与收藏.md) · [礼包与仓库](OverField-Server/docs/私服礼包与仓库.md) · [频道与家园](OverField-Server/docs/频道与家园设置.md)

## 管理与开发

在启动器目录运行 `of-launch.cmd schema` 查看玩家端命令。在服务端运行 `admin schema` 查看管理操作和 JSON 参数。启用本机管理端口后，可打开同机的 `/admin/`，从网页调整活动排期、发行兑换码和管理照片。工具可读取 `GET /admin/v1/openapi.json` 获取同一套接口定义。例如在 Windows 服务端目录执行 `.\Start-Server.cmd admin call notice.publish --input notice.json` 即可发布一条公告。[管理接口](OverField-Server/docs/本机管理接口.md) · [图文公告与版本发布](OverField-Server/docs/服主公告与版本发布.md)

实物奖品收货地址和发行方设备日志维持关闭。游戏内 GM 操作由服主按本区服账号授权。

从源码构建时，先从自己已有的对应版本发行包导入游戏配置和平台依赖。Windows 启动器使用 .NET 10，项目提供构建安装器、校验数据和重打双平台服务端包的工具。[源码构建](tools/BUILD.md)

OF_SF 从 byzp/of-ps 的游戏协议、消息分发和部分玩法代码起步，继续使用 Python、Protobuf 和 SQLite。当前区服由 OF_SF 自己的 `of.py` 启动，负责账号、事务提交、频道广播、存档迁移和服主管理；Windows 与 Linux 发行包分别附带运行时。后续按国服 1.1.12 的资源和测试逐项移植上游玩法。[起始提交](https://github.com/byzp/of-ps/tree/fa2142b8521e802beb23fc6ed538205b9f4f1513) · [上游对照](OverField-Server/docs/上游对照.md)

上游及修改部分附 [AGPL-3.0](OverField-Server/LICENSE-of-ps.txt)，仓库原有 [GPL-3.0](LICENSE) 保留。[运行时与来源](OverField-Server/docs/RUNTIME.txt)

## 免责声明

OF_SF 仅供技术学习与交流。使用者应自行承担因使用本项目产生的后果，与作者无关。相关游戏素材及商标的权利归各自权利人所有。若您认为项目内容侵犯了您的权益，请通过本仓库的 [Issues](https://github.com/YCLiuge/OF_SF/issues) 联系作者并指出相关内容，我们会及时核实并删除侵权内容。
