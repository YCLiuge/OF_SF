# OF_SF · 开放空间私服

面向《开放空间》国服 TapTap PC 1.1.12 的社区服务端、Windows 启动器和原版 Unity 客户端接入工具。角色与进度存放在所选私服。当前零售客户端进图仍需完成发行方渠道验证。

## 部署

| 使用场景 | 操作 |
| --- | --- |
| Windows 玩家 | 双击 `开放空间私服安装.exe`，再运行 `开放空间私服.exe`。首次选择自己的 1.1.12 客户端或配套介质。本机游玩选「本机自动启动区服」。 |
| Windows x64 开服 | 解压 Windows 服务端 ZIP，进入 `OverField-Server`，运行 `.\Start-Server.cmd start`。 |
| Linux x86-64 开服 | 解压 Linux 服务端 tar.gz，进入 `OverField-Server`，运行 `./start.sh start`。 |
| 远端连接 | 在启动器选择 SSH，输入服主提供的目标和端口，验证 SSH 主机后登录该服。服务默认仅监听回环地址。 |
| 搬服 | 停服后运行 `backup realm.zip`。目标机运行 `restore realm.zip`，恢复到空存档目录，再启动。两个平台共用备份格式。 |

`start` 在当前终端运行。在第二个终端使用同一个服务端入口运行 `health` 或 `admin status`。安装包和平台基包为本地发行产物。源码构建请见下节。

## 当前可用

- 私服注册、账号密码登录、独立 UID、唯一角色名、原生起名和已有档案绑定。
- 官方频道与个人家园、家具限额与回收、附近聊天、世界分区与私聊。服主可配置范围与限额。
- 支持常驻、多个可配置 UP、自选池、保底、十连与招募记录。每个角色的一次性福利覆盖 47 种代币，各 1,000,000，另授予可用家具、动作、聊天表情和服饰。
- 探索背包支持整理、装备、入库和回花园自动入库。启动器的「私服礼包」可直接为所选私服角色领取虚拟商品。
- 已接入部分序章、调查与间隙、挂机奖励、背包与商店、部分秘境及家园交互。另支持同服多人队伍、装备强化/铭文、活动签到和成就领奖。完整主线、全部活动和多人对局仍在完善。
- 多条图文公告可分别发布、编辑与撤回。启动器可按标题查看，并可校验和保存服主发布的版本附件。
- 启动器使用 .NET 10 Windows Forms 自包含构建。桌面入口和命令行启动的是同一原生 Unity 客户端。

## 管理与自动化

玩家端运行 `of-launch.cmd schema`、`of-launch.cmd status` 或 `of-launch.cmd servers`。服主运行 `admin schema` 获取所有管理操作及其 JSON 参数，`admin status` 查看区服状态。以下示例在 Windows 服务端目录运行。

```powershell
.\Start-Server.cmd admin call notice.publish --input notice.json
'{}' | .\Start-Server.cmd admin call maintenance.list
```

`notice.json` 示例

```json
{"title":"周末活动","body":"周六晚八点在花园广场集合。","images":["posters/garden.png"]}
```

## 认证

私服账号的注册策略和游戏角色由服主管理。新私服协议客户端可直接使用区服签发的一次性票据。TapTap PC 1.1.12 自带的渠道 SDK 仍向发行方验证。改选服地址只会改变游戏区服，自定义第三方登录若要替代原客户端的渠道步骤，需要支持该身份提供方的客户端构建。

## 从源码构建

使用 Windows x64、Python 3.13 和 .NET 10 SDK 构建桌面启动器。开发所需的游戏表、客户端模板和平台依赖从本地持有的对应版本便携包导入。导入及重建平台包的命令见 [源码构建](tools/BUILD.md)。

```powershell
.\client_tools\sources\build.ps1
.\OverField-Server\runtime-win64\python.exe -B .\OverField-Server\of.py test
python .\client_tools\sources\build_installer.py . .\dist\开放空间私服安装.exe
```

[招募配置](OverField-Server/docs/招募卡池.md) · [礼包与仓库](OverField-Server/docs/私服礼包与仓库.md) · [上游玩法对照](OverField-Server/docs/上游对照.md) · [部署与存档](OverField-Server/部署说明.md) · [远端接入](OverField-Server/docs/远程玩法.md) · [管理接口](OverField-Server/docs/本机管理接口.md) · [图文公告与版本发布](OverField-Server/docs/服主公告与版本发布.md) · [频道规则](OverField-Server/docs/频道与家园设置.md)

服务端基于 [byzp/of-ps](https://github.com/byzp/of-ps/tree/fa2142b8521e802beb23fc6ed538205b9f4f1513)。上游及其修改部分随附 [AGPL-3.0](OverField-Server/LICENSE-of-ps.txt)，仓库原有的 [GPL-3.0](LICENSE) 保留。运行依赖及来源见 [运行时清单](OverField-Server/docs/RUNTIME.txt)。
