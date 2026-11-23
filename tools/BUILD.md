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
