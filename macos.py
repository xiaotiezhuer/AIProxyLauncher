"""macOS application discovery, launch and connection inspection."""
from __future__ import annotations

from pathlib import Path
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import tempfile

HOME = Path.home()
SUPPORT = HOME / "Library/Application Support"
APPLICATIONS = (Path("/Applications"), HOME / "Applications")
DATA = SUPPORT / "AIProxyLauncher"
VERGE = "io.github.clash-verge-rev.clash-verge-rev"
PARTY_NAMES = ("Jimmm", "JImmm", "jimmm", "Clash Party", "Mihomo Party", "clash-party", "mihomo-party",
               "mihomo-party-org.mihomo-party")
BROWSERS = ("Google Chrome", "Microsoft Edge", "Brave Browser", "Arc", "Vivaldi", "Chromium")
APP_NAMES = {
    "ide": ("Antigravity IDE", "Google Antigravity IDE"),
    "agent": ("Antigravity", "Google Antigravity", "Antigravity Agent"),
    "claude_desktop": ("Claude",),
    "chatgpt_desktop": ("ChatGPT", "Codex"),
    "cursor": ("Cursor",),
    "windsurf": ("Windsurf",),
    "vscode": ("Visual Studio Code",),
    "web": BROWSERS,
}
MAC_PROXY_BUNDLES = {
    "Clash Verge Rev": ("Clash Verge", "Clash Verge Rev"),
    "Clash Party": ("Clash Party", "Mihomo Party"),
    "JImmm": ("Jimmm", "JImmm"),
    "FlClash": ("FlClash",),
    "Clash Nyanpasu": ("Clash Nyanpasu",),
    "Sparkle": ("Sparkle",),
    "Clash Mi": ("Clash Mi", "ClashMi"),
    "ClashX": ("ClashX", "ClashX Pro", "ClashX Meta"),
    "Stash": ("Stash",),
    "GUI.for.Clash": ("GUI.for.Clash",),
    "v2rayN": ("v2rayN",),
    "V2rayU": ("V2rayU", "V2RayXS", "V2RayX"),
    "Hiddify": ("Hiddify",),
    "NekoBox": ("NekoBox", "NekoRay", "Throne"),
    "Karing": ("Karing",),
    "GUI.for.SingBox": ("GUI.for.SingBox", "SFM", "sing-box"),
    "Surge": ("Surge",),
    "V2Box": ("V2Box",),
    "Loon": ("Loon",),
    "Shadowsocks": ("ShadowsocksX-NG", "ShadowsocksX-NG-R8"),
}


def find_bundle(*names: str) -> Path | None:
    for name in names:
        for folder in APPLICATIONS:
            path = folder / f"{name}.app"
            if path.is_dir():
                return path
    return None


def bundle_display_name(path: Path) -> str:
    """Read CFBundleDisplayName or CFBundleName from a macOS .app bundle."""
    bundle = path if path.suffix.lower() == ".app" else next((p for p in path.parents if p.suffix.lower() == ".app"), None)
    if bundle is None:
        return ""
    try:
        with (bundle / "Contents/Info.plist").open("rb") as source:
            info = plistlib.load(source)
        for key in ("CFBundleDisplayName", "CFBundleName"):
            val = info.get(key)
            if isinstance(val, str) and val.strip() and val.strip().casefold() not in {"electron", "flutter", "app"}:
                return val.strip()[:40]
    except (OSError, ValueError, plistlib.InvalidFileException):
        pass
    return bundle.stem


def executable(path: Path) -> Path:
    if path.is_file() and path.suffix.lower() != ".app":
        return path
    try:
        with (path / "Contents/Info.plist").open("rb") as source:
            name = plistlib.load(source)["CFBundleExecutable"]
        if not isinstance(name, str) or Path(name).name != name or name in {"", ".", ".."}:
            raise ValueError
        binary = path / "Contents/MacOS" / name
        if binary.is_file():
            return binary
    except (OSError, ValueError, KeyError, plistlib.InvalidFileException):
        pass
    raise ValueError(f"找不到 {path.name} 的可执行程序，请在「路径」重新选择 .app")


def _find_cli(name: str) -> Path | None:
    binary = shutil.which(name)
    options = [Path(binary)] if binary else []
    options.extend([HOME / f".local/bin/{name}", Path(f"/opt/homebrew/bin/{name}"), Path(f"/usr/local/bin/{name}")])
    return next((path for path in options if path.is_file()), None)


def detect_paths() -> dict[str, Path | None]:
    found = {key: find_bundle(*names) for key, names in APP_NAMES.items()}
    found["code"] = _find_cli("claude")
    found["gemini_cli"] = _find_cli("gemini")
    found["codex_cli"] = _find_cli("codex")
    return found


def config_files() -> list[tuple[str, Path]]:
    clash = SUPPORT / VERGE
    roots = [
        ("Clash Verge Rev", clash),
        ("ClashX", HOME / ".config/clash"),
        ("ClashX", SUPPORT / "com.west2online.ClashX"),
        ("ClashX", SUPPORT / "com.west2online.ClashXPro"),
        ("Clash Nyanpasu", SUPPORT / "clash-nyanpasu"),
        ("FlClash", SUPPORT / "com.follow.clash"),
        ("Clash Mi", SUPPORT / "clashmi"),
        ("Stash", HOME / ".config/stash"),
        ("Stash", SUPPORT / "ws.stash.app.mac"),
        ("Mihomo", HOME / ".config/mihomo"),
        ("Clash", HOME / ".config/clash.meta"),
    ]
    files = [
        ("Clash Verge Rev", clash / "clash-verge.yaml"),
        ("Clash Nyanpasu", SUPPORT / "clash-nyanpasu/clash-config.yaml"),
        *((label, root / "config.yaml") for label, root in roots),
        *(("JImmm" if name.casefold() == "jimmm" else "Clash Party", SUPPORT / name / filename)
          for name in PARTY_NAMES for filename in ("mihomo.yaml", "work/config.yaml", "config.yaml")),
        ("Sparkle", SUPPORT / "sparkle/mihomo.yaml"),
        ("Sparkle", SUPPORT / "sparkle/work/config.yaml"),
        ("Hiddify", SUPPORT / "Hiddify/current-config.json"),
        ("Hiddify", SUPPORT / "app.hiddify.com/current-config.json"),
        ("sing-box", HOME / ".config/sing-box/config.json"),
    ]
    return files


def proxy_apps() -> dict[str, Path]:
    return {key: bundle for key, group in MAC_PROXY_BUNDLES.items() if (bundle := find_bundle(*group))}


def party_socket_label() -> str:
    """Return 'JImmm' when JImmm is installed without Clash Party, else 'Clash Party'."""
    if find_bundle("Jimmm", "JImmm") and not find_bundle("Clash Party", "Mihomo Party"):
        return "JImmm"
    return "Clash Party"


def sockets() -> list[tuple[str, Path]]:
    tmp = Path("/tmp")
    roots = [tmp, Path(tempfile.gettempdir())]
    uid = str(os.getuid()) if hasattr(os, "getuid") else ""
    party_label = party_socket_label()
    paths = [
        ("Clash Verge Rev", tmp / "verge/verge-mihomo.sock"),
        ("Clash Verge Rev", tmp / "verge/verge-mihomo-alpha.sock"),
        ("Clash Nyanpasu", tmp / "nyanpasu-mihomo.sock"),
        ("Clash Nyanpasu", tmp / "clash-nyanpasu/mihomo.sock"),
    ]
    for root in dict.fromkeys(roots):
        paths.extend(("JImmm", root / name) for name in (
            "jimmm.sock", "Jimmm.sock", "jimmm/mihomo.sock", "Jimmm/mihomo.sock",
        ))
        paths.extend((party_label, root / name) for name in (
            "mihomo.sock", "mihomo-party.sock", "mihomo-party/mihomo.sock",
            "clash-party.sock", "clash-party/mihomo.sock",
        ))
        paths.extend(("Sparkle", root / name) for name in ("sparkle.sock", "sparkle/mihomo.sock"))
        if uid:
            paths.extend((party_label, path) for path in sorted(root.glob(f"mihomo-party-{uid}-*.sock"))[:12])
            paths.extend((party_label, path) for path in sorted(root.glob(f"clash-party-{uid}-*.sock"))[:12])
        for pattern, label in (
            ("verge/*.sock", "Clash Verge Rev"),
            ("nyanpasu*.sock", "Clash Nyanpasu"),
            ("sparkle*.sock", "Sparkle"),
            ("flclash*.sock", "FlClash"),
            ("jimmm*.sock", "JImmm"),
            ("Jimmm*.sock", "JImmm"),
            ("mihomo*.sock", party_label),
            ("*clash*.sock", "Clash"),
            ("*mihomo*.sock", party_label),
            ("*sing*.sock", "sing-box"),
        ):
            try:
                paths.extend((label, path) for path in sorted(root.glob(pattern))[:8])
            except OSError:
                pass
    for name in PARTY_NAMES:
        label = "JImmm" if name.casefold() == "jimmm" else "Clash Party"
        paths.append((label, SUPPORT / name / "mihomo.sock"))
        paths.append((label, SUPPORT / name / "work/mihomo.sock"))
    return list(dict.fromkeys(paths))


def system_proxy_port() -> int | None:
    try:
        output = subprocess.check_output(["/usr/sbin/scutil", "--proxy"], text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    values = dict(re.findall(r"^\s*(\w+)\s*:\s*(\S+)\s*$", output, re.M))
    for kind in ("HTTPS", "HTTP"):
        if values.get(kind + "Enable") == "1" and values.get(kind + "Proxy") in {"127.0.0.1", "localhost", "::1"}:
            port = values.get(kind + "Port", "")
            if port.isdigit() and 1 <= int(port) <= 65535:
                return int(port)
    return None


def process_snapshot() -> dict[int, tuple[str, int]]:
    output = subprocess.check_output(["/bin/ps", "-axww", "-o", "pid=", "-o", "ppid=", "-o", "comm="],
                                     text=True, timeout=4)
    return {int(pid): (Path(command).name, int(parent))
            for pid, parent, command in re.findall(r"^\s*(\d+)\s+(\d+)\s+(.+)$", output, re.M)}


def parse_tcp_connections(output: str) -> list[tuple[int, int, str, int]]:
    pid = None
    result = []
    for row in output.splitlines():
        if row.startswith("p") and row[1:].isdigit():
            pid = int(row[1:])
        elif pid is not None and row.startswith("n") and "->" in row:
            left, right = row[1:].split("->", 1)
            remote, _, port = right.rpartition(":")
            local = left.rpartition(":")[2]
            if local.isdigit() and port.isdigit():
                result.append((pid, int(local), remote, int(port)))
    return result


def parse_listening_ports(output: str) -> list[tuple[int, str, int]]:
    """Parse `lsof -nP -a -iTCP -sTCP:LISTEN -Fpn` into (pid, bind_ip, port) for local listeners."""
    pid = None
    seen: dict[tuple[int, int], str] = {}
    local_binds = {"127.0.0.1", "0.0.0.0", "[::1]", "[::]", "::1", "::", "*"}
    for row in output.splitlines():
        if row.startswith("p") and row[1:].isdigit():
            pid = int(row[1:])
        elif pid is not None and row.startswith("n") and "->" not in row:
            endpoint = row[1:].split(" ", 1)[0]
            host, sep, port_text = endpoint.rpartition(":")
            if sep and host in local_binds and port_text.isdigit():
                port = int(port_text)
                if 1 <= port <= 65535:
                    key = (pid, port)
                    if key not in seen or host in {"127.0.0.1", "[::1]"}:
                        seen[key] = host
    return [(p, ip, port) for (p, port), ip in seen.items()]


def tcp_connections() -> list[tuple[int, int, str, int]]:
    result = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-iTCP", "-sTCP:ESTABLISHED", "-Fpn"],
                            capture_output=True, text=True, timeout=5)
    if result.returncode not in {0, 1}:
        raise OSError("无法读取 macOS 活动连接")
    return parse_tcp_connections(result.stdout)


def listening_tcp_ports() -> list[tuple[int, str, int]]:
    try:
        result = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-iTCP", "-sTCP:LISTEN", "-Fpn"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode in {0, 1}:
            return parse_listening_ports(result.stdout)
    except (OSError, subprocess.SubprocessError):
        pass
    return []


def parse_mac_tun_routes(output: str) -> bool:
    """Detect active IPv4 utun proxy routes from `netstat -rn -f inet` output."""
    split_zero: set[str] = set()
    split_high: set[str] = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        dest = parts[0]
        iface = next((token for token in parts[2:] if re.fullmatch(r"utun\d+", token)), "")
        if not iface:
            continue
        if dest == "default" or dest.startswith("198.18"):
            return True
        if dest in {"0/1", "0.0.0.0/1"}:
            split_zero.add(iface)
        elif dest in {"128.0/1", "128.0.0.0/1"}:
            split_high.add(iface)
    return bool(split_zero & split_high)


def detect_system_tun() -> bool:
    try:
        output = subprocess.check_output(["/usr/sbin/netstat", "-rn", "-f", "inet"], text=True, timeout=4)
        return parse_mac_tun_routes(output)
    except (OSError, subprocess.SubprocessError):
        return False


def clash_service_hint() -> str:
    try:
        result = subprocess.run(["/bin/launchctl", "print", f"system/{VERGE}.service"], capture_output=True,
                                text=True, timeout=3)
        if result.returncode == 0 and re.search(r"\bstate\s*=\s*running\b", result.stdout):
            return "Clash 后台服务正在运行。请在 Clash 中检查服务模式与 TUN 权限，再重试。"
    except (OSError, subprocess.SubprocessError):
        pass
    return "请在 Clash Verge Rev 中安装或修复服务模式，并按 macOS 提示授权后重试。"


def open_app(path: Path) -> None:
    subprocess.run(["/usr/bin/open", "-a", str(path)], check=True, timeout=8)


def focus_app(path: Path | None) -> bool:
    if not path:
        return False
    try:
        open_app(path)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def close_app(path: Path | None, timeout: float = 5.0) -> bool:
    """Gracefully ask a macOS .app to quit and wait until it exits."""
    import time
    if not path:
        return False
    try:
        binary = executable(path)
    except ValueError:
        return False
    if not application_running(path, binary):
        return True
    app_name = path.stem.replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.run(
            ["/usr/bin/osascript", "-e", f'tell application "{app_name}" to quit'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not application_running(path, binary):
            return True
        time.sleep(0.25)
    return not application_running(path, binary)


def application_running(path: Path, binary: Path) -> bool:
    names = {path.stem.casefold(), binary.name.casefold()} - {"electron", "app"}
    if any(name.casefold() in names for name, _ in process_snapshot().values()):
        return True
    pattern = "^" + re.escape(str(binary)) + "([[:space:]]|$)"
    return subprocess.run(["/usr/bin/pgrep", "-f", "--", pattern], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=4).returncode == 0


def launch(target, path: Path | None, port: int, data_dir: Path, check_running: bool = True,
           *, stable_profile: bool = False) -> None:
    from launcher import AlreadyRunningError, chromium_args, proxy_env, with_codex_proxy

    if not path:
        raise ValueError("找不到程序，请先选择应用路径")
    binary = executable(path)
    if target.kind == "codex":
        if check_running and application_running(path, binary):
            raise AlreadyRunningError("ChatGPT / Codex 已在运行。请保存工作，按 ⌘Q 退出，再从启动器打开。")
        if port > 0:
            with_codex_proxy(port, lambda: open_app(path), data_dir=data_dir)
        else:
            open_app(path)
        return
    if target.kind == "store":
        open_app(path)
        return
    args = [str(binary)]
    if target.kind == "electron":
        if check_running and application_running(path, binary):
            raise AlreadyRunningError("应用已在运行。请保存工作，按 ⌘Q 退出，再从启动器打开。")
        args.extend(chromium_args(port))
    elif target.kind == "web":
        if path.stem not in BROWSERS and binary.name not in BROWSERS:
            raise ValueError("请选择 Chrome、Edge、Brave、Arc 或 Vivaldi 等 Chromium 浏览器")
        profile_name = f"{path.stem}-tun" if port <= 0 else (f"{path.stem}-relay" if stable_profile else f"{path.stem}-{port}")
        profile = data_dir / "Browser" / profile_name
        profile.mkdir(parents=True, exist_ok=True)
        args.extend(chromium_args(port))
        args.extend([f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", f"--app={target.url}"])
    elif target.kind == "cli":
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".command", encoding="utf-8", dir=data_dir, delete=False) as file:
            script = Path(file.name)
            command = shlex.join(["/usr/bin/env", *[f"{key}={value}" for key, value in proxy_env(port, {}).items()], str(binary)])
            file.write(f"#!/bin/sh\n/bin/rm -f -- {shlex.quote(str(script))}\nexec {command}\n")
        script.chmod(0o700)
        try:
            subprocess.Popen(["/usr/bin/open", "-a", "Terminal", str(script)])
        except OSError:
            script.unlink(missing_ok=True)
            raise
        return
    subprocess.Popen(args, env=proxy_env(port), cwd=str(path.parent), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
