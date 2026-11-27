# AI 代理启动器 · Linux

全面适配主流 Linux 桌面发行版（Ubuntu / Debian / Fedora / Arch / Manjaro / openSUSE 等，支持 X11 与 Wayland），自动适配 Linux 上常见的各类代理软件与后台服务：

- **图形代理客户端**：Clash Verge Rev、Clash Party / Mihomo Party、JImmm、FlClash（含任意白标套壳分支）、Clash Nyanpasu、Sparkle、Clash Mi、GUI.for.Clash、v2rayN、v2rayA、Hiddify、NekoBox / NekoRay / Throne、Karing、GUI.for.SingBox
- **命令行 / systemd 代理内核**：Mihomo、sing-box、Xray / V2Ray、dae / daed、Shadowsocks (`sslocal` / `ss-local`)、Privoxy / Hysteria / Naive / Gost，以及任意本地 HTTP / SOCKS5 端口、TUN 虚拟网卡或旁路由透明代理

## 快速启动

### 方式一：独立二进制包（推荐，免装 Python）

1. 从 Releases 下载 `AIProxyLauncher-Linux-x86_64.tar.gz` 并解压：
   ```bash
   tar -xzf AIProxyLauncher-Linux-x86_64.tar.gz
   cd AIProxyLauncher
   ./AIProxyLauncher
   ```
2. （可选）将启动器添加到 GNOME / KDE 等桌面应用菜单：
   ```bash
   bash install.sh
   ```

### 方式二：通用源码包（适配任意架构 x86_64 / ARM64 / RISC-V 及任意 glibc 版本）

1. 确保系统已安装 Python 3.11+ 与 Tkinter（零第三方依赖）：
   - **Ubuntu / Debian**：`sudo apt update && sudo apt install -y python3 python3-tk`
   - **Fedora / RHEL**：`sudo dnf install -y python3 python3-tkinter`
   - **Arch / Manjaro**：`sudo pacman -S --needed python tk`
   - **openSUSE**：`sudo zypper install python3-tk`
2. 解压 `AIProxyLauncher-Linux-Source.tar.gz` 并运行：
   ```bash
   tar -xzf AIProxyLauncher-Linux-Source.tar.gz
   cd AIProxyLauncher-Linux
   bash linux-start.sh
   ```

## Linux 原生适配特性

- **零子进程 `/proc` 内核级监控**：直接读取 `/proc/<pid>/stat`、`/proc/<pid>/exe`、`/proc/net/tcp`、`/proc/net/tcp6` 与 `/proc/<pid>/fd`，不依赖外部工具，且能够识别以 `root` 身份运行的 systemd 代理服务（如 `clash-verge-service`、`mihomo.service`、`sing-box.service`、`v2raya.service`）监听端口。
- **多桌面环境系统代理检测**：自动读取 GNOME / Ubuntu / Cinnamon / MATE (`gsettings org.gnome.system.proxy`)、KDE Plasma (`~/.config/kioslaverc`) 以及大小写环境变量（`HTTP_PROXY` / `http_proxy` 等）。启动应用与终端 CLI 时会同时注入大小写代理变量，确保 `curl`、`git`、Node、Electron、Claude Code 等全部生效。
- **策略路由与全量 TUN 识别**：同时检查 `ip -4 route show table all`（涵盖 Mihomo / sing-box Linux 自定义策略路由表）、`/proc/net/route` 与 `/sys/class/net/<iface>/tun_flags`，即使自定义 TUN 网卡名也能准确识别。
- **自动发现桌面应用、浏览器与终端**：自动扫描 `$PATH`、Freedesktop `.desktop` 文件（含 Flatpak / Snap 导出）以及 `~/Applications` / `~/AppImages` 下的 AppImage；网页独立窗口支持 Chrome、Chromium、Edge、Brave、Vivaldi、Opera；终端 CLI 自动适配 `$TERMINAL`、`x-terminal-emulator`、`gnome-terminal`、`konsole`、`xfce4-terminal`、`kitty`、`alacritty`、`wezterm`、`ghostty`、`foot`、`xterm` 等。
- **XDG 标准数据与自启管理**：
  - 数据与独立浏览器资料目录：`~/.local/share/AIProxyLauncher`（遵循 `$XDG_DATA_HOME`）
  - 登录开机自启：`~/.config/autostart/aiproxylauncher.desktop`（遵循 `$XDG_CONFIG_HOME`）
  - 一键撤销修改：支持在窗口右上角「↺ 撤销修改」或命令行执行 `python3 launcher.py --rollback-list` / `python3 launcher.py --rollback` 还原所有外部配置备份与自启项。

## TUN 权限提示

若桌面客户端需要启用 TUN 但代理内核提示权限不足：
- 使用 **Clash Verge Rev** 时，请在软件设置中安装并启用 **Service Mode**（`clash-verge-service.service`）。
- 使用独立 **mihomo** / **sing-box** 等内核时，可通过 systemd 服务运行，或为内核二进制授予能力：
  ```bash
  sudo setcap cap_net_admin,cap_net_bind_service=+ep /path/to/mihomo
  ```

## 本地打包

在安装了 Python 3.11+（含 Tk）的 Linux 机器上运行：

```bash
bash build-linux.sh
```

构建脚本会自动运行全量单元测试、生成独立包与 `install.sh`、在当前桌面或 `xvfb-run` 下运行 `--ui-smoke` 界面自检，并在 `构建输出/Linux-<架构>/` 输出 `AIProxyLauncher-Linux-<架构>.tar.gz` 与 `AIProxyLauncher-Linux-Source.tar.gz`。无图形界面环境若未安装 `xvfb`，可设置 `RUN_GUI_SMOKE=0`。
