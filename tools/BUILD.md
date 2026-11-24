# 从源码恢复构建输入

在仓库根目录运行以下命令。构建机需要 Git 和 Python 3.11 或更新版。游戏数据来自本机已有的 1.1.12 便携包。脚本按仓库中的 SHA-256 清单逐项校验。

## 导入本机依赖

Windows 已安装私服套件时运行

```powershell
py -3 tools/hydrate.py --base "D:\OverField\IsolatedInstaller\联网私服"
```

该命令恢复服务端数据、便携 Python、对应平台依赖和客户端地址模板。若使用单独解压的服务端 ZIP，可将 `--base` 指向其中的 `OverField-Server` 目录。构建 Windows 客户端安装器还需加上 `--client-kit "D:\OverField\IsolatedInstaller\联网私服"` 以导入地址模板。

Linux 在保留符号链接的文件系统中解压便携包，再导入数据和内置运行时

```sh
mkdir -p /tmp/of-base
tar -xzf /path/to/OverField-OnlineServer-1.1.12-Linux-x86_64.tar.gz -C /tmp/of-base
python3 tools/hydrate.py --base /tmp/of-base/OverField-Server
```

## 重打服务端压缩包

Windows 和 Linux 都能生成两个平台的包。将 `--base` 改为持有的原始压缩包路径。重打直接读取基包，无需先执行导入。Windows 示例

```powershell
py -3 tools/package_server.py --base "D:\OverField\IsolatedInstaller\OverField-OnlineServer-1.1.12-Windows-x64.zip" --output "dist/OverField-Windows-x64.zip"
py -3 tools/package_server.py --base "D:\OverField\IsolatedInstaller\OverField-OnlineServer-1.1.12-Linux-x86_64.tar.gz" --output "dist/OverField-Linux-x86_64.tar.gz"
```

Linux 使用相同参数，将 `py -3` 换成 `python3`。包内源码取自 Git 跟踪文件的当前工作区内容。新增源文件须先执行 `git add`。输出包含服务端、校验后的资源及运行时。旧基包若缺少新增玩法使用的游戏表，工具会从本地仓库的同名资源路径读取，并与已提交的源数据摘要逐项核对。输出在同目录写入 `.sha256`。玩家存档、账号库、日志与本机配置保存在各自的运行目录。

## 重打玩家补丁

在 Windows x64 上先按上文导入 Windows 依赖、客户端地址模板和国服资源，再运行以下命令。补丁使用系统自带的 .NET Framework 编译器，玩家机器可直接使用包内运行时。

```powershell
py -3 tools/package_player_patch.py --output dist/OF_SF-PlayerPatch-1.1.12-Windows-x64.zip
py -3 tools/test_compact_player.py
py -3 tools/test_player_patch.py --archive dist/OF_SF-PlayerPatch-1.1.12-Windows-x64.zip
```

## 复测频道移动

在已导入运行依赖和资源的源码目录执行下列命令。工具创建临时区服和四个角色，在两个频道同时发送 320 次移动。它逐帧核对 640 次送达、跨频道隔离和停服后四个角色的位置，并统计 SQLite 写入次数。

```sh
python tools/benchmark_movement.py --server OverField-Server --pairs 2 --rounds 80 --concurrent-channels
```

在同一台 Windows 机器上与 `800e698` 交替测量各三轮。旧版每轮写入 320 次姿态，新版为 4 次。发送后直到同频道双方收到通知的 P95，每轮分别为旧版 1.29、1.25、1.18 毫秒，新版 0.46、0.50、0.80 毫秒。该测量只覆盖本机回环和短时连续移动；服主可按自己的机器和人数复测。

同一脚本加上 `--tick-ms 50` 可模拟每个角色每秒 20 次上报，跨越多次存盘周期。本机一轮约 4 秒的对照中，姿态写入由 320 次降到 20 次，两个版本均收到 640 条预期通知，退出后均找回全部 4 个最终位置。

## 复测任务推进与招募列表

在有国服资源与运行依赖的源码目录中，以下脚本各自创建临时区服。可以用同一解释器分别指定旧版与新版的 `--server`，对比耗时和原生 Protobuf 回包摘要。

```sh
python tools/benchmark_quest_events.py --server OverField-Server --rounds 80
python tools/benchmark_recruit_list.py --server OverField-Server --iterations 300
```

与 `0748ca1` 的同机 Windows 对照中，80 轮本地 TCP 对话的 P50 从 1.52 ms 降到 0.58 ms，已完成对话重发从 0.84 ms 降到 0.17 ms。索引仅保存静态任务条件与后继关系，玩家进度仍在当前事务内读取。招募测试预置 2,000 条历史记录，8 个可见池的一次列表请求从 26 条 SQL 降至 4 条。关闭一个 UP 池后从 23 条降至 4 条，重新开启后立即恢复。用例中的任务、通知和卡池列表摘要与旧版一致。游戏帧率和公网容量需要单独测量。

## 复测连接与物品生成

```sh
python tools/benchmark_game_connections.py --server OverField-Server --clients 8 --rounds 20
python tools/benchmark_game_connections.py --server OverField-Server --clients 32 --rounds 12 --limit 32
python tools/benchmark_item_templates.py --server OverField-Server --rounds 3
```

同机回环的八角色测试连续执行 160 次请求，原版和新版响应摘要及八份最终存档相同。八人断开到全部连接名额可复用，从 511–514 ms 降到 2.25–4.15 ms。32 人的 384 次实际写入中，P95 请求往返 26.80 ms，等候服务锁 24.98 ms，SQLite 提交 0.41 ms。服主可停服后用 `rules --game-connections 32` 设置准入上限，默认 24，可设 1–512。该上限不能用作实测公网容量。

11,483 个模板逐个创建的三轮耗时中位数从 2.42 s 降到 0.08 s，批量生成维持在约 0.08 s。两条路径分别与各自旧版的完整 Protobuf 序列摘要一致。索引保存静态表，余额、角色、实例编号继续从当前区服读取。

移动按场景与频道分发并合并写盘。本轮用四名角色跨两条频道以每 50 ms 上报一次，320 帧产生 16 次姿态写入，640 条预期通知全部送达，四名角色重登时位置一致。私聊、好友和组队邀请可按 UID 直接定位在线角色。附近与世界聊天仍会检查在线玩家的频道。招募、扫荡和新活动奖品共享区服事务，扣费、发奖及收据一起提交。管理台、CLI 和 OpenAPI 共用操作校验。当前性能数据支持继续使用单区服 SQLite；跨进程同时写同一区服、跨区服联机、公网游戏并发仍须单独验证。
