"""Linux application discovery, /proc inspection, DE proxy detection, TUN routing, and launch."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time

HOME = Path.home()
XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")
DATA = XDG_DATA_HOME / "AIProxyLauncher"

VERGE = "io.github.clash-verge-rev.clash-verge-rev"
PARTY_NAMES = (
    "mihomo-party", "clash-party", "Clash Party", "Mihomo Party",
    "jimmm", "Jimmm", "JImmm",
)

SUPPORTED_LINUX_BROWSERS: dict[str, str] = {
    "google-chrome": "Google Chrome",
    "google-chrome-stable": "Google Chrome",
    "google-chrome-beta": "Google Chrome",
    "google-chrome-unstable": "Google Chrome",
    "chrome": "Google Chrome",
    "chromium": "Chromium",
    "chromium-browser": "Chromium",
    "microsoft-edge": "Microsoft Edge",
    "microsoft-edge-stable": "Microsoft Edge",
    "microsoft-edge-beta": "Microsoft Edge",
    "microsoft-edge-dev": "Microsoft Edge",
    "msedge": "Microsoft Edge",
    "brave-browser": "Brave",
    "brave-browser-stable": "Brave",
    "brave-browser-beta": "Brave",
    "brave": "Brave",
    "vivaldi": "Vivaldi",
    "vivaldi-stable": "Vivaldi",
    "vivaldi-snapshot": "Vivaldi",
    "vivaldi-bin": "Vivaldi",
    "opera": "Opera",
    "opera-stable": "Opera",
}

BROWSER_PROCESS_NAMES: tuple[str, ...] = tuple(dict.fromkeys([
    *SUPPORTED_LINUX_BROWSERS.keys(),
    "chromium-bin", "chrome-sandbox", "brave-bin", "msedge-bin",
]))

APP_COMMANDS: dict[str, tuple[str, ...]] = {
    "ide": ("antigravity-ide", "antigravity", "Antigravity IDE"),
    "agent": ("antigravity", "antigravity-agent", "Antigravity"),
    "cursor": ("cursor", "Cursor"),
    "windsurf": ("windsurf", "Windsurf"),
    "vscode": ("code", "code-insiders", "codium"),
    "claude_desktop": ("claude-desktop", "claude-desktop-native", "claude-desktop-bin"),
    "chatgpt_desktop": ("chatgpt", "chatgpt-desktop", "codex-desktop"),
}

APP_DESKTOP_TOKENS: dict[str, tuple[str, ...]] = {
    "ide": ("antigravity ide", "google antigravity ide"),
    "agent": ("antigravity", "google antigravity", "antigravity agent"),
    "cursor": ("cursor",),
    "windsurf": ("windsurf",),
    "vscode": ("visual studio code", "code - oss", "vscodium"),
    "claude_desktop": ("claude",),
    "chatgpt_desktop": ("chatgpt", "codex"),
}

APP_PROCESS_NAMES: dict[str, tuple[str, ...]] = {
    "ide": ("antigravity-ide", "antigravity", "Antigravity IDE"),
    "agent": ("antigravity", "antigravity-agent", "Antigravity"),
    "code": ("claude",),
    "claude_desktop": ("claude-desktop", "claude-desktop-native", "claude"),
    "chatgpt_desktop": ("chatgpt", "chatgpt-desktop", "codex"),
    "cursor": ("cursor",),
    "windsurf": ("windsurf",),
    "vscode": ("code", "code-insiders", "codium"),
    "gemini_cli": ("gemini",),
    "codex_cli": ("codex",),
}

LINUX_PROXY_BINS: dict[str, tuple[str, ...]] = {
    "Clash Verge Rev": ("clash-verge", "clash-verge-rev"),
    "Clash Party": ("mihomo-party", "clash-party"),
    "JImmm": ("jimmm", "Jimmm"),
    "FlClash": ("flclash", "FlClash"),
    "Clash Nyanpasu": ("clash-nyanpasu",),
    "Sparkle": ("sparkle",),
    "Clash Mi": ("clashmi", "clash-mi"),
    "Clash for Windows": ("cfw", "clash-for-windows"),
    "GUI.for.Clash": ("GUI.for.Clash", "gui-for-clash"),
    "v2rayN": ("v2rayN", "v2rayn"),
    "v2rayA": ("v2raya",),
    "Hiddify": ("hiddify", "Hiddify"),
    "NekoBox": ("nekobox", "nekoray", "throne", "Throne"),
    "Karing": ("karing", "Karing"),
    "GUI.for.SingBox": ("GUI.for.SingBox", "gui-for-singbox"),
    "Shadowsocks": ("sslocal", "ss-local"),
    "dae": ("dae", "daed"),
    "Mihomo": ("mihomo", "mihomo-alpha", "clash-meta", "clash"),
    "sing-box": ("sing-box",),
    "Xray": ("xray", "v2ray"),
    "Privoxy": ("privoxy", "hysteria", "naive", "gost"),
}

LINUX_PROXY_DESKTOP_TOKENS: dict[str, tuple[str, ...]] = {
    "Clash Verge Rev": ("clash verge", "clash verge rev"),
    "Clash Party": ("clash party", "mihomo party"),
    "JImmm": ("jimmm",),
    "FlClash": ("flclash",),
    "Clash Nyanpasu": ("clash nyanpasu",),
    "Sparkle": ("sparkle",),
    "Clash Mi": ("clash mi", "clashmi"),
    "GUI.for.Clash": ("gui.for.clash",),
    "v2rayN": ("v2rayn",),
    "v2rayA": ("v2raya",),
    "Hiddify": ("hiddify", "hiddify next"),
    "NekoBox": ("nekobox", "nekoray", "throne"),
    "Karing": ("karing",),
    "GUI.for.SingBox": ("gui.for.singbox",),
    "dae": ("dae", "daed"),
}

DESKTOP_DIRS: tuple[Path, ...] = (
    XDG_DATA_HOME / "applications",
    Path("/usr/local/share/applications"),
    Path("/usr/share/applications"),
    XDG_DATA_HOME / "flatpak/exports/share/applications",
    Path("/var/lib/flatpak/exports/share/applications"),
    Path("/var/lib/snapd/desktop/applications"),
)

APPIMAGE_DIRS: tuple[Path, ...] = (
    HOME / "Applications",
    HOME / "AppImages",
    HOME / ".local/bin",
    Path("/opt"),
)

TERMINAL_CANDIDATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("x-terminal-emulator", ("-e",)),
    ("gnome-terminal", ("--",)),
    ("konsole", ("-e",)),
    ("xfce4-terminal", ("-x",)),
    ("kitty", ("--",)),
    ("alacritty", ("-e",)),
    ("wezterm", ("start", "--")),
    ("ghostty", ("-e",)),
    ("foot", ("--",)),
    ("tilix", ("-e",)),
    ("mate-terminal", ("-x",)),
    ("lxterminal", ("-e",)),
    ("terminator", ("-x",)),
    ("urxvt", ("-e",)),
    ("xterm", ("-e",)),
)

_TUN_IFACE_PATTERN = re.compile(
    r"^(?:tun\d*|utun\d*|meta|mihomo\w*|clash\w*|sing[-_]?tun\w*|sing[-_]?box\w*|"
    r"flclash\w*|hiddify\w*|nekobox\w*|nekoray\w*|karing\w*|wg\d*|tailscale\d*|dae\w*)$",
    re.I,
)
_EXCLUDED_IFACE_PREFIXES = ("lo", "docker", "br-", "veth", "virbr", "lxc", "podman", "cni", "flannel", "cali")
_GENERIC_DESKTOP_NAMES = {"", "electron", "flutter", "chromium", "app", "application", "python", "python3"}


def _desktop_dirs() -> tuple[Path, ...]:
    return DESKTOP_DIRS


def parse_desktop_entry(text: str) -> dict[str, str]:
    """Parse the [Desktop Entry] section of a Freedesktop .desktop file."""
    in_entry = False
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            if in_entry:
                break
            in_entry = line == "[Desktop Entry]"
            continue
        if in_entry and "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            if key not in result:
                result[key] = val.strip()
    return result


def resolve_desktop_exec(exec_value: str) -> Path | None:
    """Extract the primary executable Path from a .desktop Exec= field."""
    if not exec_value:
        return None
    try:
        tokens = shlex.split(exec_value)
    except ValueError:
        tokens = exec_value.split()
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if tok in {"env", "/usr/bin/env"}:
            idx += 1
            continue
        if "=" in tok and not tok.startswith("/") and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            idx += 1
            continue
        break
    if idx >= len(tokens):
        return None
    cmd = tokens[idx]
    if cmd.startswith("%"):
        return None
    candidate = Path(cmd)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    resolved = shutil.which(cmd)
    return Path(resolved) if resolved else None


def _iter_desktop_entries(dirs: tuple[Path, ...] | None = None) -> list[tuple[dict[str, str], Path]]:
    entries: list[tuple[dict[str, str], Path]] = []
    for folder in (dirs if dirs is not None else _desktop_dirs()):
        if not folder.is_dir():
            continue
        try:
            files = sorted(folder.glob("*.desktop"))
        except OSError:
            continue
        for desktop_file in files[:400]:
            try:
                parsed = parse_desktop_entry(desktop_file.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if not parsed or parsed.get("Hidden", "").lower() == "true":
                continue
            entries.append((parsed, desktop_file))
    return entries


def find_desktop_app(tokens: tuple[str, ...], *, dirs: tuple[Path, ...] | None = None) -> Path | None:
    folded_tokens = tuple(t.casefold() for t in tokens)
    for parsed, _desktop_file in _iter_desktop_entries(dirs):
        names = [
            parsed.get("Name", "").strip().casefold(),
            parsed.get("StartupWMClass", "").strip().casefold(),
        ]
        if any(n and (n == tok or n.startswith(tok + " ")) for n in names for tok in folded_tokens):
            exe = resolve_desktop_exec(parsed.get("Exec", ""))
            if exe and exe.is_file():
                return exe
    return None


def desktop_display_name(exe_path: Path, *, dirs: tuple[Path, ...] | None = None) -> str:
    """Resolve friendly display name from matching Freedesktop .desktop entry."""
    try:
        resolved_target = exe_path.resolve(strict=False)
    except OSError:
        resolved_target = exe_path
    target_name = exe_path.name.casefold()
    for parsed, _desktop_file in _iter_desktop_entries(dirs):
        exe = resolve_desktop_exec(parsed.get("Exec", ""))
        if not exe:
            continue
        try:
            same = exe.resolve(strict=False) == resolved_target or exe.name.casefold() == target_name
        except OSError:
            same = exe.name.casefold() == target_name
        if same:
            name = (parsed.get("Name") or "").strip()
            if name and name.casefold() not in _GENERIC_DESKTOP_NAMES:
                return name[:40]
    return ""


def _find_binary(*names: str) -> Path | None:
    extra_dirs = (
        HOME / ".local/bin",
        HOME / ".npm-global/bin",
        Path("/usr/local/bin"),
        Path("/usr/bin"),
        Path("/snap/bin"),
    )
    for name in names:
        if resolved := shutil.which(name):
            p = Path(resolved)
            if p.is_file():
                return p
        for folder in extra_dirs:
            cand = folder / name
            try:
                if cand.is_file() and os.access(cand, os.X_OK):
                    return cand
            except OSError:
                continue
    return None


def _find_appimage(*prefixes: str) -> Path | None:
    lowered = tuple(p.casefold() for p in prefixes)
    for folder in APPIMAGE_DIRS:
        if not folder.is_dir():
            continue
        try:
            for item in sorted(folder.iterdir())[:120]:
                low = item.name.casefold()
                if low.endswith(".appimage") and any(low.startswith(pref) for pref in lowered):
                    if item.is_file():
                        return item
        except OSError:
            continue
    return None


def is_supported_browser(path: Path) -> bool:
    name = path.name.casefold()
    if name in SUPPORTED_LINUX_BROWSERS:
        return True
    return any(tok in name for tok in ("chrome", "chromium", "edge", "brave", "vivaldi", "opera"))


def browser_display_name(path: Path) -> str:
    name = path.name.casefold()
    if name in SUPPORTED_LINUX_BROWSERS:
        return SUPPORTED_LINUX_BROWSERS[name]
    for token, label in (
        ("edge", "Microsoft Edge"),
        ("chrome", "Google Chrome"),
        ("chromium", "Chromium"),
        ("brave", "Brave"),
        ("vivaldi", "Vivaldi"),
        ("opera", "Opera"),
    ):
        if token in name:
            return label
    return path.stem


def detect_paths() -> dict[str, Path | None]:
    found: dict[str, Path | None] = {}
    for key, commands in APP_COMMANDS.items():
        path = (
            _find_binary(*commands)
            or find_desktop_app(APP_DESKTOP_TOKENS.get(key, ()))
            or _find_appimage(*commands)
        )
        found[key] = path
    found["code"] = _find_binary("claude")
    found["gemini_cli"] = _find_binary("gemini")
    found["codex_cli"] = _find_binary("codex")
    found["web"] = (
        _find_binary(*SUPPORTED_LINUX_BROWSERS.keys())
        or find_desktop_app(("google chrome", "chromium", "microsoft edge", "brave", "vivaldi", "opera"))
    )
    return found


def proxy_apps() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for name, bins in LINUX_PROXY_BINS.items():
        hit = (
            _find_binary(*bins)
            or find_desktop_app(LINUX_PROXY_DESKTOP_TOKENS.get(name, ()))
            or _find_appimage(*bins)
        )
        if hit:
            found[name] = hit
    return found


def config_files() -> list[tuple[str, Path]]:
    verge_data = XDG_DATA_HOME / VERGE
    verge_cfg = XDG_CONFIG_HOME / "clash-verge"
    files: list[tuple[str, Path]] = [
        ("Clash Verge Rev", verge_data / "clash-verge.yaml"),
        ("Clash Verge Rev", verge_data / "config.yaml"),
        ("Clash Verge Rev", verge_cfg / "clash-verge.yaml"),
        ("Clash Verge Rev", verge_cfg / "config.yaml"),
        ("Clash Nyanpasu", XDG_CONFIG_HOME / "clash-nyanpasu/clash-config.yaml"),
        ("Clash Nyanpasu", XDG_CONFIG_HOME / "clash-nyanpasu/config.yaml"),
        ("Clash Nyanpasu", XDG_DATA_HOME / "clash-nyanpasu/clash-config.yaml"),
        ("FlClash", XDG_CONFIG_HOME / "com.follow.clash/config.yaml"),
        ("FlClash", XDG_DATA_HOME / "com.follow.clash/config.yaml"),
        ("FlClash", XDG_CONFIG_HOME / "flclash/config.yaml"),
        ("Clash Mi", XDG_CONFIG_HOME / "clashmi/config.yaml"),
        ("Clash Mi", XDG_DATA_HOME / "clashmi/config.yaml"),
        ("Clash for Windows", XDG_CONFIG_HOME / "clash/config.yaml"),
        ("Clash", XDG_CONFIG_HOME / "clash.meta/config.yaml"),
        ("Mihomo", XDG_CONFIG_HOME / "mihomo/config.yaml"),
        ("Mihomo", Path("/etc/mihomo/config.yaml")),
        ("Sparkle", XDG_CONFIG_HOME / "sparkle/mihomo.yaml"),
        ("Sparkle", XDG_CONFIG_HOME / "sparkle/work/config.yaml"),
        ("Sparkle", XDG_CONFIG_HOME / "sparkle/config.yaml"),
        ("Hiddify", XDG_CONFIG_HOME / "hiddify/current-config.json"),
        ("Hiddify", XDG_DATA_HOME / "hiddify/current-config.json"),
        ("Hiddify", XDG_DATA_HOME / "app.hiddify.com/current-config.json"),
        ("sing-box", XDG_CONFIG_HOME / "sing-box/config.json"),
        ("sing-box", Path("/etc/sing-box/config.json")),
    ]
    for name in PARTY_NAMES:
        label = "JImmm" if name.casefold() == "jimmm" else "Clash Party"
        for filename in ("mihomo.yaml", "work/config.yaml", "config.yaml"):
            files.append((label, XDG_CONFIG_HOME / name / filename))
            files.append((label, XDG_DATA_HOME / name / filename))
    return files


def party_socket_label() -> str:
    if _find_binary("jimmm", "Jimmm") and not _find_binary("mihomo-party", "clash-party"):
        return "JImmm"
    return "Clash Party"


def sockets() -> list[tuple[str, Path]]:
    tmp = Path("/tmp")
    roots: list[Path] = [tmp, Path(tempfile.gettempdir())]
    if runtime_dir := os.environ.get("XDG_RUNTIME_DIR"):
        roots.append(Path(runtime_dir))
    uid = str(os.getuid()) if hasattr(os, "getuid") else ""
    party_label = party_socket_label()
    paths: list[tuple[str, Path]] = [
        ("Clash Verge Rev", tmp / "verge/verge-mihomo.sock"),
        ("Clash Verge Rev", tmp / "verge/verge-mihomo-alpha.sock"),
        ("Clash Verge Rev", Path("/run/clash-verge-rev/verge-mihomo.sock")),
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
            try:
                paths.extend((party_label, p) for p in sorted(root.glob(f"mihomo-party-{uid}-*.sock"))[:12])
                paths.extend((party_label, p) for p in sorted(root.glob(f"clash-party-{uid}-*.sock"))[:12])
            except OSError:
                pass
        for pattern, label in (
            ("verge/*.sock", "Clash Verge Rev"),
            ("nyanpasu*.sock", "Clash Nyanpasu"),
            ("sparkle*.sock", "Sparkle"),
            ("flclash*.sock", "FlClash"),
            ("jimmm*.sock", "JImmm"),
            ("mihomo*.sock", party_label),
            ("*clash*.sock", "Clash"),
            ("*mihomo*.sock", party_label),
            ("*sing*.sock", "sing-box"),
        ):
            try:
                paths.extend((label, p) for p in sorted(root.glob(pattern))[:8])
            except OSError:
                pass
    for name in PARTY_NAMES:
        label = "JImmm" if name.casefold() == "jimmm" else "Clash Party"
        paths.append((label, XDG_CONFIG_HOME / name / "mihomo.sock"))
        paths.append((label, XDG_CONFIG_HOME / name / "work/mihomo.sock"))
    return list(dict.fromkeys(paths))


# ── System Proxy Detection (GNOME / KDE) ────────────────────────────────


def parse_kde_kioslaverc(text: str) -> int | None:
    """Parse KDE Plasma ~/.config/kioslaverc [Proxy Settings] for local manual HTTP/HTTPS proxy port."""
    in_section = False
    settings: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_section = line.lower() == "[proxy settings]"
            continue
        if in_section and "=" in line:
            key, _, val = line.partition("=")
            settings[key.strip().lower()] = val.strip()
    if settings.get("proxytype") != "1":
        return None
    for key in ("httpsproxy", "httpproxy"):
        raw = settings.get(key, "")
        if not raw:
            continue
        # KDE stores either "http://127.0.0.1 7890" (space) or "http://127.0.0.1:7890" (colon)
        m = re.fullmatch(r"(?:https?://)?(?:127\.0\.0\.1|localhost|\[::1\])[:\s]+([0-9]{1,5})/?", raw, re.I)
        if m and 1 <= int(m[1]) <= 65535:
            return int(m[1])
    return None


def _gnome_system_proxy_port() -> int | None:
    gsettings = shutil.which("gsettings")
    if not gsettings:
        return None
    try:
        mode = subprocess.check_output(
            [gsettings, "get", "org.gnome.system.proxy", "mode"],
            text=True, stderr=subprocess.DEVNULL, timeout=2,
        ).strip().strip("'\"")
        if mode != "manual":
            return None
        for proto in ("https", "http"):
            host = subprocess.check_output(
                [gsettings, "get", f"org.gnome.system.proxy.{proto}", "host"],
                text=True, stderr=subprocess.DEVNULL, timeout=2,
            ).strip().strip("'\"")
            if host not in {"127.0.0.1", "localhost", "::1"}:
                continue
            port_text = subprocess.check_output(
                [gsettings, "get", f"org.gnome.system.proxy.{proto}", "port"],
                text=True, stderr=subprocess.DEVNULL, timeout=2,
            ).strip().split()[-1]
            if port_text.isdigit() and 1 <= int(port_text) <= 65535:
                return int(port_text)
    except (OSError, subprocess.SubprocessError, IndexError):
        return None
    return None


def system_proxy_port() -> int | None:
    port = _gnome_system_proxy_port()
    if port:
        return port
    kio = XDG_CONFIG_HOME / "kioslaverc"
    if kio.is_file():
        try:
            port = parse_kde_kioslaverc(kio.read_text(encoding="utf-8", errors="replace"))
            if port:
                return port
        except OSError:
            pass
    return None


# ── Pure-/proc Kernel Process & TCP Inspection ──────────────────────────


def process_executable_path(pid: int) -> Path | None:
    if pid <= 0:
        return None
    try:
        target = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None
    if target.endswith(" (deleted)"):
        target = target[:-10]
    path = Path(target)
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def _proc_name(pid: int, stat_comm: str) -> str:
    """Resolve full untruncated process name on Linux (avoiding 15-char /proc/pid/comm truncation)."""
    exe = process_executable_path(pid)
    if exe and exe.name and not exe.name.startswith("ld-linux"):
        if exe.name.casefold() not in {"python", "python3", "sh", "bash", "dash", "env"}:
            return exe.name
    try:
        raw_cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
        if raw_cmd:
            argv0 = raw_cmd.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
            if argv0:
                base = Path(argv0).name
                if base and base.casefold() not in {"sh", "bash", "env"}:
                    return base
    except OSError:
        pass
    return stat_comm


def parse_proc_stat(stat_text: str) -> tuple[str, int] | None:
    """Parse /proc/<pid>/stat safely even when comm contains spaces or ')'."""
    lparen = stat_text.find("(")
    rparen = stat_text.rfind(")")
    if lparen < 0 or rparen <= lparen:
        return None
    comm = stat_text[lparen + 1:rparen]
    fields = stat_text[rparen + 2:].split()
    if len(fields) < 2 or not fields[1].lstrip("-").isdigit():
        return None
    return comm, int(fields[1])


def process_snapshot() -> dict[int, tuple[str, int]]:
    proc_root = Path("/proc")
    if proc_root.is_dir():
        processes: dict[int, tuple[str, int]] = {}
        try:
            entries = os.listdir(proc_root)
        except OSError:
            entries = []
        for entry in entries:
            if not entry.isdigit():
                continue
            pid = int(entry)
            try:
                stat_text = (proc_root / entry / "stat").read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parsed = parse_proc_stat(stat_text)
            if not parsed:
                continue
            comm, ppid = parsed
            processes[pid] = (_proc_name(pid, comm), ppid)
        if processes:
            return processes
    output = subprocess.check_output(["/bin/ps", "-eo", "pid=,ppid=,comm="], text=True, timeout=4)
    return {
        int(pid): (Path(command.strip()).name, int(parent))
        for pid, parent, command in re.findall(r"^\s*(\d+)\s+(\d+)\s+(.+)$", output, re.M)
    }


def _decode_proc_ipv4(hex_addr: str, byteorder: str = sys.byteorder) -> str:
    fmt = "<I" if byteorder == "little" else ">I"
    return socket.inet_ntop(socket.AF_INET, struct.pack(fmt, int(hex_addr, 16)))


def _decode_proc_ipv6(hex_addr: str, byteorder: str = sys.byteorder) -> str:
    fmt = "<4I" if byteorder == "little" else ">4I"
    words = [int(hex_addr[i:i + 8], 16) for i in range(0, 32, 8)]
    ip = socket.inet_ntop(socket.AF_INET6, struct.pack(fmt, *words))
    # Normalize IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1 -> 127.0.0.1) for consistent route matching
    if ip.lower().startswith("::ffff:"):
        mapped = ip[7:]
        if "." in mapped:
            return mapped
    return f"[{ip}]"


def parse_proc_net_tcp(
    text: str,
    *,
    ipv6: bool = False,
    inode_to_pid: dict[str, int] | None = None,
    byteorder: str = sys.byteorder,
) -> tuple[list[tuple[int, int, str, int]], list[tuple[int, str, int]]]:
    """Parse Linux /proc/net/tcp or /proc/net/tcp6 into (established, listening) socket tuples."""
    established: list[tuple[int, int, str, int]] = []
    listening: list[tuple[int, str, int]] = []
    inodes = inode_to_pid or {}
    local_binds = {"127.0.0.1", "0.0.0.0", "[::1]", "[::]"}
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 10:
            continue
        local_raw, remote_raw, state, inode = parts[1], parts[2], parts[3].upper(), parts[9]
        if ":" not in local_raw or ":" not in remote_raw:
            continue
        l_hex, l_port_hex = local_raw.split(":", 1)
        r_hex, r_port_hex = remote_raw.split(":", 1)
        expected_len = 32 if ipv6 else 8
        if len(l_hex) != expected_len or len(r_hex) != expected_len:
            continue
        try:
            local_port = int(l_port_hex, 16)
            remote_port = int(r_port_hex, 16)
            local_ip = _decode_proc_ipv6(l_hex, byteorder) if ipv6 else _decode_proc_ipv4(l_hex, byteorder)
            remote_ip = _decode_proc_ipv6(r_hex, byteorder) if ipv6 else _decode_proc_ipv4(r_hex, byteorder)
        except (ValueError, OSError):
            continue
        pid = inodes.get(inode, 0)
        if state == "01":  # TCP_ESTABLISHED
            if pid > 0:
                established.append((pid, local_port, remote_ip, remote_port))
        elif state == "0A":  # TCP_LISTEN
            if local_ip in local_binds and 1 <= local_port <= 65535:
                listening.append((pid, local_ip, local_port))
    return established, listening


def _build_socket_inode_map() -> dict[str, int]:
    inode_to_pid: dict[str, int] = {}
    proc_root = Path("/proc")
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return inode_to_pid
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        fd_dir = proc_root / entry / "fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink(fd_dir / fd)
            except OSError:
                continue
            if link.startswith("socket:[") and link.endswith("]"):
                inode_to_pid[link[8:-1]] = pid
    return inode_to_pid


def _read_proc_tcp_tables() -> tuple[list[tuple[int, int, str, int]], list[tuple[int, str, int]]]:
    inode_map = _build_socket_inode_map()
    established: list[tuple[int, int, str, int]] = []
    seen_listening: dict[tuple[int, int], str] = {}
    for path_str, is_v6 in (("/proc/net/tcp", False), ("/proc/net/tcp6", True)):
        p = Path(path_str)
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="ascii", errors="replace")
        except OSError:
            continue
        est, lis = parse_proc_net_tcp(text, ipv6=is_v6, inode_to_pid=inode_map)
        established.extend(est)
        for pid, ip, port in lis:
            key = (pid, port)
            if key not in seen_listening or ip in {"127.0.0.1", "[::1]"}:
                seen_listening[key] = ip
    listening = [(pid, ip, port) for (pid, port), ip in seen_listening.items()]
    return established, listening


def tcp_connections() -> list[tuple[int, int, str, int]]:
    est, _ = _read_proc_tcp_tables()
    return est


def listening_tcp_ports() -> list[tuple[int, str, int]]:
    _, lis = _read_proc_tcp_tables()
    return lis


# ── Linux TUN Route Detection (Main + Policy Routing Tables) ────────────


def _iface_is_tun(iface: str, tun_ifaces: set[str] | None = None) -> bool:
    if not iface:
        return False
    low = iface.lower()
    if any(low == pref or low.startswith(pref) for pref in _EXCLUDED_IFACE_PREFIXES):
        return False
    if tun_ifaces is not None and iface in tun_ifaces:
        return True
    if _TUN_IFACE_PATTERN.fullmatch(iface):
        return True
    return False


def _sysfs_tun_interfaces() -> set[str]:
    found: set[str] = set()
    net_root = Path("/sys/class/net")
    if not net_root.is_dir():
        return found
    try:
        for iface_dir in net_root.iterdir():
            if (iface_dir / "tun_flags").is_file():
                # Verify interface is administratively UP (IFF_UP = 0x1)
                try:
                    flags = int((iface_dir / "flags").read_text(encoding="ascii").strip(), 0)
                    if flags & 0x1:
                        found.add(iface_dir.name)
                except (OSError, ValueError):
                    found.add(iface_dir.name)
    except OSError:
        pass
    return found


def parse_linux_ip_routes(output: str, tun_ifaces: set[str] | None = None) -> bool:
    """Detect active TUN routes from `ip -4 route show table all` output."""
    split_zero: set[str] = set()
    split_high: set[str] = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("local ", "broadcast ", "unreachable ", "blackhole ", "prohibit ")):
            continue
        parts = line.split()
        if len(parts) < 3 or "dev" not in parts:
            continue
        dev_idx = parts.index("dev")
        if dev_idx + 1 >= len(parts):
            continue
        dest = parts[0]
        iface = parts[dev_idx + 1]
        if not _iface_is_tun(iface, tun_ifaces):
            continue
        if dest == "default" or dest.startswith(("198.18.", "198.19.")):
            return True
        if dest == "0.0.0.0/1":
            split_zero.add(iface)
        elif dest == "128.0.0.0/1":
            split_high.add(iface)
    return bool(split_zero & split_high)


def parse_proc_net_route(text: str, tun_ifaces: set[str] | None = None) -> bool:
    """Fallback parser for /proc/net/route when `ip` command is unavailable."""
    split_zero: set[str] = set()
    split_high: set[str] = set()
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 8:
            continue
        iface, dest_hex, flags_hex, mask_hex = parts[0], parts[1], parts[3], parts[7]
        try:
            flags = int(flags_hex, 16)
            if not (flags & 0x1):  # RTF_UP
                continue
            dest = _decode_proc_ipv4(dest_hex)
            mask = _decode_proc_ipv4(mask_hex)
        except (ValueError, OSError):
            continue
        if not _iface_is_tun(iface, tun_ifaces):
            continue
        if (dest == "0.0.0.0" and mask == "0.0.0.0") or dest.startswith(("198.18.", "198.19.")):
            return True
        if dest == "0.0.0.0" and mask == "128.0.0.0":
            split_zero.add(iface)
        elif dest == "128.0.0.0" and mask == "128.0.0.0":
            split_high.add(iface)
    return bool(split_zero & split_high)


def detect_system_tun() -> bool:
    tun_ifaces = _sysfs_tun_interfaces()
    ip_bin = shutil.which("ip") or ("/usr/sbin/ip" if Path("/usr/sbin/ip").is_file() else "/sbin/ip")
    if ip_bin and Path(ip_bin).is_file():
        try:
            output = subprocess.check_output(
                [ip_bin, "-4", "route", "show", "table", "all"],
                text=True, stderr=subprocess.DEVNULL, timeout=4,
            )
            return parse_linux_ip_routes(output, tun_ifaces)
        except (OSError, subprocess.SubprocessError):
            pass
    proc_route = Path("/proc/net/route")
    if proc_route.is_file():
        try:
            return parse_proc_net_route(proc_route.read_text(encoding="ascii", errors="replace"), tun_ifaces)
        except OSError:
            pass
    return False


def clash_service_hint() -> str:
    systemctl = shutil.which("systemctl")
    if systemctl:
        for unit in ("clash-verge-service.service", "clash-verge-service"):
            try:
                res = subprocess.run(
                    [systemctl, "is-active", unit],
                    capture_output=True, text=True, timeout=3,
                )
                if res.returncode == 0 and res.stdout.strip() == "active":
                    return "Clash Verge systemd 服务正在运行。请在 Clash Verge Rev 中检查服务模式与 TUN 权限后重试。"
            except (OSError, subprocess.SubprocessError):
                pass
    return "请在 Clash Verge Rev 中安装并启用 Service Mode（或通过 Polkit 授权 / 为内核授予 cap_net_admin 权限）后重试。"


# ── App Running Check, Focus, Graceful Close & Launch ───────────────────


def _matching_pids(target, path: Path | None) -> list[int]:
    names: set[str] = set()
    resolved_target: Path | None = None
    if path:
        names.add(path.name.casefold())
        try:
            resolved_target = path.resolve(strict=False)
            names.add(resolved_target.name.casefold())
        except OSError:
            pass
    for key_name in APP_PROCESS_NAMES.get(getattr(target, "key", ""), ()):
        names.add(key_name.casefold())
    if getattr(target, "kind", "") == "store":
        names.update(n.casefold() for n in APP_PROCESS_NAMES["claude_desktop"])
    elif getattr(target, "kind", "") == "codex":
        names.update(n.casefold() for n in APP_PROCESS_NAMES["chatgpt_desktop"])
    names -= {"", "electron", "app", "sh", "bash", "python", "python3"}

    matched: list[int] = []
    snapshot = process_snapshot()
    for pid, (proc_name, _ppid) in snapshot.items():
        if proc_name.casefold() in names:
            matched.append(pid)
            continue
        if resolved_target is not None:
            exe = process_executable_path(pid)
            if exe and exe.resolve(strict=False) == resolved_target:
                matched.append(pid)
    return matched


def application_running(path: Path) -> bool:
    resolved = path.resolve(strict=False)
    names = {path.name.casefold(), resolved.name.casefold()} - {"", "electron", "app", "sh", "bash"}
    snapshot = process_snapshot()
    for pid, (proc_name, _ppid) in snapshot.items():
        if proc_name.casefold() in names:
            return True
        exe = process_executable_path(pid)
        if exe and exe.resolve(strict=False) == resolved:
            return True
    return False


def _wmctrl_windows_for_pids(pids: set[int]) -> list[str]:
    wmctrl = shutil.which("wmctrl")
    if not wmctrl or not pids:
        return []
    try:
        out = subprocess.check_output([wmctrl, "-lp"], text=True, stderr=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return []
    wids: list[str] = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 3 and parts[2].isdigit() and int(parts[2]) in pids:
            wids.append(parts[0])
    return wids


def focus_app(target, path: Path | None) -> bool:
    pids = set(_matching_pids(target, path))
    if not pids:
        return False
    wmctrl = shutil.which("wmctrl")
    if wmctrl:
        wids = _wmctrl_windows_for_pids(pids)
        if wids:
            try:
                subprocess.run([wmctrl, "-ia", wids[0]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=True)
                return True
            except (OSError, subprocess.SubprocessError):
                pass
    xdotool = shutil.which("xdotool")
    if xdotool:
        for pid in pids:
            try:
                res = subprocess.run(
                    [xdotool, "search", "--pid", str(pid), "windowactivate"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
                )
                if res.returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False


def close_app(target, path: Path | None, timeout: float = 5.0) -> bool:
    """Gracefully ask a running Linux desktop app to close (WM close or SIGTERM, never SIGKILL)."""
    pids = set(_matching_pids(target, path))
    if not pids:
        return True
    sent = False
    wmctrl = shutil.which("wmctrl")
    if wmctrl:
        for wid in _wmctrl_windows_for_pids(pids):
            try:
                subprocess.run([wmctrl, "-ic", wid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                sent = True
            except (OSError, subprocess.SubprocessError):
                pass
    if not sent:
        snapshot = process_snapshot()
        # Only signal top-level matching PIDs so Electron/Chromium child processes shut down cleanly
        roots = [pid for pid in pids if snapshot.get(pid, ("", 0))[1] not in pids] or list(pids)
        for pid in roots:
            try:
                os.kill(pid, signal.SIGTERM)
                sent = True
            except OSError:
                pass
    if not sent:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _matching_pids(target, path):
            return True
        time.sleep(0.25)
    return not _matching_pids(target, path)


def find_terminal() -> tuple[str, tuple[str, ...]] | None:
    custom = (os.environ.get("TERMINAL") or "").strip()
    if custom:
        resolved = shutil.which(custom)
        if resolved:
            base = Path(resolved).name.casefold()
            for term_name, flag in TERMINAL_CANDIDATES:
                if base == term_name:
                    return resolved, flag
            return resolved, ("-e",)
    for term_name, flag in TERMINAL_CANDIDATES:
        resolved = shutil.which(term_name)
        if resolved:
            return resolved, flag
    return None


def launch(target, path: Path | None, port: int, data_dir: Path, check_running: bool = True,
           *, stable_profile: bool = False) -> None:
    from launcher import AlreadyRunningError, chromium_args, proxy_env, with_codex_proxy

    if path is None or not path.is_file():
        raise ValueError("找不到程序，请先点「路径」选择对应的可执行文件。")
    if target.kind == "codex":
        if check_running and application_running(path):
            raise AlreadyRunningError("ChatGPT / Codex 已在运行。请先保存工作并退出原窗口，再从启动器打开。")
        start = lambda: subprocess.Popen(
            [str(path)], env=proxy_env(port), cwd=str(path.parent),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if port > 0:
            with_codex_proxy(port, start, data_dir=data_dir)
        else:
            start()
        return
    if target.kind == "store":
        subprocess.Popen(
            [str(path)], env=proxy_env(port), cwd=str(path.parent),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return
    args = [str(path)]
    if target.kind == "electron":
        if check_running and application_running(path):
            raise AlreadyRunningError("应用已在运行。请先保存工作并完全退出原窗口，再用启动器打开。")
        args.extend(chromium_args(port))
    elif target.kind == "web":
        if not is_supported_browser(path):
            raise ValueError("请选择 Chrome、Chromium、Edge、Brave、Vivaldi 或 Opera 等 Chromium 内核浏览器。")
        profile_name = f"{path.stem}-tun" if port <= 0 else (f"{path.stem}-relay" if stable_profile else f"{path.stem}-{port}")
        profile = data_dir / "Browser" / profile_name
        profile.mkdir(parents=True, exist_ok=True)
        args.extend(chromium_args(port))
        args.extend([f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", f"--app={target.url}"])
    elif target.kind == "cli":
        term = find_terminal()
        if not term:
            raise ValueError("未找到可用的终端程序（如 gnome-terminal、konsole、kitty、alacritty、xterm），请设置 $TERMINAL 或安装终端模拟器。")
        term_bin, term_flags = term
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", encoding="utf-8", dir=data_dir, delete=False) as file:
            script = Path(file.name)
            env_pairs = [f"{key}={value}" for key, value in proxy_env(port, {}).items()]
            command = shlex.join(["/usr/bin/env", *env_pairs, str(path)])
            file.write(f"#!/bin/sh\nrm -f -- {shlex.quote(str(script))}\nexec {command}\n")
        script.chmod(0o700)
        try:
            subprocess.Popen(
                [term_bin, *term_flags, str(script)],
                cwd=str(HOME),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            script.unlink(missing_ok=True)
            raise
        return
    subprocess.Popen(
        args, env=proxy_env(port), cwd=str(HOME if target.kind == "web" else path.parent),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
