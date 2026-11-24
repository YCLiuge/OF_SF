# OF_SF

OF_SF 提供国服 TapTap PC 1.1.12 的 Windows 玩家补丁，以及可迁移的 Windows x64 / Linux x86-64 区服。游戏画面与操作使用原版 Unity 客户端，角色进度保存在所选区服。

## 玩家接入

1. 解压玩家补丁，双击 `OF_SF 玩家补丁.exe`，选择包含 `launcher.exe` 的原游戏目录。首次准备需同卷可写空间，实测约新增 2.2–2.4 GiB。
2. 填写服主提供的 `https://主机:端口`。使用自建 CA 的区服，选择服主提供的 PEM 文件。注册或登录本服账号。角色名在区服内唯一，留空可进游戏起名。
3. 有全进度备份的玩家，请在首次进入前点「导入角色进度」，选择 ZIP 或备份目录，核对原 UID、角色名、恢复地图和当前主线，再确认。原 UID 或名字已被占用时，可选本服 UID 或另行起名。首次进游戏前可以撤销。进入游戏后按住 **Alt** 可唤出指针。

「区服设置.cmd」可以更改服务器。退出后，从 TapTap 原入口可继续玩官服。该版本原客户端进图仍有发行方在线渠道验证。[玩家补丁说明](client_tools/PLAYER_PATCH.md) · [个人进度导入](OverField-Server/docs/个人进度导入.md)

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

## 功能与覆盖

| 模块 | 当前支持 |
| --- | --- |
| 角色与进度 | 瓶中小径新手教学和战斗、序章任务、角色成长、编队、装备、背包、仓库，207 个可更换头像，及个人备份中的头像、穿搭预设、挂件、染色与隐藏状态。[剧情对照](OverField-Server/docs/剧情对照.md) |
| 招募与道具 | 常驻、新手自选、可设置 UP 池、单抽、十连和保底。虚拟礼包点击发放，角色初始获得 47 种代币各 100 万及可用收藏。[招募配置](OverField-Server/docs/招募卡池.md) · [代币与收藏](OverField-Server/docs/初始代币与收藏.md) · [礼包与仓库](OverField-Server/docs/私服礼包与仓库.md) |
| 家园与多人 | 家具布置、座位、访问和点赞，附近、世界与私聊，同服组队及部分多人小游戏。[频道与家园](OverField-Server/docs/频道与家园设置.md) |
| 探索与活动 | 挂机补给、调查和间隙、152 个可扫荡关卡、联合演算、通行证、祈愿树每日任务手动领奖与月亮碎片兑换、签到、每日甜点，以及可排期的罗塔广场海上舞台。[活动复刻](OverField-Server/docs/活动复刻核对.md) · [活动管理](OverField-Server/docs/活动管理.md) · [祈愿树排期](OverField-Server/docs/祈愿树排期.md) |
| 服主管理 | 多条图文公告、版本附件、兑换码、游戏内 GM 授权、本机 CLI 与 HTTP 管理接口。[公告与版本](OverField-Server/docs/服主公告与版本发布.md) · [游戏内 GM](OverField-Server/docs/游戏内管理.md) · [管理接口](OverField-Server/docs/本机管理接口.md) |

已核对 303 种客户端请求与响应编号。仍有 11 种请求待还原，另有 4 种按区服策略关闭。部分扫荡掉落采用社区规则。[逐项验证结果](OverField-Server/docs/客户端请求对照.md) · [玩法缺口与上游对照](OverField-Server/docs/上游对照.md)

## 开发与许可

服务端使用 Python、Protobuf 和 SQLite，Windows 完整启动器使用 .NET 10。协议与部分玩法源自 byzp/of-ps，OF_SF 自行维护账号、事务、频道广播、存档迁移和运维入口。构建时从已有发行包导入对应版本的运行时及游戏资源。[源码构建](tools/BUILD.md) · [上游起始提交](https://github.com/byzp/of-ps/tree/fa2142b8521e802beb23fc6ed538205b9f4f1513) · [运行时与来源](OverField-Server/docs/RUNTIME.txt)

上游及其修改部分附 [AGPL-3.0](OverField-Server/LICENSE-of-ps.txt)，仓库原有 [GPL-3.0](LICENSE) 保留。

## 免责声明

OF_SF 仅供技术学习与交流。使用者应自行承担因使用本项目产生的后果，与作者无关。相关游戏素材及商标的权利归各自权利人所有。若您认为项目内容侵犯了您的权益，请通过本仓库的 [Issues](https://github.com/YCLiuge/OF_SF/issues) 联系作者并指出相关内容，我们会及时核实并删除侵权内容。
