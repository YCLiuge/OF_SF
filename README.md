# OF_SF

OF_SF 提供 Windows 启动器和可迁移的区服，适用于国服 TapTap PC 1.1.12。游戏使用原版 Unity 客户端。角色、物品和进度保存在玩家选定的区服。

## 开始游玩

1. 在 Windows 上双击发行目录中的安装程序。首次运行时选择已有的 1.1.12 客户端，或将配套 ISO 放在安装程序旁。
2. 双击安装目录顶层的启动器。自己开服选择「本机自动启动区服」。连接已有区服时，双击同目录的 `区服设置.cmd`，填写服主提供的 SSH 地址和端口。
3. 选择「私服账号」注册或登录。注册时留空游戏名，可在首次进图时使用游戏内起名界面。同区服的角色名必须唯一。TapTap PC 客户端进图仍需完成发行方渠道验证。
4. 进入标题画面后单击屏幕。游戏内按住 **Alt** 唤出鼠标。领取虚拟礼包可在启动器点「私服礼包」，登录该区服，选中礼包后点「领取」。

## 开服与迁移

解压对应架构的服务端包，进入 `OverField-Server` 目录运行命令。两个平台包各自带有运行时。

| 操作 | Windows x64（PowerShell） | Linux x86-64 |
| --- | --- | --- |
| 启动区服 | `.\Start-Server.cmd start` | `./start.sh start` |
| 检查状态（另开终端） | `.\Start-Server.cmd health` | `./start.sh health` |
| 正常停服 | `.\Start-Server.cmd stop` | `./start.sh stop` |
| 停服后备份 | `.\Start-Server.cmd backup realm.zip` | `./start.sh backup realm.zip` |
| 恢复到空存档目录 | `.\Start-Server.cmd restore realm.zip` | `./start.sh restore realm.zip` |

从本机自动开服搬家时，需指定启动器使用的 `State/realm`。独立服务端默认在自己的 `state/realm` 存档。Windows 与 Linux 的备份可互相恢复。[部署与迁移](OverField-Server/部署说明.md) · [远端连接](OverField-Server/docs/远程玩法.md)

## 已实现的功能

| 模块 | 玩家或服主可用的功能 |
| --- | --- |
| 账号与社交 | 独立账号、唯一角色名、旧档绑定、公共频道和家园、家具限额、附近与世界聊天、私聊、同服组队。 |
| 角色与物品 | 47 种代币各发放 100 万，每个角色发放一次。解锁可用家具、服饰、动作和聊天表情。探索背包与仓库、穿戴、强化、铭文和制作按角色存档。 |
| 招募与礼包 | 服主可开启多个 UP 池、调整概率。支持常驻、新手自选、单抽、十连和历史。测试 UP「虎虎狮妹」可在新手自选中选取。124 个虚拟礼包可按商品领取，月卡支持每日领奖。 |
| 场景与玩法 | 新角色从「瓶中小径」开始，完成风车教学后进入广场。已接入序章「新的伙伴」「初入空间」、广场交互、挂机补给、调查与间隙、部分秘境、世界等级试炼、联合演算、餐厅合成与领奖、活动签到、每日甜点、新人支援、通行证、星云树节点与祈愿树兑换。历史活动在原客户端中的入口受排期影响。 |
| 服主管理 | 发布多条图文公告，编辑或撤回单条公告，发布带校验值的版本附件。玩家可在启动器查看和保存。 |

[剧情逐项对照](OverField-Server/docs/剧情对照.md) · [广场入口实测](OverField-Server/docs/广场入口实测.md) · [活动管理](OverField-Server/docs/活动管理.md) · [玩法覆盖与已知缺口](OverField-Server/docs/上游对照.md) · [卡池配置](OverField-Server/docs/招募卡池.md) · [代币与收藏](OverField-Server/docs/初始代币与收藏.md) · [礼包与仓库](OverField-Server/docs/私服礼包与仓库.md) · [频道与家园](OverField-Server/docs/频道与家园设置.md)

## 管理与开发

在启动器目录运行 `of-launch.cmd schema` 查看玩家端命令。在服务端运行 `admin schema` 查看管理操作和 JSON 参数。例如在 Windows 服务端目录执行 `.\Start-Server.cmd admin call notice.publish --input notice.json` 即可发布一条公告。[管理接口](OverField-Server/docs/本机管理接口.md) · [图文公告与版本发布](OverField-Server/docs/服主公告与版本发布.md)

从源码构建时，先从自己已有的对应版本发行包导入游戏配置和平台依赖。Windows 启动器使用 .NET 10，项目提供构建安装器、校验数据和重打双平台服务端包的工具。[源码构建](tools/BUILD.md)

服务端基于 [byzp/of-ps](https://github.com/byzp/of-ps/tree/fa2142b8521e802beb23fc6ed538205b9f4f1513)。上游及修改部分附 [AGPL-3.0](OverField-Server/LICENSE-of-ps.txt)，仓库原有 [GPL-3.0](LICENSE) 保留。[运行时与来源](OverField-Server/docs/RUNTIME.txt)
