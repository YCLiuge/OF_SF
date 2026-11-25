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

## 生成可公开分享的朋友包

从校验通过的通用 ZIP 生成，构建过程只需该 ZIP 和它的 SHA-256。连接资料通过独立邀请在玩家本机导入。

```powershell
$baseZip = "dist/OF_SF-PlayerPatch-1.1.12-Windows-x64.zip"
$baseHash = ((Get-Content -LiteralPath "$baseZip.sha256" -Raw).Trim() -split '\s+')[0]
$friendsZip = "dist/OF_SF-Friends-1.1.12-Windows-x64.zip"
py -3 tools/package_friends_patch.py --source $baseZip --sha256 $baseHash --output $friendsZip
py -3 tools/test_friends_patch.py --source $baseZip --friends $friendsZip
```

服主可在已配置好的玩家补丁目录中，使用随包运行时从本机设置导出独立明文邀请。请把输出指定到自己的私有文件夹，通过单独渠道发给获准加入的人。邀请只包含区服地址和可选的公开 CA，不能代替玩家的账号密码。

```powershell
.\OverField-Server\runtime-win64\python.exe -B .\client_tools\realm_invitation.py export --output "$env:LOCALAPPDATA\我的区服.ofinvite"
```

若需实际验证区服 TLS，可在测试命令后添加 `--invitation <私有邀请文件> --check-connection`。

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

移动按场景与频道分发并合并写盘。本轮用四名角色跨两条频道以每 50 ms 上报一次，320 帧产生 16 次姿态写入，640 条预期通知全部送达，四名角色重登时位置一致。私聊、好友和组队邀请可按 UID 直接定位在线角色。附近和世界消息分别按所在频道和所选世界频道分发。招募、扫荡和新活动奖品共享区服事务，扣费、发奖及收据一起提交。管理台、CLI 和 OpenAPI 共用操作校验。当前性能数据支持继续使用单区服 SQLite；跨进程同时写同一区服、跨区服联机、公网游戏并发仍须单独验证。

## 登录、聊天与移动的实测

区服每个进程运行一个游戏世界。原生 TCP 使用 Protobuf，发行包加载原生 upb 扩展。账号、背包、任务与奖励保存在 SQLite，持久库启用 WAL。玩法事务提交后才发布响应，慢连接使用有上限的独立发送队列。WAL 支持读写并行，同一时刻只允许一个写事务。多进程写同一区服需要重新设计事务与广播归属。当前部署保留单写者，同时缩短持锁的装载和广播操作。[SQLite 的 WAL 并发模型](https://www.sqlite.org/wal.html#concurrency)

| 本机实测范围 | 改动与结果 |
| --- | --- |
| 登录背包 | 使用一个游标读取堆叠和独立实例，直接解码到原分包。混合 1,993 件、11 个分包的字节与旧实现逐包一致。91 轮交替测试中位数 9.79 → 7.43 ms，初始 1,899 件为 9.58 → 6.56 ms。11 组回环重登中，从主数据请求到场景通知中位数 16.52 → 14.52 ms。 |
| 聊天选收件人 | 为附近和世界聊天分别维护订阅名单，提交后更新，退出时清理。每频道有 3 人，每轮 5,000 次选取，8 轮交替测试。24 人时每次中位耗时 2.16 → 0.50 µs，128 人为 9.70 → 0.52 µs，512 人为 36.98 → 0.47 µs。这里测量的是内存查找耗时，不含网络或聊天落库。 |
| 移动广播 | 4 人及以上的广播复用序列化包头对象，每个收件人仍使用自己的帧序号。24 个真实回环连接、30,240 次收包，构帧入队的锁内 P95 为 120.6 → 96.0 µs。端到端 P95 随轮次波动。 |

另以 24 个并发原生客户端交替写入存档、切换头像、更新签名和移动。每版四轮，每轮 2,304 次请求、3,456 次移动投递。响应、每名收件人的移动事件及 24 份最终角色数据的校验摘要全部相同。比较四轮分位数的中位值，回环 RTT 的 P50 从 16.24 降至 11.64 ms，P95 从 20.03 降至 17.64 ms，P99 从 31.58 升至 41.15 ms。单独切换包头复用时，P99 为 46.94 和 47.51 ms。另用相同混合负载对服务端 TCP_NODELAY 做 8 轮交错试验。逐对比较，P99 两次下降、两次上升，均值从 11.45 变为 11.59 ms，本版保留原有 TCP 设置。

在具有运行时与资源的源码目录执行：

```text
python tools/benchmark_chat_channels.py
python tools/benchmark_mixed_gameplay.py --server OverField-Server --clients 24 --rounds 24 --group 6
python tools/benchmark_movement.py --pairs 4 --rounds 80 --concurrent-channels
```

在 Windows PowerShell 中，可用以下命令复测登录背包。容量取决于实际机器、玩法组合和网关延迟。

```powershell
$server = (Resolve-Path OverField-Server).Path
$env:PYTHONPATH = "$server/app;$server/app/upstream;$server/tests;$server/vendor/common;$server/vendor/win64"
python -B "$server/tests/test_login_inventory.py" --resources "$server/resources" --benchmark 91
```


## 数据库和运行时选型

当前是单进程区服，Python 执行玩法，Protobuf 使用原生 upb 扩展。SQLite WAL 保存本机存档。32 个回环客户端的写入测试中，请求等待区服锁的 P95 为 24.98 ms，SQLite 提交为 0.41 ms；两项独立统计。现阶段优先缩短区服锁内的计算、减少重复解包和大对象分配。不同区服可运行独立进程并使用各自的存档目录。

`of.py postgres-report refresh` 可将完整备份中的公开角色与背包数据投影到 PostgreSQL，`verify` 逐行校验。24 个角色、45,696 条库存上，每批 240 次跨角色道具统计的中位耗时从 SQLite 的 615 ms 降至 PostgreSQL 14.23 的 201 ms。指定角色和道具的点查分别为 1.73 和 139 ms，后者每批包含一次 `psql` 启动和本地连接。游戏仍向 SQLite 写入。多进程共用一个世界还需明确角色和场景的写入归属、跨玩家交易与领奖的事务边界。[PostgreSQL 查询副本与复测方法](../OverField-Server/docs/PostgreSQL查询副本.md)

原生扩展适合在 CPU 分析确认热点后替换独立的编解码或广播运算。每次替换必须保持相同的消息顺序、收件人、存档与重试结果。服务进程内存和网络延迟分别测量，配置的连接上限由服主按实际机器验证。

### 实际进程内存

Windows 11 x64，Python 3.12.13。用同一份合成数据库对比两版游戏进程，每人 1,997 件物品，24 个原生客户端共 47,928 件。每版独立启动三次并交错运行，每次稳定后采七个样本。下表为三轮中位数，单位 MiB。登录响应、头像、签名与存档回读均一致。另一次 1,998 件混合物品的登录快照对照中，临时 Python 分配峰值从 342 降至 46 KiB（未计入原生 Protobuf 分配）。

| 游戏进程 | 旧版工作集 | 新版工作集 | 旧版私有提交 | 新版私有提交 |
| --- | ---: | ---: | ---: | ---: |
| 启动后空闲 | 105.67 | 86.71 | 93.92 | 75.51 |
| 24 人在线 | 111.41 | 93.25 | 100.30 | 82.64 |

本轮旧版为同机已安装区服，新版在启动时解析校验配置，保留启动和首登实际用到的 30 张表，其余表在首次使用时解码并缓存。独立跟踪确认首登时无需再解码资源表。每版 8 轮交错冷启动，进程启动中位数为 665 → 543 ms，第一个角色的完整登录为 76 → 88 ms（两组范围分别为 73–111 和 71–98 ms），后两次热登录合计中位数为 16.1 → 17.1 ms。

24 人分四频道同时移动、写入存档、切换头像和修改签名，每版 4 轮、每轮 2,304 次请求、3,456 次广播投递。单轮分位数的中位值 P50/P95/P99 分别为 10.01/13.66/33.47 → 9.46/12.64/21.09 ms。角色存档、响应和广播摘要全部相同。开发中的一次首登出现 EOF，旧记录没有异常堆栈。增加诊断后，最终版本另用 5 组存档、20 次启动验证 120 次首次登录和 480 次携带装备重登，均成功。

内存统计仅含区服游戏进程。工作集计入部分可共享页面，私有提交也包含可换出的内存。网关、启动器、客户端及操作系统另计。使用同一 Python 环境执行以下命令可复测。

```powershell
python tools/benchmark_resident_memory.py --a "D:/bench/old/OverField-Server" --b OverField-Server --pairs 3 --clients 24
```

## 野外补给箱查询

首次访问一张地图的补给箱时，区服按原版点位编号建立索引。同一地图后续开箱直接定位，不再每次扫描整张图。已逐项核对 1,633 个地图箱点位。开发机上对樱庭昼 134 个点位各查 100 遍、取 7 轮中位数，查询部分从每次 5.63 微秒降至 0.57 微秒。此项测试只计点位查找，开箱全程还包括生成奖励、存档和网络回包。
