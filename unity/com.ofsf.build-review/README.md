# OF_SF 构建对照

这是一个独立的 Unity Editor 工具包，使用 Unity 2021.3 的编辑器 API。

1. 在源码仓库根目录运行 `python tools/inspect_client_build.py --client cn "原版游戏目录" --client other "另一份安装目录" > builds.json`。
2. 在 Unity 的 **Window → Package Manager** 中点 **+ → Add package from disk…**，选择本包的 `package.json`。使用打好的 `.tgz` 时可选 **Add package from tarball…**。
3. 打开 **Tools → OF_SF → 客户端构建对照**，点击 **打开报告…**。第一个命令行样本作为参考，顶部下拉框切换其余样本。更新同一个 JSON 后点 **重新读取**。

窗口展示基础与有效资源版本、Unity 与脚本后端、四类包差异及代码容器校验结果。每个样本的标记来自生成命令，便于分别比较不同渠道或同一渠道的两次安装。

二次开发可从 `OfSf.BuildReview.BuildReport.Load(path)` 开始，按名称从 `Clients` 取样本，从 `Differences` 取相对参考样本的差异。`BuildCompareWindow.Open().OpenReport(path)` 可从自己的编辑器菜单直接打开报告。源码在 `Editor/`，程序集限制为 Editor。
