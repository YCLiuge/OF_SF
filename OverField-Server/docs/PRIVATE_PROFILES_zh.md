# 私有服务档案与连接票据

私有服务管理独立玩家档案。HTTP 服务通过 `127.0.0.1:18881` 发布区服地址并核验档案密钥，玩法服务在连接开始时消费短期票据。两者共用同一份 `identity.sqlite3`。已登录的本机账号与 TapTap 官服账号没有关联。

## 建档

管理员可在服务器本机预建档案。输出文件必须是新文件。它含档案密钥，交给对应玩家保管。

```console
python of.py create-player --nickname 海星 --out player.json
```

昵称可选。缺省时服务器生成唯一昵称。自选昵称长度为 1 至 16 个字符。旧版身份数据库升级时也会补全昵称。已有私服玩法存档应保留其已有非空角色名。

## 单独运行档案服务

```console
python app/private_profiles.py --state data --bind 127.0.0.1 --port 18881 --game-host 127.0.0.1 --game-port 11033 --registration closed
```

`--game-host` 和 `--game-port` 是客户端实际能到达的玩法地址。部署到容器或专网时可以更改 `--bind`，并使用 `--http-host` 指定对外提供档案服务的主机名。HTTP 只接受配置允许的 Host。HTTPS 使用 `--tls-cert` 与 `--tls-key`。HTTPS 只保护档案服务的 HTTP 连接。玩法 TCP 默认传输原始游戏数据包；跨机器传送票据与游戏数据时，应将玩法 TCP 单独放在加密隧道或受信任的专网内。独立档案服务不会启动玩法监听器，整合入口由服务端主程序提供。

在非回环地址监听时，默认关闭 HTTP 注册。需要在受控网络上开放注册时可设置 `--registration open`。关闭注册后，已建档玩家仍可申请票据。

## 独立客户端接口

| 方法和路径 | 请求 JSON | 结果 |
| --- | --- | --- |
| GET /healthz | 无 | 检查档案服务进程 |
| GET /local/v1/region | 无 | 宣告的玩法地址和协议 |
| POST /local/v1/profiles | `{}` 或 `{"nickname":"海星"}` | 新建档案，返回 slot、profile_key、nickname |
| POST /local/v1/sessions | `{"slot":"…","profile_key":"…"}` | 返回 ticket、expires_in、region、nickname |

区服信息中的 game_protocol 为 `overfield-packethead-protobuf`。玩法 TCP 按原生 PacketHead 与 protobuf 格式收包，第一条身份请求是 VerifyLoginTokenReq，login_token 字段承载本服务签发的一次性票据。随后进入角色登录及主数据流程。客户端不发送额外的 ASCII 前导。

POST 使用 `Content-Type: application/json`。客户端需把档案密钥存放在自己的私有配置里。每次重新建立玩法连接，都要重新申请一张票据。票据默认 90 秒有效，验证成功后即失效。档案数据库只保留密钥及票据的单向摘要。服务进程不输出申请内容。

迁移时先停止写入进程，再备份并移动整个服务端数据目录，包括 `identity.sqlite3` 和玩法数据库。这样原来的档案密钥、昵称及角色进度可以继续使用。此目录不应放入可公开下载的安装包。

