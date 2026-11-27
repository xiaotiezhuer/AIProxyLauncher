<div align="center">

# AIProxyLauncher

**Cross-Platform (Windows / macOS) Proxy Launcher & Diagnostic Dashboard for AI Apps**  
*100% Pure Python 3.11+ Standard Library · Zero Runtime Dependencies · Universal Proxy & White-Label Client Detection*

[简体中文](README.md) · **English**

[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS-0D9488?style=flat-square)](../../releases)
[![Python](https://img.shields.io/badge/Python-3.11%2B%20Stdlib%20Only-2563EB?style=flat-square)](#run-from-source--build)
[![Release](https://img.shields.io/badge/Release-Latest-10B981?style=flat-square)](../../releases/latest)

<br />

<img src="docs/screenshots/dashboard-dark.png" alt="AIProxyLauncher Dark Dashboard" width="900" />

</div>

---

## Interface Preview

<table>
  <tr>
    <td width="50%" align="center">
      <img src="docs/screenshots/dashboard-light.png" alt="Light Theme Dashboard" />
      <br />
      <b>Light Theme · Health Ring & Official Brand Icon Cards</b>
    </td>
    <td width="50%" align="center">
      <img src="docs/screenshots/dashboard-dark.png" alt="Dark Theme Dashboard" />
      <br />
      <b>Dark Theme · Stable Local Relay & Live Route Attribution</b>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="docs/screenshots/user-guide.png" alt="Startup Interactive User Guide" />
      <br />
      <b>Interactive Startup Guide (Minimizes to Top-Right Button)</b>
    </td>
    <td width="50%" align="center">
      <img src="docs/screenshots/env-audit.png" alt="Network & Environment Audit" />
      <br />
      <b>DNS / IPv6 Audit, Stale Proxy Repair & Sanitized Report</b>
    </td>
  </tr>
</table>

---

## Downloads

Download prebuilt standalone binaries directly from **[GitHub Releases (Latest)](../../releases/latest)**:

| Platform | File | Notes |
| :--- | :--- | :--- |
| **Windows (x64)** | `AIProxyLauncher.exe` | Single-file portable executable; double-click to run, no Python required |
| **macOS (Apple Silicon)** | `AIProxyLauncher-macOS-arm64.zip` | Unzip and launch `AIProxyLauncher.app` (auto-loads macOS system root CAs) |
| **macOS (Universal Source)** | `AIProxyLauncher-macOS-Source.zip` | Works on both Intel & Apple Silicon Macs with Python 3.11+ (Tk); double-click `macos-start.command` |
| **Android (Mobile)** | `AIProxyLauncher-Android.apk` | Standalone native Flutter APK; sideload directly (see [mobile/README.md](mobile/README.md)) |
| **iOS (Mobile)** | `AIProxyLauncher-iOS-unsigned.ipa` / `XcodeProject.zip` | Ready for TrollStore / AltStore / sideloading or Xcode signing (see [mobile/README.md](mobile/README.md)) |

Detailed setup guides (Chinese): [Windows Guide](使用说明.md) ｜ [macOS Guide](macOS使用说明.md)

---

## Key Features

- **4-Layer Universal Proxy & White-Label Client Detection**
  - Out-of-the-box support for 20+ clients: Clash Verge Rev, Clash Party / Mihomo Party, JImmm, FlClash, Clash Nyanpasu, Sparkle, ClashX, Stash, Surge, v2rayN, Hiddify, NekoBox / Throne, Karing, Shadowsocks, Xray, Sing-box, and more.
  - **Directory Skeleton Fingerprinting + Universal GUI-Framework IPC Guard**: Automatically identifies rebranded/white-label FlClash, Mihomo, Sing-box, and Xray forks without brand lists (reading real display names from Windows PE metadata or macOS `Info.plist`), passively discovers FlClash-family `shared_preferences.json` ports, and detects Flutter/Electron/Tauri/Qt GUI frontends so ephemeral internal IPC sockets are never disrupted.
  - **Auto SOCKS5→HTTP Bridging + Transparent Router / Custom-TUN Fallback**: Actively probes Clash REST API / HTTP CONNECT / SOCKS5 and wildcard Named Pipes / Unix sockets; **SOCKS5-only clients** (Shadowsocks, SSH `-D`, Tor, Xray/sing-box SOCKS inbounds) are automatically verified and bridged to local HTTP via pure-stdlib RFC 1928 relay; portless custom-named TUN VPNs and **router/gateway transparent proxies** are verified directly via non-CN AI-endpoint ground-truth probes.
- **Stable Local HTTP/CONNECT Proxy Relay (`127.0.0.1:17890`, HTTP + SOCKS5 Upstreams)**
  - Toggle `⇄ 统一中继` to bind a pure-stdlib local relay ([relay.py](relay.py)) supporting both HTTP and SOCKS5 local upstreams so all launched apps share one stable local port.
  - Isolated web-app windows share a persistent browser profile so switching proxy clients never logs you out; upstream proxies hot-swap per domain in the background with `350ms` dead-upstream failover and strict `HTTP 502` on upstream loss (zero direct traffic leaks).
- **Per-Service Exit Node & True Region Detection**
  - Probes exit regions directly against AI domains (`chatgpt.com`, `claude.ai`) and extracts the exact matched outbound Clash node & RTT for each of the 12+ monitored AI endpoints, highlighting restricted regions and automatically avoiding them during selection and node repair.
- **Built-In Node Switcher, DNS/IPv6 Health Audit, Modification Rollback & Sanitized Report**
  - Browse selector groups, run concurrent latency tests, and switch nodes without leaving the launcher.
  - Detects and repairs stale proxy ports in Windows user environment variables, `.codex/.env`, `.gitconfig`, and `.npmrc` (with `.bak` backups); audits DNS Fake-IP / poisoning and global IPv6 leaks, and copies a sanitized diagnostic summary free of secrets or personal paths.
  - **One-Click Modification Rollback (Troubleshooting)**: Click `↺ 撤销修改` in the top-right header (or run `python launcher.py --rollback`) to revert external config files, Windows user environment variables, login autostart, and session proxy node/TUN changes—plus optional reset of app settings and isolated browser profiles—to quickly isolate whether an issue is caused by the launcher.
- **Android Clash Access Control & Gemini Fix (Mobile App)**
  - Detects when Android Clash-like VPN Access Control whitelists bypass the app or hide Google Gemini (`com.google.android.apps.bard`, which lacks `INTERNET` permission and is hidden by default) and system Google components (`com.google.android.gms` / `com.google.android.gsf`); supports one-tap Clash API `tun.include-package` completion (with rollback), package list / YAML copy, and web fallback (see [mobile/README.md](mobile/README.md)).
- **Zero-Subprocess Native Win32 IPC & Modern Desktop UX**
  - Replaces `powershell.exe` / `netstat.exe` polling on Windows with native `ctypes` overlapped Named Pipe I/O (`~0.4ms`) and kernel `iphlpapi.GetExtendedTcpTable` reads ([win32net.py](win32net.py)).
  - Official high-DPI icons for all 9 default AI apps + 7 built-in presets (Cursor, Windsurf, VS Code, Gemini CLI, Codex CLI, Grok, Perplexity), Light/Dark themes, startup interactive guide with top-right quick reopen, system tray, single-instance wakeup guard, login autostart (`--minimized`), and sleep/wake + system-proxy change watchdog.

---

## Run from Source & Build

```bash
# Launch UI (requires Python 3.11+ with built-in Tkinter; zero third-party packages)
python launcher.py

# Start minimized directly to system tray
python launcher.py --minimized

# Headless CLI probe check (outputs JSON)
python launcher.py --check

# Inspect or roll back all external modifications made by the launcher
python launcher.py --rollback-list
python launcher.py --rollback

# Run full unit test suite (80 tests)
python -B -m unittest -v
```

### Local Packaging

- **Windows**:
  ```powershell
  .\build-windows.ps1              # Run tests + build 新版\AIProxyLauncher.exe + refresh macOS source ZIP
  .\build-windows.ps1 -SourceOnly  # Run tests + refresh macOS source ZIP only
  ```
- **macOS**:
  ```bash
  bash build-macos.sh              # Build AIProxyLauncher.app & ZIP for current architecture
  bash build-macos.sh universal2   # Build universal2 bundle (requires Universal2 Python)
  ```

### Automated Releases (GitHub Actions)

Pushing any `v*` tag (e.g., `git tag v2.3.0 && git push origin v2.3.0`) or triggering **Build & Release** manually ([.github/workflows/release.yml](.github/workflows/release.yml)) automatically runs the full test suite, builds signed/verified Windows and macOS bundles, and publishes all assets to GitHub Releases.

---

## Security & Privacy

- All local relays and controller connections are strictly restricted to loopback (`127.0.0.1`), local Windows Named Pipes, or local Unix domain sockets—never binding `0.0.0.0` and never silently falling back to direct internet connections.
- Controller bearer secrets live in memory for the current session only and are never written to disk; copied diagnostic reports strip secrets and personal file paths automatically.

