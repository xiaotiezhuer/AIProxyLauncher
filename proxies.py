"""Universal local proxy discovery, process attribution, and OS-level TUN detection."""
from __future__ import annotations

import concurrent.futures
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time

if sys.platform == "win32":
    import winreg
import linux
import macos
import win32net


HOME = Path.home()
LOCAL = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local"))
ROAMING = Path(os.environ.get("APPDATA", HOME / "AppData/Roaming"))
WINDOWS = Path(os.environ.get("SystemRoot", "C:/Windows"))
CLASH_HOME = ROAMING / "io.github.clash-verge-rev.clash-verge-rev"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PIPE_PREFIX = "\\\\.\\pipe\\"
PIPE_NAME = re.compile(r"(?:[a-zA-Z0-9_-]{1,64}\\){0,2}[a-zA-Z0-9_-]{1,160}")
RESTRICTED_AI_REGIONS = {"CN", "HK", "MO", "RU", "BY", "KP", "IR", "SY", "CU"}
REGION_NAMES = {
    "US": "美国", "JP": "日本", "TW": "台湾", "SG": "新加坡", "HK": "香港",
    "MO": "澳门", "CN": "中国", "GB": "英国", "DE": "德国", "FR": "法国",
    "NL": "荷兰", "CA": "加拿大", "KR": "韩国", "AU": "澳大利亚", "IN": "印度",
    "SE": "瑞典", "CH": "瑞士", "IE": "爱尔兰", "FI": "芬兰", "IT": "意大利",
    "ES": "西班牙", "PL": "波兰", "BR": "巴西", "RU": "俄罗斯", "TR": "土耳其",
    "MY": "马来西亚", "TH": "泰国", "VN": "越南", "PH": "菲律宾", "ID": "印度尼西亚",
}
RESTRICTED_NODE_TOKENS = (
    "香港", "hong kong", "hongkong", "🇭🇰", "中国", "china", "🇨🇳",
    "澳门", "macau", "macao", "🇲🇴", "俄罗斯", "russia", "🇷🇺",
)
LOCAL_CONTROLLER_ADDR = re.compile(
    r"^(?:127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\]|\[::\]|):([0-9]{1,5})$",
    re.I,
)
LOCAL_BIND_IPS = {"127.0.0.1", "0.0.0.0", "[::1]", "[::]", "::1", "::", "*"}
CLASH_CONFIG_KEYS = {"mode", "port", "mixed-port", "socks-port", "redir-port", "tproxy-port", "allow-lan", "tun"}
GENERIC_EXECUTABLES = {
    "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe", "explorer.exe", "svchost.exe",
    "services.exe", "wininit.exe", "system", "node.exe", "python.exe", "pythonw.exe",
    "windowsterminal.exe", "openconsole.exe", "launchd", "bash", "zsh", "sh", "fish",
    "terminal", "iterm2", "python3", "node", "systemd",
}
TUN_ADAPTER_PATTERN = re.compile(
    r"(?:wintun|mihomo|meta\s+tunnel|clash|sing-?tun|sing-?box|v2rayn?|hiddify|"
    r"nekobox|nekoray|karing|flclash|wireguard|outline|surge|stash|\butun\d*|\btun\d*|tap-windows)",
    re.I,
)
IPV4_ROUTE_ROW = re.compile(
    r"^\s*(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\S+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s*$"
)
PROXY_PIPE_PATTERNS = (
    ("verge-mihomo", "Clash Verge Rev"),
    ("clashparty", "Clash Party"),
    ("mihomoparty", "Clash Party"),
    ("nyanpasu", "Clash Nyanpasu"),
    ("sparkle", "Sparkle"),
    ("flclash", "FlClash"),
    ("clashmi", "Clash Mi"),
    ("mihomo", "Mihomo"),
    ("clash-meta", "Clash"),
    ("clash-rs", "Clash"),
)


@dataclass(frozen=True)
class Controller:
    pipe: str = ""
    port: int | None = None
    secret: str = field(default="", repr=False)
    name: str = ""
    unix: str = ""


JIMMM_CONTROLLER = Controller(pipe=r"MihomoParty\mihomo")


@dataclass(frozen=True)
class ProxyProfile:
    name: str
    family: str  # "clash", "singbox", "v2ray", "http"
    win_exes: tuple[str, ...] = ()
    win_cores: tuple[str, ...] = ()
    mac_apps: tuple[str, ...] = ()
    mac_procs: tuple[str, ...] = ()
    linux_procs: tuple[str, ...] = ()
    registry_names: tuple[str, ...] = ()
    win_dirs: tuple[str, ...] = ()
    default_ports: tuple[int, ...] = ()
    standalone_core: bool = False


PROFILES: tuple[ProxyProfile, ...] = (
    ProxyProfile(
        "Clash Verge Rev", "clash",
        win_exes=("clash-verge.exe",),
        win_cores=("verge-mihomo.exe", "verge-mihomo-alpha.exe", "clash-meta.exe", "clash-meta-alpha.exe"),
        mac_apps=("Clash Verge", "Clash Verge Rev"),
        mac_procs=("clash-verge", "verge-mihomo", "verge-mihomo-alpha"),
        linux_procs=("clash-verge", "clash-verge-rev", "clash-verge-service", "verge-mihomo", "verge-mihomo-alpha"),
        registry_names=("clash verge", "clash verge rev"),
        win_dirs=("Clash Verge", "Clash Verge Rev"),
        default_ports=(7897, 7890),
    ),
    ProxyProfile(
        "Clash Party", "clash",
        win_exes=("Clash Party.exe", "Mihomo Party.exe", "clash-party.exe", "mihomo-party.exe"),
        win_cores=("mihomo.exe", "mihomo-alpha.exe"),
        mac_apps=("Clash Party", "Mihomo Party"),
        mac_procs=("Clash Party", "Mihomo Party", "clash-party", "mihomo-party"),
        linux_procs=("mihomo-party", "clash-party"),
        registry_names=("clash party", "mihomo party"),
        win_dirs=("Clash Party", "Mihomo Party", "clash-party", "mihomo-party"),
        default_ports=(7890,),
    ),
    ProxyProfile(
        "JImmm", "clash",
        win_exes=("Jimmm.exe",),
        win_cores=("mihomo.exe", "mihomo-alpha.exe"),
        mac_apps=("Jimmm", "JImmm"),
        mac_procs=("Jimmm", "JImmm"),
        linux_procs=("jimmm", "Jimmm"),
        registry_names=("jimmm",),
        win_dirs=("Jimmm",),
        default_ports=(9674,),
    ),
    ProxyProfile(
        "FlClash", "clash",
        win_exes=("FlClash.exe",),
        win_cores=("FlClashCore.exe",),
        mac_apps=("FlClash",),
        mac_procs=("FlClash", "FlClashCore"),
        linux_procs=("flclash", "FlClash", "FlClashCore", "flclashcore"),
        registry_names=("flclash",),
        win_dirs=("FlClash",),
        default_ports=(7890,),
    ),
    ProxyProfile(
        "Clash Nyanpasu", "clash",
        win_exes=("Clash Nyanpasu.exe", "clash-nyanpasu.exe"),
        win_cores=("nyanpasu-mihomo.exe", "mihomo.exe", "mihomo-alpha.exe", "clash-rs.exe"),
        mac_apps=("Clash Nyanpasu",),
        mac_procs=("Clash Nyanpasu", "clash-nyanpasu"),
        linux_procs=("clash-nyanpasu", "nyanpasu-mihomo"),
        registry_names=("clash nyanpasu",),
        win_dirs=("Clash Nyanpasu", "clash-nyanpasu"),
        default_ports=(7890, 7897),
    ),
    ProxyProfile(
        "Sparkle", "clash",
        win_exes=("Sparkle.exe", "sparkle.exe"),
        win_cores=("mihomo.exe", "mihomo-alpha.exe"),
        mac_apps=("Sparkle",),
        mac_procs=("Sparkle", "sparkle"),
        linux_procs=("sparkle",),
        registry_names=("sparkle",),
        win_dirs=("Sparkle",),
        default_ports=(7890,),
    ),
    ProxyProfile(
        "Clash Mi", "clash",
        win_exes=("ClashMi.exe", "clashmi.exe"),
        win_cores=("mihomo.exe",),
        mac_apps=("Clash Mi", "ClashMi"),
        mac_procs=("Clash Mi", "ClashMi", "clashmi"),
        linux_procs=("clashmi", "clash-mi"),
        registry_names=("clash mi", "clashmi"),
        win_dirs=("ClashMi", "Clash Mi"),
        default_ports=(7890,),
    ),
    ProxyProfile(
        "Clash for Windows", "clash",
        win_exes=("Clash for Windows.exe",),
        win_cores=("clash-win64.exe", "clash-win32.exe", "clash-win64-v3.exe", "clash.exe"),
        linux_procs=("cfw", "clash-for-windows"),
        registry_names=("clash for windows",),
        win_dirs=("Clash for Windows",),
        default_ports=(7890,),
    ),
    ProxyProfile(
        "ClashX", "clash",
        mac_apps=("ClashX", "ClashX Pro", "ClashX Meta"),
        mac_procs=("ClashX", "ClashX Pro", "ClashX Meta", "ProxyConfigManager"),
        default_ports=(7890,),
    ),
    ProxyProfile(
        "Stash", "clash",
        mac_apps=("Stash",),
        mac_procs=("Stash",),
        default_ports=(7890,),
    ),
    ProxyProfile(
        "GUI.for.Clash", "clash",
        win_exes=("GUI.for.Clash.exe",),
        win_cores=("mihomo-windows-amd64.exe", "mihomo-windows-amd64-v3.exe", "mihomo.exe"),
        mac_apps=("GUI.for.Clash",),
        mac_procs=("GUI.for.Clash",),
        linux_procs=("GUI.for.Clash", "gui-for-clash"),
        registry_names=("gui.for.clash",),
        win_dirs=("GUI.for.Clash",),
        default_ports=(20122, 7890),
    ),
    ProxyProfile(
        "v2rayN", "v2ray",
        win_exes=("v2rayN.exe",),
        win_cores=("xray.exe", "sing-box.exe", "v2ray.exe", "mihomo.exe", "hysteria.exe", "naive.exe"),
        mac_apps=("v2rayN",),
        mac_procs=("v2rayN",),
        linux_procs=("v2rayN", "v2rayn"),
        registry_names=("v2rayn",),
        win_dirs=("v2rayN",),
        default_ports=(10808, 10809),
    ),
    ProxyProfile(
        "v2rayA", "v2ray",
        linux_procs=("v2raya",),
        default_ports=(20171, 20172, 20170),
    ),
    ProxyProfile(
        "V2rayU", "v2ray",
        mac_apps=("V2rayU", "V2RayXS", "V2RayX"),
        mac_procs=("V2rayU", "V2RayXS", "V2RayX", "v2ray-core"),
        default_ports=(1087, 10809),
    ),
    ProxyProfile(
        "Hiddify", "singbox",
        win_exes=("Hiddify.exe",),
        win_cores=("HiddifyCli.exe",),
        mac_apps=("Hiddify",),
        mac_procs=("Hiddify", "HiddifyCli"),
        linux_procs=("hiddify", "Hiddify", "HiddifyCli"),
        registry_names=("hiddify", "hiddify next"),
        win_dirs=("Hiddify", "Hiddify Next"),
        default_ports=(12334,),
    ),
    ProxyProfile(
        "NekoBox", "singbox",
        win_exes=("nekobox.exe", "nekoray.exe", "Throne.exe"),
        win_cores=("nekobox_core.exe", "nekoray_core.exe", "ThroneCore.exe", "sing-box.exe"),
        mac_apps=("NekoBox", "NekoRay", "Throne"),
        mac_procs=("nekobox", "nekoray", "Throne", "nekobox_core", "nekoray_core", "ThroneCore"),
        linux_procs=("nekobox", "nekoray", "throne", "Throne", "nekobox_core", "nekoray_core", "ThroneCore"),
        registry_names=("nekobox", "nekoray", "throne"),
        win_dirs=("nekobox", "nekoray", "Throne"),
        default_ports=(2080, 2081),
    ),
    ProxyProfile(
        "Karing", "singbox",
        win_exes=("karing.exe",),
        win_cores=("karingService.exe",),
        mac_apps=("Karing",),
        mac_procs=("Karing", "karing", "karingService"),
        linux_procs=("karing", "Karing", "karingService"),
        registry_names=("karing",),
        win_dirs=("Karing",),
        default_ports=(3067, 7890),
    ),
    ProxyProfile(
        "GUI.for.SingBox", "singbox",
        win_exes=("GUI.for.SingBox.exe",),
        win_cores=("sing-box.exe",),
        mac_apps=("GUI.for.SingBox", "SFM"),
        mac_procs=("GUI.for.SingBox", "SFM"),
        linux_procs=("GUI.for.SingBox", "gui-for-singbox"),
        registry_names=("gui.for.singbox",),
        win_dirs=("GUI.for.SingBox",),
        default_ports=(20122, 7890, 10808),
    ),
    ProxyProfile(
        "dae", "singbox",
        linux_procs=("dae", "daed"),
        default_ports=(12345,),
    ),
    ProxyProfile(
        "Surge", "http",
        mac_apps=("Surge",),
        mac_procs=("Surge", "Surge Helper"),
        default_ports=(6152,),
    ),
    ProxyProfile(
        "V2Box", "http",
        mac_apps=("V2Box",),
        mac_procs=("V2Box",),
        default_ports=(10809, 7890),
    ),
    ProxyProfile(
        "Loon", "http",
        mac_apps=("Loon",),
        mac_procs=("Loon",),
        default_ports=(7890,),
    ),
    ProxyProfile(
        "Shadowsocks", "http",
        win_exes=("Shadowsocks.exe", "ShadowsocksR-dotnet4.0.exe", "ss-local.exe", "sslocal.exe"),
        win_cores=("privoxy.exe", "ss-local.exe", "sslocal.exe"),
        mac_apps=("ShadowsocksX-NG", "ShadowsocksX-NG-R8"),
        mac_procs=("ShadowsocksX-NG", "ShadowsocksX-NG-R8", "ss-local", "sslocal", "privoxy"),
        linux_procs=("ss-local", "sslocal"),
        registry_names=("shadowsocks",),
        win_dirs=("Shadowsocks",),
        default_ports=(1080, 1087),
    ),
    ProxyProfile(
        "Mihomo", "clash",
        win_exes=("mihomo.exe", "mihomo-alpha.exe", "clash-meta.exe", "clash.exe", "clash-rs.exe"),
        mac_procs=("mihomo", "mihomo-alpha", "clash-meta", "clash", "clash-rs"),
        linux_procs=("mihomo", "mihomo-alpha", "clash-meta", "clash", "clash-rs"),
        registry_names=("mihomo",),
        default_ports=(7890,),
        standalone_core=True,
    ),
    ProxyProfile(
        "sing-box", "singbox",
        win_exes=("sing-box.exe",),
        mac_apps=("sing-box",),
        mac_procs=("sing-box",),
        linux_procs=("sing-box",),
        default_ports=(7890, 10808),
        standalone_core=True,
    ),
    ProxyProfile(
        "Xray", "v2ray",
        win_exes=("xray.exe", "v2ray.exe"),
        mac_procs=("xray", "v2ray"),
        linux_procs=("xray", "v2ray"),
        default_ports=(10808, 10809),
        standalone_core=True,
    ),
    ProxyProfile(
        "Privoxy", "http",
        win_exes=("privoxy.exe", "trojan.exe", "trojan-go.exe", "hysteria.exe", "naive.exe", "gost.exe"),
        mac_procs=("privoxy", "trojan", "trojan-go", "hysteria", "naive", "gost"),
        linux_procs=("privoxy", "trojan", "trojan-go", "hysteria", "naive", "gost"),
        default_ports=(8118,),
        standalone_core=True,
    ),
)

PROFILE_BY_NAME: dict[str, ProxyProfile] = {profile.name: profile for profile in PROFILES}
STANDALONE_CORES: set[str] = {profile.name for profile in PROFILES if profile.standalone_core}

_GUI_PROC_MAP: dict[str, str] = {}
_CORE_PROC_MAP: dict[str, str] = {}
_REGISTRY_MAP: dict[str, ProxyProfile] = {}
for _profile in PROFILES:
    for _reg in _profile.registry_names:
        _REGISTRY_MAP[_reg.casefold()] = _profile
    if not _profile.standalone_core:
        for _exe in (*_profile.win_exes, *_profile.mac_procs, *_profile.linux_procs):
            _GUI_PROC_MAP.setdefault(_exe.casefold(), _profile.name)
        for _core in _profile.win_cores:
            # Shared cores like mihomo.exe or sing-box.exe should fall back to standalone core unless parent matches.
            if _core.casefold() not in {"mihomo.exe", "mihomo-alpha.exe", "sing-box.exe", "xray.exe", "v2ray.exe",
                                        "hysteria.exe", "naive.exe", "privoxy.exe"}:
                _CORE_PROC_MAP.setdefault(_core.casefold(), _profile.name)
    else:
        for _exe in (*_profile.win_exes, *_profile.mac_procs, *_profile.linux_procs):
            _CORE_PROC_MAP.setdefault(_exe.casefold(), _profile.name)


@dataclass(frozen=True)
class HTTPProxyCandidate:
    port: int
    name: str
    source: str  # "system", "env", "process", "manual"
    pid: int | None = None
    scheme: str = "http"


@dataclass(frozen=True)
class DiscoverySnapshot:
    controllers: list[Controller]
    http_proxies: list[HTTPProxyCandidate]
    detected_apps: dict[str, Path | None]
    hints: list[str]
    controller_sibling_ports: dict[Controller, list[int]] = field(default_factory=dict)


def parse_local_controller_port(address: str) -> int | None:
    match = LOCAL_CONTROLLER_ADDR.fullmatch(address.strip())
    if match and 1 <= int(match[1]) <= 65535:
        return int(match[1])
    return None


def parse_local_http_proxy_port(value: str) -> int | None:
    match = re.fullmatch(r"(?:https?://)?(?:127\.0\.0\.1|localhost|\[::1\]):([0-9]{1,5})/?", value.strip(), re.I)
    if match and 1 <= int(match[1]) <= 65535:
        return int(match[1])
    return None


def env_proxy_ports(env: dict[str, str] | None = None) -> list[int]:
    source = os.environ if env is None else env
    by_upper = {key.upper(): value for key, value in source.items() if isinstance(value, str)}
    ports = []
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        raw = by_upper.get(key, "").strip()
        if not raw or raw.lower().startswith("socks"):
            continue
        port = parse_local_http_proxy_port(raw)
        if port and port not in ports:
            ports.append(port)
    return ports


def identify_process_proxy(pid: int, processes: dict[int, tuple[str, int]]) -> str | None:
    """Walk the process tree from pid upward to identify the owning proxy client name."""
    core_match = None
    current = pid
    visited = set()
    for _ in range(8):
        if current not in processes or current in visited:
            break
        visited.add(current)
        name, parent = processes[current]
        folded = name.casefold()
        if folded in _GUI_PROC_MAP:
            return _GUI_PROC_MAP[folded]
        if core_match is None and folded in _CORE_PROC_MAP:
            core_match = _CORE_PROC_MAP[folded]
        current = parent
    return core_match


def fallback_process_label(pid: int, processes: dict[int, tuple[str, int]], default: str = "系统代理") -> str:
    known = identify_process_proxy(pid, processes)
    if known:
        return known
    current = pid
    visited = set()
    chosen = ""
    for _ in range(6):
        if current not in processes or current in visited:
            break
        visited.add(current)
        name, parent = processes[current]
        folded = name.casefold()
        if not folded or folded in GENERIC_EXECUTABLES:
            if chosen:
                break
        else:
            stem = Path(name).stem.strip()
            if stem:
                chosen = stem
        current = parent
    return chosen or default


def process_executable_path(pid: int) -> Path | None:
    """Return the executable path for a Windows or Linux PID without spawning a subprocess."""
    if pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        return linux.process_executable_path(pid)
    if sys.platform != "win32":
        return None
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    )
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            path = Path(buf.value)
            if path.is_file():
                return path
    except OSError:
        pass
    finally:
        kernel.CloseHandle(handle)
    return None


def parse_windows_listening_ports(output: str) -> list[tuple[int, str, int]]:
    seen: dict[tuple[int, int], str] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 5 or parts[0].upper() != "TCP" or parts[3].upper() != "LISTENING":
            continue
        local_ip, sep, port_text = parts[1].rpartition(":")
        if not sep or local_ip not in LOCAL_BIND_IPS or not port_text.isdigit() or not parts[4].isdigit():
            continue
        port = int(port_text)
        pid = int(parts[4])
        if not (1 <= port <= 65535) or pid <= 0:
            continue
        key = (pid, port)
        if key not in seen or local_ip in {"127.0.0.1", "[::1]"}:
            seen[key] = local_ip
    return [(pid, ip, port) for (pid, port), ip in seen.items()]


def listening_tcp_ports(is_mac: bool = False, is_linux: bool = False) -> list[tuple[int, str, int]]:
    if is_mac:
        return macos.listening_tcp_ports()
    if is_linux:
        return linux.listening_tcp_ports()
    native = win32net.windows_tcp_tables()
    if native is not None:
        return native[1]
    try:
        output = subprocess.check_output(
            [str(WINDOWS / "System32/netstat.exe"), "-ano"],
            timeout=5, creationflags=NO_WINDOW,
        ).decode("ascii", errors="replace")
        return parse_windows_listening_ports(output)
    except (OSError, subprocess.SubprocessError):
        return []


def parse_windows_tun_routes(output: str) -> bool:
    has_tun_adapter = False
    defaults: list[tuple[str, int]] = []
    split_zero: set[str] = set()
    split_high: set[str] = set()
    fake_ip_ifaces: set[str] = set()
    excluded = ("netease uu", "uu tap", "vmware", "virtualbox", "hyper-v", "wsl")

    for line in output.splitlines():
        match = IPV4_ROUTE_ROW.match(line)
        if match:
            dest, mask, _gateway, iface, metric_text = match.groups()
            metric = int(metric_text)
            if dest == "0.0.0.0" and mask == "0.0.0.0":
                defaults.append((iface, metric))
            elif dest == "0.0.0.0" and mask == "128.0.0.0":
                split_zero.add(iface)
            elif dest == "128.0.0.0" and mask == "128.0.0.0":
                split_high.add(iface)
            elif dest.startswith("198.18.") or iface.startswith(("198.18.", "198.19.")):
                fake_ip_ifaces.add(iface)
        elif not any(token in line.casefold() for token in excluded) and TUN_ADAPTER_PATTERN.search(line):
            has_tun_adapter = True

    # Split-default routes (0.0.0.0/1 + 128.0.0.0/1) on the same interface are a standard TUN takeover.
    if split_zero & split_high:
        return True
    # Fake-IP interface (198.18.x.x) paired with a default route or active TUN interface.
    if fake_ip_ifaces and (any(iface in fake_ip_ifaces for iface, _ in defaults) or has_tun_adapter):
        return True
    if not defaults:
        return False
    best_metric = min(metric for _, metric in defaults)
    tun_prefixes = ("198.18.", "198.19.", "172.19.", "172.18.", "10.0.85.")
    if has_tun_adapter and any(iface.startswith(tun_prefixes) and metric <= best_metric for iface, metric in defaults):
        return True
    # Universal custom-named TUN/VPN takeover: multiple default routes where the highest-priority
    # (strictly lowest metric) default route uses a tunnel/CGNAT address space (198.18/15, 172.16-31, 100.64-127, 10.x)
    # beating the physical LAN gateway (e.g. 192.168.x.x).
    if len(defaults) >= 2:
        max_metric = max(metric for _, metric in defaults)
        if best_metric < max_metric:
            for iface, metric in defaults:
                if metric == best_metric and _is_tunnel_ip_prefix(iface):
                    return True
    return False


def _is_tunnel_ip_prefix(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return False
    a, b = int(parts[0]), int(parts[1])
    if a == 198 and b in {18, 19}:
        return True
    if a == 100 and 64 <= b <= 127:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 10 and (b == 0 or b >= 8):
        return True
    return False


def detect_system_tun(is_mac: bool = False, is_linux: bool = False) -> bool:
    if is_mac:
        return macos.detect_system_tun()
    if is_linux:
        return linux.detect_system_tun()
    try:
        output = subprocess.check_output(
            [str(WINDOWS / "System32/route.exe"), "print", "-4"],
            timeout=4, creationflags=NO_WINDOW,
        ).decode("mbcs", errors="replace")
        return parse_windows_tun_routes(output)
    except (OSError, subprocess.SubprocessError):
        return False


def config_files(is_mac: bool = False, is_linux: bool = False) -> list[tuple[str, Path]]:
    if is_mac:
        return macos.config_files()
    if is_linux:
        return linux.config_files()
    party_roots = [
        ("JImmm", ROAMING / "Jimmm"),
        ("Clash Party", ROAMING / "clash-party"),
        ("Clash Party", ROAMING / "mihomo-party"),
        ("Sparkle", ROAMING / "sparkle"),
    ]
    files: list[tuple[str, Path]] = [
        ("Clash Verge Rev", CLASH_HOME / "clash-verge.yaml"),
        ("Clash Verge Rev", CLASH_HOME / "config.yaml"),
        ("Clash Nyanpasu", ROAMING / "clash-nyanpasu/clash-config.yaml"),
        ("Clash Nyanpasu", ROAMING / "clash-nyanpasu/config.yaml"),
        ("Clash Nyanpasu", LOCAL / "clash-nyanpasu/clash-config.yaml"),
        ("Clash for Windows", HOME / ".config/clash/config.yaml"),
        ("Clash for Windows", ROAMING / "clash_win/config.yaml"),
        ("FlClash", ROAMING / "com.follow.clash/config.yaml"),
        ("FlClash", ROAMING / "FlClash/config.yaml"),
        ("Clash Mi", ROAMING / "clashmi/config.yaml"),
        ("Mihomo", HOME / ".config/mihomo/config.yaml"),
        ("Mihomo", ROAMING / "mihomo/config.yaml"),
        ("Hiddify", ROAMING / "Hiddify/hiddify/current-config.json"),
        ("Hiddify", ROAMING / "app.hiddify.com/hiddify/current-config.json"),
        ("Hiddify", ROAMING / "Hiddify/current-config.json"),
        ("Hiddify", ROAMING / "app.hiddify.com/current-config.json"),
        ("sing-box", HOME / ".config/sing-box/config.json"),
        ("sing-box", ROAMING / "sing-box/config.json"),
    ]
    for label, root in party_roots:
        for filename in ("mihomo.yaml", "work/config.yaml", "config.yaml"):
            files.append((label, root / filename))
    return files


def parse_yaml_controller_file(path: Path, name: str, is_mac: bool = False, is_linux: bool = False) -> tuple[list[Controller], list[Controller]]:
    pipes: list[Controller] = []
    http: list[Controller] = []
    try:
        with path.open(encoding="utf-8-sig") as config:
            values: dict[str, str] = {}
            for line in config.read(4 * 1024 * 1024).splitlines():
                match = re.fullmatch(
                    r'''(")?(external-controller-pipe|external-controller-unix|external-controller|secret)(?(1)"):[ \t]*(.*?)\s*''',
                    line,
                )
                if match:
                    scalar = re.fullmatch(r'''("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^ \t#]*)(?:[ \t]+#.*)?''', match[3])
                    if scalar:
                        value = scalar[1]
                        if value.startswith('"'):
                            try:
                                value = json.loads(value)
                            except ValueError:
                                continue
                        elif value.startswith("'"):
                            value = value[1:-1].replace("''", "'")
                        values[match[2]] = value
    except (OSError, UnicodeError):
        return pipes, http
    pipe = values.get("external-controller-pipe", "").removeprefix(PIPE_PREFIX)
    if not is_mac and not is_linux and PIPE_NAME.fullmatch(pipe):
        pipes.append(Controller(pipe=pipe, name=name))
    unix = values.get("external-controller-unix", "")
    if (is_mac or is_linux) and unix.startswith("/") and "\x00" not in unix:
        pipes.append(Controller(unix=unix, name=name))
    port = parse_local_controller_port(values.get("external-controller", ""))
    if port:
        http.append(Controller(port=port, secret=values.get("secret", ""), name=name))
    return pipes, http


def parse_json_controller_file(path: Path, name: str) -> list[Controller]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        if len(raw) > 4 * 1024 * 1024:
            return []
        data = json.loads(raw)
    except (OSError, UnicodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    clash_api = (data.get("experimental") or {}).get("clash_api") if isinstance(data.get("experimental"), dict) else None
    if not isinstance(clash_api, dict):
        return []
    addr = clash_api.get("external_controller")
    if not isinstance(addr, str):
        return []
    port = parse_local_controller_port(addr)
    if not port:
        return []
    secret = clash_api.get("secret")
    return [Controller(port=port, secret=secret if isinstance(secret, str) else "", name=name)]


def is_restricted_region(code: str) -> bool:
    return code.upper() in RESTRICTED_AI_REGIONS


def region_label(code: str) -> str:
    upper = code.upper().strip()
    if not upper:
        return ""
    return REGION_NAMES.get(upper, upper)


def node_looks_restricted(name: str) -> bool:
    folded = name.casefold()
    if any(token in folded for token in RESTRICTED_NODE_TOKENS):
        return True
    return bool(re.search(r"(?:^|[^a-z])(hk|cn|mo|ru)(?:$|[^a-z])", folded))


NODE_REGION_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("US", ("美国", "🇺🇸", "united states", "america", "los angeles", "san jose", "seattle", "new york", "dallas", "chicago", "silicon")),
    ("JP", ("日本", "🇯🇵", "japan", "tokyo", "osaka", "东京", "大阪")),
    ("TW", ("台湾", "🇹🇼", "taiwan", "taipei", "台北")),
    ("SG", ("新加坡", "狮城", "🇸🇬", "singapore")),
    ("HK", ("香港", "🇭🇰", "hong kong", "hongkong")),
    ("GB", ("英国", "🇬🇧", "united kingdom", "london", "伦敦")),
    ("DE", ("德国", "🇩🇪", "germany", "frankfurt", "法兰克福")),
    ("KR", ("韩国", "🇰🇷", "korea", "seoul", "首尔")),
    ("CA", ("加拿大", "🇨🇦", "canada", "toronto", "vancouver")),
    ("NL", ("荷兰", "🇳🇱", "netherlands", "amsterdam")),
    ("FR", ("法国", "🇫🇷", "france", "paris")),
    ("AU", ("澳大利亚", "澳洲", "🇦🇺", "australia", "sydney")),
    ("RU", ("俄罗斯", "🇷🇺", "russia", "moscow")),
    ("CN", ("中国", "🇨🇳", "china")),
)


def infer_region_from_node(node_name: str) -> str:
    """Infer an ISO country code from a proxy node display name without network calls."""
    if not node_name:
        return ""
    folded = node_name.casefold()
    for code, tokens in NODE_REGION_PATTERNS:
        if any(tok in folded for tok in tokens):
            return code
    match = re.search(r"(?:^|[^a-z])(us|jp|tw|sg|hk|gb|uk|de|kr|ca|nl|fr|au|ru|cn|mo)(?:$|[^a-z])", folded)
    if match:
        code = match[1].upper()
        return "GB" if code == "UK" else code
    return ""


_SSL_CA_SOURCE: tuple[str, str] | None = None
MAC_CA_BUNDLE_CANDIDATES: tuple[str, ...] = (
    "/etc/ssl/cert.pem",
    "/private/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/ca-bundle.pem",
    "/opt/homebrew/etc/openssl@3/cert.pem",
    "/usr/local/etc/openssl@3/cert.pem",
)


def _detect_ca_source() -> tuple[str, str]:
    """Locate a working system CA bundle when Python's default OpenSSL trust store is empty (e.g. macOS .app)."""
    global _SSL_CA_SOURCE
    if _SSL_CA_SOURCE is not None:
        return _SSL_CA_SOURCE
    try:
        ctx = ssl.create_default_context()
        ca_count = ctx.cert_store_stats().get("x509_ca", 0)
        if not isinstance(ca_count, int):
            return ("default", "")
        if ca_count >= 10:
            _SSL_CA_SOURCE = ("default", "")
            return _SSL_CA_SOURCE
    except (OSError, ssl.SSLError):
        pass
    for path in MAC_CA_BUNDLE_CANDIDATES:
        if os.path.isfile(path):
            try:
                test = ssl.create_default_context(cafile=path)
                if test.cert_store_stats().get("x509_ca", 0) >= 10:
                    _SSL_CA_SOURCE = ("file", path)
                    return _SSL_CA_SOURCE
            except (OSError, ssl.SSLError):
                continue
    if sys.platform == "darwin":
        try:
            pem = subprocess.check_output(
                [
                    "/usr/bin/security", "find-certificate", "-a", "-p",
                    "/System/Library/Keychains/SystemRootCertificates.keychain",
                ],
                text=True, timeout=5, stderr=subprocess.DEVNULL,
            )
            if "BEGIN CERTIFICATE" in pem:
                test = ssl.create_default_context(cadata=pem)
                if test.cert_store_stats().get("x509_ca", 0) > 0:
                    _SSL_CA_SOURCE = ("cadata", pem)
                    return _SSL_CA_SOURCE
        except (OSError, subprocess.SubprocessError, ssl.SSLError):
            pass
    _SSL_CA_SOURCE = ("default", "")
    return _SSL_CA_SOURCE


def create_ssl_context() -> ssl.SSLContext:
    """Create a verified TLS context that works out-of-the-box in PyInstaller macOS .app bundles."""
    kind, value = _detect_ca_source()
    if kind == "file":
        return ssl.create_default_context(cafile=value)
    if kind == "cadata":
        return ssl.create_default_context(cadata=value)
    return ssl.create_default_context()


def probe_exit_region(
    port: int | None,
    timeout: float = 3.5,
    host: str = "www.cloudflare.com",
    *,
    scheme: str = "http",
) -> tuple[str, int | None]:
    """Read proxy exit country code and RTT (ms) via Cloudflare trace through HTTP CONNECT, SOCKS5, or direct OS TUN."""
    started = time.perf_counter()
    sock = None
    try:
        if port is None:
            sock = socket.create_connection((host, 443), timeout=timeout)
            sock.settimeout(timeout)
        elif scheme == "socks5":
            import relay
            sock = relay.dial_socks5_upstream(port, host, 443, timeout=timeout)
        else:
            sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
            sock.settimeout(timeout)
            sock.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode("ascii"))
            with sock.makefile("rb") as reader:
                status = reader.readline(4096).split()
                for _ in range(32):
                    line = reader.readline(4096)
                    if line == b"\r\n" or not line:
                        break
            if len(status) < 2 or status[1] != b"200":
                return "", None
        context = create_ssl_context()
        with context.wrap_socket(sock, server_hostname=host) as tls:
            tls.sendall(
                f"GET /cdn-cgi/trace HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nUser-Agent: AIProxyLauncher\r\n\r\n".encode("ascii")
            )
            chunks = []
            total = 0
            while total < 8192:
                data = tls.recv(4096)
                if not data:
                    break
                chunks.append(data)
                total += len(data)
        elapsed_ms = max(1, int(round((time.perf_counter() - started) * 1000)))
        text = b"".join(chunks).decode("ascii", errors="replace")
        match = re.search(r"^loc=([A-Z]{2})\s*$", text, re.M)
        return (match[1], elapsed_ms) if match else ("", elapsed_ms)
    except OSError:
        return "", None
    finally:
        if sock:
            try:
                sock.close()
            except OSError:
                pass


def find_registered_app(display_tokens: tuple[str, ...], executables: tuple[str, ...]) -> Path | None:
    """Locate an installed Windows desktop app executable via Uninstall Registry."""
    if sys.platform != "win32":
        return None
    tokens = tuple(t.casefold() for t in display_tokens)
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive, view in (
        (winreg.HKEY_CURRENT_USER, 0),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
    ):
        try:
            with winreg.OpenKey(hive, uninstall, 0, winreg.KEY_READ | view) as base:
                for idx in range(winreg.QueryInfoKey(base)[0]):
                    try:
                        with winreg.OpenKey(base, winreg.EnumKey(base, idx)) as entry:
                            display = str(winreg.QueryValueEx(entry, "DisplayName")[0]).strip().casefold()
                            if not any(display == tok or display.startswith(tok + " ") for tok in tokens):
                                continue
                            for reg_key in ("InstallLocation", "DisplayIcon"):
                                try:
                                    raw = os.path.expandvars(str(winreg.QueryValueEx(entry, reg_key)[0])).strip('" ')
                                    if reg_key == "DisplayIcon":
                                        raw = raw.split(",", 1)[0].strip('" ')
                                    if not raw:
                                        continue
                                    p = Path(raw)
                                    if not p.is_absolute() or str(p).startswith("\\\\"):
                                        continue
                                    if p.is_file() and p.suffix.lower() == ".exe":
                                        if not executables or p.name.casefold() in {e.casefold() for e in executables}:
                                            return p
                                    elif p.is_dir():
                                        hit = first_file(*(p / exe for exe in executables))
                                        if hit:
                                            return hit
                                except (OSError, TypeError):
                                    continue
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def pipe_display_name(pipe: str, processes: dict[int, tuple[str, int]], default: str = "") -> str:
    folded = pipe.casefold()
    if folded.startswith("mihomoparty\\"):
        running = {name.casefold() for name, _ in processes.values()}
        if "jimmm.exe" in running:
            return "JImmm"
        if "clash party.exe" in running or "mihomo party.exe" in running or "clash-party.exe" in running:
            return "Clash Party"
        if "sparkle.exe" in running:
            return "Sparkle"
        return "JImmm" if (ROAMING / "Jimmm").is_dir() else "Clash Party"
    for token, label in PROXY_PIPE_PATTERNS:
        if token in folded:
            return label
    return default or "Clash"


def windows_existing_pipes() -> set[str] | None:
    if sys.platform != "win32":
        return None
    try:
        return {entry.casefold() for entry in os.listdir(PIPE_PREFIX)}
    except OSError:
        return None


COMMON_PROXY_PORTS: frozenset[int] = frozenset({
    7890, 7891, 7892, 7893, 7897, 7898, 7899,
    9090, 9097, 9674, 1080, 1081, 1082,
    10800, 10801, 10808, 10809, 10810, 10811,
    12334, 2080, 2081, 20170, 20171, 4780, 4781,
    15732, 6152, 6153, 8118, 9910,
})

_NON_PROXY_EXES: frozenset[str] = frozenset({
    "system", "idle", "registry", "svchost.exe", "services.exe", "lsass.exe",
    "explorer.exe", "wininit.exe", "csrss.exe", "smss.exe", "spoolsv.exe",
    "msedge.exe", "chrome.exe", "brave.exe", "vivaldi.exe", "firefox.exe", "arc.exe",
    "code.exe", "cursor.exe", "windsurf.exe", "antigravity.exe", "antigravity ide.exe",
    "python.exe", "pythonw.exe", "python3", "node.exe", "node", "java.exe", "javaw.exe",
    "wechat.exe", "weixin.exe", "qq.exe", "dingtalk.exe", "wpscloudsvr.exe",
    "aiproxylauncher.exe", "aiproxylauncher", "workbuddy.exe", "claude.exe", "chatgpt.exe",
})

_WHITELABEL_CACHE: dict[str, tuple[str, str, tuple[str, ...]] | None] = {}


def detect_whitelabel_proxy(exe_path: Path, *, is_mac: bool = False, is_linux: bool = False) -> tuple[str, str, tuple[str, ...]] | None:
    """Identify white-label / rebranded proxy clients by their directory skeleton artifacts.

    Returns (friendly_display_name, core_family, expected_core_exes) or None.
    """
    cache_key = str(exe_path).casefold()
    if cache_key in _WHITELABEL_CACHE:
        return _WHITELABEL_CACHE[cache_key]

    result: tuple[str, str, tuple[str, ...]] | None = None
    try:
        low_path = str(exe_path).casefold()
        if any(seg in low_path for seg in ("\\windows\\", "\\system32\\", "\\windowsapps\\", "/system/", "/usr/bin/", "/usr/sbin/")):
            _WHITELABEL_CACHE[cache_key] = None
            return None
        if exe_path.name.casefold() in _NON_PROXY_EXES:
            _WHITELABEL_CACHE[cache_key] = None
            return None

        scan_dirs = [exe_path.parent, exe_path.parent / "resources"]
        if is_linux:
            scan_dirs.extend([exe_path.parent / "lib", exe_path.parent / "data"])
        if is_mac:
            bundle = next((p for p in exe_path.parents if p.suffix.lower() == ".app"), None)
            if bundle:
                scan_dirs.extend([bundle / "Contents/MacOS", bundle / "Contents/Resources"])
        entries: set[str] = set()
        for folder in dict.fromkeys(scan_dirs):
            if folder.is_dir():
                try:
                    entries.update(name.casefold() for name in os.listdir(folder))
                except OSError:
                    continue

        family = ""
        expected_cores: tuple[str, ...] = ()
        if (any(e.startswith("flclashcore") for e in entries)
                or ({"proxy_plugin.dll", "enableloopback.exe"} <= entries)
                or ("proxy_plugin.dll" in entries and any(e.endswith("helperservice.exe") for e in entries))
                or ("libproxy_plugin.so" in entries and any("flutter" in e for e in entries))):
            family = "FlClash"
            expected_cores = ("flclashcore.exe", "flclashcore")
        elif any(e in entries for e in ("verge-mihomo.exe", "verge-mihomo-alpha.exe", "mihomo.exe", "mihomo-alpha.exe", "clash-meta.exe", "libclash.dll", "libclash.so", "libclash.dylib")):
            family = "Mihomo"
            expected_cores = ("verge-mihomo.exe", "verge-mihomo-alpha.exe", "mihomo.exe", "mihomo-alpha.exe", "clash-meta.exe", "mihomo", "verge-mihomo")
        elif any(e in entries for e in ("sing-box.exe", "sing-box")):
            family = "Sing-box"
            expected_cores = ("sing-box.exe", "sing-box")
        elif any(e in entries for e in ("xray.exe", "v2ray.exe", "xray", "v2ray")):
            family = "Xray"
            expected_cores = ("xray.exe", "v2ray.exe", "xray", "v2ray")

        if family:
            friendly = ""
            if is_mac:
                import macos
                friendly = macos.bundle_display_name(exe_path)
            elif is_linux:
                friendly = linux.desktop_display_name(exe_path)
            else:
                friendly = win32net.pe_display_name(exe_path)
            if not friendly:
                friendly = exe_path.stem
            result = (friendly, family, expected_cores)
    except OSError:
        result = None

    _WHITELABEL_CACHE[cache_key] = result
    return result


_GUI_FRAMEWORK_MARKERS: frozenset[str] = frozenset({
    "flutter_windows.dll", "webview2loader.dll", "qt6core.dll", "qt5core.dll",
    "libcef.dll", "d3dcompiler_47.dll",
    "libflutter_linux_gtk.so", "libcef.so", "libqt6core.so", "libqt5core.so",
})
_GUI_SHELL_CACHE: dict[str, bool] = {}


def is_gui_framework_executable(exe_path: Path, *, is_mac: bool = False, is_linux: bool = False) -> bool:
    """Detect whether ANY executable is a desktop GUI frontend (Flutter/Electron/Tauri/Qt/CEF).

    Random ephemeral TCP sockets owned by GUI frontends are internal UI<->Core IPC channels
    and must NEVER be actively probed with HTTP requests (prevents breaking IPC like FlClash 'socket done').
    """
    key = str(exe_path).casefold()
    if key in _GUI_SHELL_CACHE:
        return _GUI_SHELL_CACHE[key]
    found = False
    try:
        if is_mac:
            bundle = next((p for p in exe_path.parents if p.suffix.lower() == ".app"), None)
            if bundle:
                fw = bundle / "Contents/Frameworks"
                if fw.is_dir():
                    names = {n.casefold() for n in os.listdir(fw)}
                    found = any(tok in n for n in names for tok in ("flutter", "electron", "chromium", "qt"))
        else:
            parent = exe_path.parent
            if parent.is_dir():
                names = {n.casefold() for n in os.listdir(parent)}
                if is_linux and (parent / "lib").is_dir():
                    try:
                        names.update(n.casefold() for n in os.listdir(parent / "lib"))
                    except OSError:
                        pass
                if names & _GUI_FRAMEWORK_MARKERS:
                    found = True
                elif is_linux and any(n.startswith(("libflutter_linux", "libqt6core.so", "libqt5core.so", "libwebkit2gtk")) for n in names):
                    found = True
                elif (parent / "resources" / "app.asar").is_file():
                    found = True
    except OSError:
        found = False
    _GUI_SHELL_CACHE[key] = found
    return found


def discover_flclash_family_configs(is_mac: bool = False, *, is_linux: bool = False, base_dirs: tuple[Path, ...] | None = None) -> list[tuple[str, int, bool]]:
    """Passively read configured mixed-port & TUN state for ANY FlClash-family white-label client.

    Every FlClash fork stores Flutter SharedPreferences under com.follow/<brand>/shared_preferences.json.
    Returns [(brand_name, mixed_port, tun_enabled), ...].
    """
    roots = base_dirs
    if roots is None:
        if is_mac:
            support = HOME / "Library/Application Support"
            roots = (support / "com.follow", support / "com.follow.clash")
        elif is_linux:
            roots = (linux.XDG_DATA_HOME / "com.follow", linux.XDG_DATA_HOME / "com.follow.clash", linux.XDG_CONFIG_HOME / "com.follow")
        else:
            roots = (ROAMING / "com.follow",)
    results: list[tuple[str, int, bool]] = []
    seen_prefs: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        candidates: list[tuple[str, Path]] = []
        direct_pref = root / "shared_preferences.json"
        if direct_pref.is_file():
            candidates.append((root.name, direct_pref))
        try:
            subdirs = [d for d in root.iterdir() if d.is_dir()]
        except OSError:
            subdirs = []
        for sub in subdirs[:16]:
            pref = sub / "shared_preferences.json"
            if pref.is_file():
                candidates.append((sub.name, pref))
        for raw_brand, pref in candidates:
            if pref in seen_prefs:
                continue
            seen_prefs.add(pref)
            try:
                raw = json.loads(pref.read_text(encoding="utf-8-sig"))
                cfg_str = raw.get("flutter.config")
                cfg = json.loads(cfg_str) if isinstance(cfg_str, str) else (cfg_str if isinstance(cfg_str, dict) else {})
                patch = cfg.get("patchClashConfig") or {}
                port = patch.get("mixed-port") or patch.get("port") or 0
                tun_cfg = patch.get("tun") or {}
                tun_on = tun_cfg.get("enable") is True if isinstance(tun_cfg, dict) else False
                if isinstance(port, int) and 1 <= port <= 65535:
                    brand = raw_brand.strip() or "FlClash"
                    if brand.casefold() in {"clash", "com.follow.clash"}:
                        brand = "FlClash"
                    results.append((brand, port, tun_on))
            except (OSError, ValueError, TypeError):
                continue
    return results


def scan_whitelabel_processes(
    processes: dict[int, tuple[str, int]],
    *,
    is_mac: bool = False,
    is_linux: bool = False,
    path_resolver=None,
) -> dict[int, tuple[str, str, tuple[str, ...], Path]]:
    """Find running unknown processes that are white-label forks of FlClash/Mihomo/Sing-box/Xray."""
    resolver = path_resolver or process_executable_path
    found: dict[int, tuple[str, str, tuple[str, ...], Path]] = {}
    checked_names: dict[str, tuple[str, str, tuple[str, ...], Path] | None] = {}
    for pid, (name, _ppid) in processes.items():
        low = name.casefold()
        if not low or low in _NON_PROXY_EXES or identify_process_proxy(pid, processes):
            continue
        if low in checked_names:
            hit = checked_names[low]
            if hit:
                found[pid] = hit
            continue
        exe = resolver(pid)
        if not exe or not exe.is_file():
            checked_names[low] = None
            continue
        info = detect_whitelabel_proxy(exe, is_mac=is_mac, is_linux=is_linux)
        if info:
            rec = (info[0], info[1], info[2], exe)
            checked_names[low] = rec
            found[pid] = rec
        else:
            checked_names[low] = None
    return found


def probe_pipe_controller(pipe: str, timeout: float = 0.6) -> bool:
    """Verify via native Win32 pipe I/O whether an unknown named pipe speaks Clash/Mihomo REST API."""
    if sys.platform != "win32":
        return False
    try:
        raw = win32net.pipe_http_request(
            pipe,
            b"GET /version HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            timeout=timeout,
            max_bytes=8192,
        )
        if not raw:
            return False
        head, sep, body = raw.partition(b"\r\n\r\n")
        if sep and b"200" in head.splitlines()[0]:
            data = json.loads(body)
            return isinstance(data, dict) and "version" in data
    except (OSError, ValueError, IndexError):
        pass
    return False


def windows_active_proxy_pipes(processes: dict[int, tuple[str, int]]) -> list[Controller]:
    if sys.platform != "win32":
        return []
    try:
        entries = os.listdir(PIPE_PREFIX)
    except OSError:
        return []
    found: list[Controller] = []
    seen: set[str] = set()
    wildcard_tokens = ("mihomo", "clash", "flclash", "sing-box", "singbox")
    for entry in entries:
        folded = entry.casefold()
        if "service" in folded or not PIPE_NAME.fullmatch(entry) or folded in seen:
            continue
        if any(token in folded for token, _ in PROXY_PIPE_PATTERNS):
            seen.add(folded)
            found.append(Controller(pipe=entry, name=pipe_display_name(entry, processes)))
        elif any(tok in folded for tok in wildcard_tokens) and probe_pipe_controller(entry):
            seen.add(folded)
            friendly = entry.split("\\")[0].replace("-mihomo", "").replace("_mihomo", "").strip() or "Clash"
            found.append(Controller(pipe=entry, name=friendly))
    return found


def probe_local_controller(port: int, name: str, timeout: float = 0.7) -> tuple[Controller | None, bool]:
    """Probe whether 127.0.0.1:port exposes a Clash/Mihomo-compatible REST controller API."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("GET", "/configs", headers={"Host": "localhost"})
        with connection.getresponse() as response:
            if response.status == 401:
                body = response.read(512)
                if b"unauthorized" in body.lower() or b"message" in body.lower():
                    return None, True
                return None, False
            if response.status != 200:
                return None, False
            body = response.read(65536)
            data = json.loads(body)
            if isinstance(data, dict) and (CLASH_CONFIG_KEYS & data.keys()):
                return Controller(port=port, name=name), False
    except (OSError, http.client.HTTPException, ValueError):
        pass
    finally:
        connection.close()
    return None, False


def probe_socks5_port(port: int, timeout: float = 0.5) -> bool:
    """Return True if 127.0.0.1:port responds to a SOCKS5 greeting."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"\x05\x01\x00")
            reply = sock.recv(2)
            return len(reply) == 2 and reply[0] == 5 and reply[1] in {0, 2, 0xFF}
    except OSError:
        return False


def registered_proxy_apps() -> dict[str, Path | None]:
    if sys.platform != "win32":
        return {}
    found: dict[str, Path | None] = {}
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive, view in (
        (winreg.HKEY_CURRENT_USER, 0),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
    ):
        try:
            with winreg.OpenKey(hive, uninstall, 0, winreg.KEY_READ | view) as base:
                for idx in range(winreg.QueryInfoKey(base)[0]):
                    try:
                        with winreg.OpenKey(base, winreg.EnumKey(base, idx)) as entry:
                            display = str(winreg.QueryValueEx(entry, "DisplayName")[0]).strip()
                            folded = display.casefold()
                            profile = _REGISTRY_MAP.get(folded)
                            if not profile:
                                profile = next(
                                    (p for token, p in _REGISTRY_MAP.items() if folded.startswith(token + " ")),
                                    None,
                                )
                            if not profile or found.get(profile.name):
                                continue
                            resolved: Path | None = None
                            try:
                                raw_loc = os.path.expandvars(str(winreg.QueryValueEx(entry, "InstallLocation")[0])).strip('" ')
                                if raw_loc:
                                    loc = Path(raw_loc)
                                    if loc.is_absolute() and not str(loc).startswith("\\\\"):
                                        resolved = loc
                            except (OSError, TypeError):
                                pass
                            if resolved is None:
                                try:
                                    raw_icon = os.path.expandvars(str(winreg.QueryValueEx(entry, "DisplayIcon")[0])).strip('" ')
                                    icon_path = Path(raw_icon.split(",", 1)[0].strip('" '))
                                    if icon_path.is_absolute() and not str(icon_path).startswith("\\\\") and icon_path.suffix.lower() == ".exe":
                                        resolved = icon_path
                                except (OSError, TypeError):
                                    pass
                            found[profile.name] = resolved
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def first_file(*paths: Path) -> Path | None:
    for path in paths:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _hide_standalone_core(label: str, found: dict[str, Path | None]) -> bool:
    if label not in STANDALONE_CORES:
        return False
    profile = PROFILE_BY_NAME.get(label)
    if not profile:
        return False
    return any(
        (other := PROFILE_BY_NAME.get(name)) is not None
        and not other.standalone_core
        and other.family == profile.family
        for name in found
    )


def detected_proxy_apps(processes: dict[int, tuple[str, int]] | None = None, is_mac: bool = False, is_linux: bool = False) -> dict[str, Path | None]:
    if is_mac:
        result: dict[str, Path | None] = dict(macos.proxy_apps())
        for name, path in macos.config_files():
            try:
                if path.is_file():
                    result.setdefault(name, None)
            except OSError:
                pass
        if processes:
            for pid in processes:
                label = identify_process_proxy(pid, processes)
                if label and not _hide_standalone_core(label, result):
                    result.setdefault(label, None)
        return result

    if is_linux:
        result = dict(linux.proxy_apps())
        for name, path in linux.config_files():
            try:
                if path.is_file():
                    result.setdefault(name, None)
            except OSError:
                pass
        if processes:
            for pid in processes:
                label = identify_process_proxy(pid, processes)
                if not label or _hide_standalone_core(label, result):
                    continue
                if not result.get(label):
                    result[label] = process_executable_path(pid) or result.get(label)
            for _wl_pid, (wl_name, _wl_family, _wl_cores, wl_exe) in scan_whitelabel_processes(processes, is_linux=True).items():
                result.setdefault(wl_name, wl_exe)
        return result

    program_roots = [Path(p) for key in ("ProgramFiles", "ProgramFiles(x86)") if (p := os.environ.get(key))]
    scoop_apps = HOME / "scoop/apps"
    roots = (*program_roots, LOCAL / "Programs", LOCAL)
    found: dict[str, Path | None] = {}

    # 1. Standard install directories, Scoop, & PATH (no hardcoded personal folders)
    for profile in PROFILES:
        candidates: list[Path] = []
        for folder in profile.win_dirs:
            for exe in profile.win_exes:
                for root in roots:
                    candidates.append(root / folder / exe)
                candidates.append(scoop_apps / folder.lower().replace(" ", "-") / "current" / exe)
        if profile.standalone_core:
            for exe in profile.win_exes:
                if resolved := shutil.which(exe):
                    candidates.append(Path(resolved))
        binary = first_file(*candidates)
        if binary:
            found[profile.name] = binary

    # 2. Windows Uninstall Registry
    for name, location in registered_proxy_apps().items():
        if found.get(name):
            continue
        binary = None
        if location:
            if location.is_file() and location.suffix.lower() == ".exe":
                binary = location
            elif location.is_dir():
                profile = PROFILE_BY_NAME.get(name)
                if profile:
                    binary = first_file(*(location / exe for exe in profile.win_exes))
        found.setdefault(name, binary)

    # 3. Config files on disk
    for name, config in config_files(is_mac=False):
        try:
            if config.is_file():
                found.setdefault(name, None)
        except OSError:
            pass

    # 4. Live running proxy processes (also resolves portable app binary path!)
    if processes is not None:
        for pid, (exe_name, _parent) in processes.items():
            label = identify_process_proxy(pid, processes)
            if not label or _hide_standalone_core(label, found):
                continue
            if not found.get(label):
                profile = PROFILE_BY_NAME.get(label)
                exe_path = None
                if profile and exe_name.casefold() in {e.casefold() for e in profile.win_exes}:
                    exe_path = process_executable_path(pid)
                found[label] = exe_path or found.get(label)
        for _wl_pid, (wl_name, _wl_family, _wl_cores, wl_exe) in scan_whitelabel_processes(processes, is_mac=False).items():
            found.setdefault(wl_name, wl_exe)
    return found


def discover_proxies(
    processes: dict[int, tuple[str, int]],
    listeners: list[tuple[int, str, int]],
    sys_port: int | None,
    env_ports: list[int],
    static_controllers: list[Controller],
    custom: Controller | None = None,
    *,
    is_mac: bool = False,
    is_linux: bool = False,
    detected_apps: dict[str, Path | None] | None = None,
    probe_controller=probe_local_controller,
    exclude_ports: set[int] | None = None,
) -> DiscoverySnapshot:
    """Universally discover running Clash/Mihomo controllers and generic HTTP proxy ports."""
    apps = dict(detected_apps) if detected_apps is not None else detected_proxy_apps(processes, is_mac=is_mac, is_linux=is_linux)
    hints: list[str] = []
    excluded = set(exclude_ports or ())
    self_pid = os.getpid()
    is_unix = is_mac or is_linux

    # Map listening ports -> owning PIDs and process labels (ignoring our own relay ports)
    port_pids: dict[int, list[int]] = {}
    pid_ports: dict[int, list[int]] = {}
    filtered_listeners: list[tuple[int, str, int]] = []
    for pid, ip, port in listeners:
        if pid == self_pid or port in excluded:
            continue
        filtered_listeners.append((pid, ip, port))
        port_pids.setdefault(port, []).append(pid)
        pid_ports.setdefault(pid, []).append(port)
    listeners = filtered_listeners
    if sys_port in excluded:
        sys_port = None
    env_ports = [p for p in env_ports if p not in excluded]

    # Determine PIDs that belong to known proxy processes, white-label forks, or own system/env/common proxy ports
    whitelabel = scan_whitelabel_processes(processes, is_mac=is_mac, is_linux=is_linux)
    for wl_pid, (wl_name, _wl_family, _wl_cores, wl_exe) in whitelabel.items():
        apps.setdefault(wl_name, wl_exe)

    # Identify GUI shell PIDs that spawn a separate proxy core binary (e.g. baicai.exe -> FlClashCore.exe,
    # clash-verge.exe -> verge-mihomo.exe). Their random ephemeral ports (e.g. :13113, :6979, :33331) are
    # internal GUI<->Core IPC sockets; probing them with HTTP can kill FlClash IPC ("socket done")!
    gui_shell_pids: set[int] = set()
    for wl_pid, (_wl_name, _wl_family, wl_cores, _wl_exe) in whitelabel.items():
        if wl_cores:
            gui_shell_pids.add(wl_pid)
    gui_shell_exes = {
        exe.casefold()
        for prof in PROFILES
        if prof.win_cores
        for exe in prof.win_exes
        if exe.casefold() not in {c.casefold() for c in prof.win_cores}
    }
    if is_linux:
        gui_shell_exes.update({
            "clash-verge", "clash-verge-rev", "flclash", "mihomo-party", "clash-party",
            "clash-nyanpasu", "sparkle", "clashmi", "hiddify", "nekobox", "nekoray", "throne", "v2rayn", "karing",
        })
    for pid, (proc_name, _ppid) in processes.items():
        if proc_name.casefold() in gui_shell_exes:
            gui_shell_pids.add(pid)
    # Universal framework guard: ANY unknown Flutter/Electron/Tauri/Qt GUI process listening on ports
    # is treated as a GUI shell so we never poke arbitrary ephemeral IPC ports on any desktop client.
    for pid in {p for p, _, _ in listeners if p > 0}:
        if pid in gui_shell_pids or identify_process_proxy(pid, processes):
            continue
        exe = process_executable_path(pid)
        if exe and exe.is_file() and is_gui_framework_executable(exe, is_mac=is_mac, is_linux=is_linux):
            gui_shell_pids.add(pid)

    def _safe_to_probe(pid: int, port: int) -> bool:
        if pid not in gui_shell_pids:
            return True
        return port == sys_port or port in env_ports or port in COMMON_PROXY_PORTS

    running_proxy_names: set[str] = set()
    for pid in processes:
        ident = identify_process_proxy(pid, processes)
        if ident:
            running_proxy_names.add(ident)

    proxy_pids: dict[int, str] = {}
    for pid in {p for p, _, _ in listeners if p > 0}:
        label = identify_process_proxy(pid, processes)
        if label:
            # If a known core (e.g. FlClashCore.exe) was spawned by a white-label GUI, prefer the white-label brand name
            parent = processes.get(pid, ("", 0))[1]
            if parent in whitelabel:
                label = whitelabel[parent][0]
            proxy_pids[pid] = label
        elif pid in whitelabel:
            proxy_pids[pid] = whitelabel[pid][0]

    def infer_port_label(port: int, owning_pid: int | None, fallback: str) -> str:
        if owning_pid and owning_pid > 0:
            if owning_pid in proxy_pids:
                return proxy_pids[owning_pid]
            return fallback_process_label(owning_pid, processes, fallback)
        # When TUN/systemd core runs as root on macOS/Linux, non-root inspection cannot map its listening PID;
        # match against running proxy profiles' well-known default ports first.
        for name in running_proxy_names:
            profile = PROFILE_BY_NAME.get(name)
            if profile and port in profile.default_ports:
                return name
        if len(running_proxy_names) == 1:
            return next(iter(running_proxy_names))
        return fallback

    # Protocol-first: also inspect any unknown non-system process listening on a common proxy port
    for pid, _ip, port in listeners:
        if pid <= 0 or pid in proxy_pids or port not in COMMON_PROXY_PORTS:
            continue
        proc_name = processes.get(pid, ("", 0))[0].casefold()
        if proc_name and proc_name not in _NON_PROXY_EXES:
            proxy_pids[pid] = fallback_process_label(pid, processes, f"本机代理 :{port}")

    sys_pids: set[int] = set()
    for special_port, fallback_name in [
        *([(sys_port, "系统代理")] if sys_port else []),
        *((port, "环境变量代理") for port in env_ports),
    ]:
        for pid in port_pids.get(special_port, ()):
            if pid <= 0:
                continue
            sys_pids.add(pid)
            parent = processes.get(pid, ("", 0))[1]
            if parent in pid_ports:
                sys_pids.add(parent)
            # Also include children of the parent (e.g. GUI spawned core + controller)
            for child_pid, (_, child_parent) in processes.items():
                if child_parent in {pid, parent} and child_pid in pid_ports:
                    sys_pids.add(child_pid)
            proxy_pids.setdefault(pid, fallback_process_label(pid, processes, fallback_name))

    for pid in sys_pids:
        if pid not in proxy_pids:
            proxy_pids[pid] = fallback_process_label(pid, processes, "系统代理")

    # 1. Start with custom controller, active OS pipes/sockets, and static config controllers
    controllers: list[Controller] = []
    if custom:
        controllers.append(custom)
    existing_pipes: set[str] | None = None
    if not is_unix:
        existing_pipes = windows_existing_pipes()
        controllers.extend(windows_active_proxy_pipes(processes))
    for ctrl in static_controllers:
        # Skip non-existent named pipes / unix sockets in auto-discovery to avoid timeouts.
        if not is_unix and ctrl.pipe and existing_pipes is not None and ctrl.pipe.casefold() not in existing_pipes:
            continue
        if is_unix and ctrl.unix and not Path(ctrl.unix).is_socket():
            continue
        controllers.append(ctrl)

    known_controller_ports = {c.port for c in controllers if c.port}

    # 2. Probe listening ports owned by proxy processes (and root-owned Linux common proxy ports)
    probe_candidates: list[tuple[int, str, int]] = []
    seen_probe_ports: set[int] = set()
    for pid, _ip, port in listeners:
        if port in known_controller_ports or port in seen_probe_ports:
            continue
        if pid > 0:
            if pid not in proxy_pids or not _safe_to_probe(pid, port):
                continue
            seen_probe_ports.add(port)
            probe_candidates.append((port, proxy_pids[pid], pid))
        elif is_linux and (port in COMMON_PROXY_PORTS or port == sys_port or port in env_ports):
            seen_probe_ports.add(port)
            probe_candidates.append((port, infer_port_label(port, None, f"本机代理 :{port}"), 0))

    protected_controller_ports: dict[int, str] = {}
    discovered_controller_ports: set[int] = set(known_controller_ports)
    controller_sibling_ports: dict[Controller, list[int]] = {}

    if probe_candidates:
        def _probe(item: tuple[int, str, int]):
            port, label, pid = item
            ctrl, needs_secret = probe_controller(port, label)
            return port, label, pid, ctrl, needs_secret

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(12, len(probe_candidates))) as pool:
            for port, label, pid, ctrl, needs_secret in pool.map(_probe, probe_candidates):
                if ctrl:
                    controllers.append(ctrl)
                    discovered_controller_ports.add(port)
                    siblings = _sibling_ports(pid, port, processes, pid_ports, sys_port)
                    if siblings:
                        controller_sibling_ports[ctrl] = siblings
                elif needs_secret:
                    protected_controller_ports[port] = label
                    discovered_controller_ports.add(port)

    # Also attach sibling ports for static HTTP controllers (useful for Sing-box controllers with port=0)
    for ctrl in controllers:
        if ctrl.port and ctrl not in controller_sibling_ports:
            for pid in port_pids.get(ctrl.port, ()):
                siblings = _sibling_ports(pid, ctrl.port, processes, pid_ports, sys_port)
                if siblings:
                    controller_sibling_ports[ctrl] = siblings
                    break

    for port, label in sorted(protected_controller_ports.items()):
        hints.append(
            f"检测到 {label} 的控制接口 127.0.0.1:{port} 需要密钥；"
            "如需核对分流规则或自动切换节点/TUN，可在「控制接口」填写该端口和密钥。"
        )

    # 3. Collect generic HTTP/mixed proxy port candidates (excluding controller API ports)
    http_proxies: list[HTTPProxyCandidate] = []
    seen_http_ports: set[int] = set()

    def add_http(port: int, name: str, source: str, pid: int | None = None):
        if port in seen_http_ports or port in discovered_controller_ports:
            return
        seen_http_ports.add(port)
        http_proxies.append(HTTPProxyCandidate(port=port, name=name, source=source, pid=pid if (pid and pid > 0) else None))
        if name not in {"系统代理", "环境变量代理"}:
            apps.setdefault(name, None)

    running_names_low = {name.casefold() for name, _ in processes.values()}
    idle_whitelabel: list[tuple[str, str]] = []
    for _wl_pid, (wl_name, wl_family, wl_cores, _wl_exe) in whitelabel.items():
        if wl_cores and not any(c in running_names_low for c in wl_cores):
            if (wl_name, wl_family) not in idle_whitelabel:
                idle_whitelabel.append((wl_name, wl_family))

    if sys_port:
        sys_port_pids = port_pids.get(sys_port, [])
        owning_pid = next((p for p in sys_port_pids if p > 0), None)
        port_is_listening = bool(sys_port_pids)
        # On Windows (GetExtendedTcpTable) and Linux (/proc/net/tcp), all listening ports are visible even for root/SYSTEM.
        # If listeners were populated and sys_port is NOT listening at all, do NOT misattribute a dead port.
        if not is_mac and listeners and not port_is_listening:
            if idle_whitelabel:
                wl_name, wl_family = idle_whitelabel[0]
                elev_tip = "检查服务模式/权限" if is_linux else "右键以管理员身份运行该软件"
                hints.append(
                    f"系统代理指向 127.0.0.1:{sys_port} 但当前无人监听；"
                    f"检测到「{wl_name}」（{wl_family} 内核）外壳在运行但代理内核未启动，"
                    f"请{elev_tip}（或先关闭其 TUN 模式测试）、检查套餐流量，并关闭其他代理的 TUN 冲突。"
                )
            else:
                hints.append(
                    f"系统代理指向 127.0.0.1:{sys_port}，但当前没有任何进程监听该端口，"
                    "请确认对应代理软件已真正连接节点。"
                )
        else:
            label = infer_port_label(sys_port, owning_pid, "系统代理")
            add_http(sys_port, label, "system", owning_pid)
    elif idle_whitelabel:
        for wl_name, wl_family in idle_whitelabel:
            hints.append(
                f"检测到「{wl_name}」（{wl_family} 套壳客户端）正在运行，但其代理内核尚未启动或未开放本地端口。"
            )

    for port in env_ports:
        env_port_pids = port_pids.get(port, [])
        owning_pid = next((p for p in env_port_pids if p > 0), None)
        label = infer_port_label(port, owning_pid, "环境变量代理")
        add_http(port, label, "env", owning_pid)

    # Sort process listening ports so known default ports come before arbitrary internal ports
    proc_candidates: list[tuple[int, int, str, int | None]] = []
    for pid, _ip, port in listeners:
        if port in seen_http_ports or port in discovered_controller_ports:
            continue
        if pid > 0:
            if pid not in proxy_pids or not _safe_to_probe(pid, port):
                continue
            label = proxy_pids[pid]
            profile = PROFILE_BY_NAME.get(label)
            is_default = 0 if (profile and port in profile.default_ports) else 1
            proc_candidates.append((is_default, port, label, pid))
        elif is_linux and port in COMMON_PROXY_PORTS:
            label = infer_port_label(port, None, f"本机代理 :{port}")
            profile = PROFILE_BY_NAME.get(label)
            is_default = 0 if (profile and port in profile.default_ports) else 1
            proc_candidates.append((is_default, port, label, None))

    proc_candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    for _, port, label, pid in proc_candidates:
        add_http(port, label, "process", pid)

    # Passively discover active ports configured in ANY FlClash-family fork's shared_preferences.json
    # (only if that port is genuinely listening right now — never adds dead/unstarted ports).
    for fc_brand, fc_port, _fc_tun in discover_flclash_family_configs(is_mac=is_mac, is_linux=is_linux):
        if fc_port in excluded or fc_port in seen_http_ports or fc_port in discovered_controller_ports:
            continue
        owning_pids = port_pids.get(fc_port)
        if not owning_pids:
            continue
        first_pid = next((p for p in owning_pids if p > 0), None)
        owner_proc = processes.get(first_pid, ("", 0))[0].casefold() if first_pid else ""
        if owner_proc in _NON_PROXY_EXES:
            continue
        add_http(fc_port, fc_brand, "process", first_pid)

    # Deduplicate controllers while preserving order
    deduped_controllers: list[Controller] = []
    seen_ctrls: set[tuple[str, int | None, str]] = set()
    for ctrl in controllers:
        key = (ctrl.pipe.casefold(), ctrl.port, ctrl.unix)
        if key not in seen_ctrls:
            seen_ctrls.add(key)
            deduped_controllers.append(ctrl)

    return DiscoverySnapshot(
        controllers=deduped_controllers,
        http_proxies=http_proxies,
        detected_apps=apps,
        hints=hints,
        controller_sibling_ports=controller_sibling_ports,
    )


def _sibling_ports(
    pid: int,
    exclude_port: int,
    processes: dict[int, tuple[str, int]],
    pid_ports: dict[int, list[int]],
    sys_port: int | None,
) -> list[int]:
    if pid <= 0:
        ports = [
            p for p in pid_ports.get(0, ())
            if p != exclude_port and (p == sys_port or p in COMMON_PROXY_PORTS)
        ]
        ports = list(dict.fromkeys(ports))
        if sys_port and sys_port in ports:
            ports.remove(sys_port)
            ports.insert(0, sys_port)
        return ports
    family = {pid}
    parent = processes.get(pid, ("", 0))[1]
    if parent:
        family.add(parent)
    for child, (_, child_parent) in processes.items():
        if child_parent in family:
            family.add(child)
    ports = [p for member in family for p in pid_ports.get(member, ()) if p != exclude_port]
    ports = list(dict.fromkeys(ports))
    if sys_port and sys_port in ports:
        ports.remove(sys_port)
        ports.insert(0, sys_port)
    return ports
