# AI 代理启动器 · macOS

自动适配 macOS 上常见的各类代理软件，包括 Clash Verge Rev、Clash Party / Mihomo Party、JImmm、FlClash、Clash Nyanpasu、Sparkle、ClashX / ClashX Pro / ClashX Meta、Stash、Surge、v2rayN、V2rayU / V2RayXS、Hiddify、NekoBox / NekoRay / Throne、Karing、ShadowsocksX-NG、V2Box、Loon、独立 Mihomo / sing-box，以及任何开启了 macOS 系统代理或本地 HTTP 端口的代理软件。

## 启动

1. 若下载独立应用包 `AIProxyLauncher-macOS-arm64.zip`，解压后直接双击 `AIProxyLauncher.app` 即可运行（已内置自动加载 macOS 系统根证书，无需额外安装 Python 或手动装证书）。
2. 若使用源码启动包，需安装带 Tk 的 Python 3.11 或更新版本，解压后双击 **macos-start.command**，或在解压目录执行 `bash macos-start.command`。
3. 打开代理软件并连上节点。保持「自动检测」，点击卡片勾选应用并点「检查修复并打开」。也可在下拉框选择 **手动指定端口** 填写本机 HTTP 端口。

常规应用从 `/Applications` 和 `~/Applications` 查找；网页独立窗口支持 Chrome、Edge、Brave、Arc、Vivaldi、Chromium 等浏览器。点击「+ 管理应用」可开启 Cursor、Windsurf、VS Code、Gemini CLI、Codex CLI、Grok、Perplexity 或添加自定义应用/网页。

## 诊断、节点切换与 TUN

- **内置启动引导说明书**：打开应用后默认先弹出使用说明书，点击「我已阅读，进入启动器」后进入主界面；说明书随后收起在窗口右上角「? 使用说明」，随时可点击反复查阅。
- **统一本地中继与 SOCKS5 自动桥接**：点击顶部「⇄ 统一中继」可在 `127.0.0.1:17890` 启动本地稳定中继端口（同时支持 HTTP 与 SOCKS5 上游），独立网页窗口共用唯一资料目录（切换代理软件免重登），后台按域名自动热切换上游代理且无直连泄漏；检测到仅开放 SOCKS5 端口的代理时也会自动桥接为本地 HTTP 供所有应用使用。
- **按服务出口地区与快捷切节点**：直接探测 AI 目标域名出口与每个服务命中的出站节点（悬停服务圆点可看详情）；支持控制接口的客户端可直接通过「切换节点」测速换节点。
- **环境体检与 DNS/IPv6 诊断**：点击「环境体检」可排查并一键同步/清理 `~/.codex/.env`、`~/.gitconfig`、`~/.npmrc` 中的失效旧代理端口，同时检测 DNS 污染与公网 IPv6 泄漏，并支持一键复制脱敏诊断报告。
- **修改撤销与故障排除**：点击窗口右上角「↺ 撤销修改」可逐项或一键还原 `~/.codex/.env`、`~/.gitconfig`、`~/.npmrc` 的修改前备份（或删除本软件新建的 `.env`）、移除 LaunchAgent 开机自启（`~/Library/LaunchAgents/local.aiproxylauncher.plist`）并撤销本次会话内的代理节点/TUN 切换；也支持在终端执行 `python3 launcher.py --rollback-list` / `python3 launcher.py --rollback` 快速恢复现场。
- **单实例防多开与开机自启**：重复启动会自动唤起现有窗口；可在「+ 管理应用」中开启登录自启（LaunchAgent）。
- **运行中应用唤起与重启**：检测到桌面应用正在运行时，点击卡片「唤起 ↗」会直接激活已走对代理的窗口，或在需要切换代理端口时引导优雅退出并重新启动。
- **TUN 与透明代理 / 旁路由验证**：提供兼容控制接口（Unix 套接字或 `127.0.0.1` 控制端口）的代理可自动切换与复测 TUN；未开放控制接口的代理及路由器/旁路由透明代理会自动检测 `utun` 或直接通过系统路由验证非中国大陆 AI 出口。
- **深色 / 浅色主题**：点击顶部主题按钮可随时切换并记住偏好。

启动器设置和浏览器资料存放在 `~/Library/Application Support/AIProxyLauncher`。

## 打包独立应用

在安装了 Python 及 Xcode Command Line Tools 的 macOS 上运行以下命令：

```bash
bash build-macos.sh
bash build-macos.sh universal2
```

`构建输出/macOS-<架构>` 中会生成 `AIProxyLauncher.app` 和可分享的 ZIP。构建脚本会跑测试、检查应用签名并运行界面自检。没有图形登录会话的构建环境可设置 `RUN_GUI_SMOKE=0`。
