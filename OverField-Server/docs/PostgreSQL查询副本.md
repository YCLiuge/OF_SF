# PostgreSQL 查询副本

OF_SF 可以把同一份区服备份中的角色和背包发布到 PostgreSQL，供运营统计或只读报表使用。游戏继续写入 `game.sqlite` 和 `identity.sqlite3`。多人写入、原生 Protobuf、角色进度结算及恢复区服仍走现有服务。查询副本按服主选择的备份刷新。

## 刷新和校验

在区服停服时按现有流程执行 `sh start.sh backup /backup/realm.zip`。备份完成后即可重新开服。以下命令可以在另一台具有服务端程序、备份和 PostgreSQL 客户端 `psql` 的机器执行。Windows 将 `sh start.sh` 换成 `.\Start-Server.cmd`，或用 `--psql` 传入客户端的完整路径。

~~~sh
export PGSERVICE=of_sf_reporting
sh start.sh postgres-report refresh /backup/realm.zip
sh start.sh postgres-report verify /backup/realm.zip
~~~

libpq 服务配置指定目标数据库、用户和连接方式。远程部署可在服务文件中设置 `sslmode=verify-full`、`sslrootcert`，密码由 libpq 密码文件提供。使用专用报表数据库，刷新账号对目标数据库需要 `CREATE` 和 `TEMP` 权限，首次运行后由该账号持有 `of_sf_report`。多区服各用独立的目标数据库。[连接参数](https://www.postgresql.org/docs/current/libpq-connect.html) [连接服务文件](https://www.postgresql.org/docs/current/libpq-pgservice.html)

刷新前校验备份内两份 SQLite 文件的 SHA-256 和数据库完整性。程序通过 `COPY FROM STDIN` 写入临时表，以公开 UID 关联原服角色与库存。全部数据校验后在一个 PostgreSQL 事务内写入新增、变动、删除的记录，读者仍可查询已提交的版本。再次刷新会比对全部行，并保留内容相同的行。事务失败会回滚。如果在提交时连接中断，可运行 `verify` 确认已提交的版本。若需要恢复原游戏服务，请使用完整区服备份及原有 `restore` 命令。[PostgreSQL COPY](https://www.postgresql.org/docs/current/sql-copy.html)

## 可查询字段

| 表 | 内容 |
| --- | --- |
| `of_sf_report.players` | 公开 UID、角色名、等级、世界等级、最后保存的场景和频道，以及是否关联本地档案和账号。旧档缺少位置时为 NULL，无公开 UID 映射时使用存档内角色 ID。 |
| `of_sf_report.inventory` | 每条原生背包记录的 UID、存储类别、键、道具模板、背包标签及可堆叠数量。类别 0 以道具 ID 为键，类别 1 以装备等实例 ID 为键。非堆叠物品的数量为 NULL。 |
| `of_sf_report.snapshot` | 投影版本、两个源数据库的校验值、档案数、注册账号数、角色数和背包行数。 |

UID、实例编号和余额使用 `bigint`。登录凭据、令牌、账号名、聊天及物品内的完整属性只保留在源端。按道具跨角色查询可使用 `(item_id, uid)` 索引。角色背包使用 `(uid, storage, entry_id)` 主键，场景统计可使用 `(scene, channel, uid)` 索引。

~~~sql
SELECT scene, channel, count(*) AS saved_players
FROM of_sf_report.players
GROUP BY scene, channel;

SELECT count(*) AS holders, sum(quantity) AS total
FROM of_sf_report.inventory
WHERE storage = 0 AND item_id = 101 AND quantity IS NOT NULL;
~~~

供普通报表使用时，给已有读者角色授权。独立的 `verify` 会逐行比较备份与 PostgreSQL，包括缺行、多行和字段差异；运行校验的读者还需要数据库的 `TEMP` 权限。

~~~sql
GRANT USAGE ON SCHEMA of_sf_report TO report_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA of_sf_report TO report_reader;
~~~

## 验证范围

在 WSL Ubuntu 22.04 的 PostgreSQL 14.23 实例上，24 个合成角色共有 45,696 条背包记录。验收覆盖空库、旧库、UID 4,200,000,001、余额超过 32 位、同一备份重复刷新、内容差异检测、故障回滚和跨刷新的一致读事务。公开角色名中的反斜杠、制表符和换行均能往返。

同一组 240 次查询交错执行 5 轮。按道具汇总所有角色的库存，SQLite 实时读原生物品记录的中位用时为 615 毫秒，PostgreSQL 报表为 201 毫秒。指定一个角色和道具的点查，SQLite 为 1.73 毫秒，PostgreSQL 为 139 毫秒。每批 PostgreSQL 查询包含一次 `psql` 启动和本地 Unix 套接字连接；这些数据衡量报表读取，不能换算成游戏帧延迟。在源码工作区可用 `tools/test_postgres_report.py` 重跑。

## 多节点部署

若以后多个游戏进程共用一个世界，需要给角色、地图和频道分配负责写入的节点，为跨节点交易及领奖设置唯一收据，并在事务提交后转发广播。P2P 客户端可以交换表现层事件，余额、物品、UID 和交易仍由受信区服裁决。当前查询副本可部署到另一台机器，不能在游戏主库故障时接管登录或角色进度。
