from __future__ import annotations

import argparse
import base64
import concurrent.futures
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field, replace
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse

if sys.platform == "win32":
    import winreg
import linux
import macos
import proxies
import relay
import win32net
from proxies import (
    CLASH_HOME,
    Controller,
    JIMMM_CONTROLLER,
    PIPE_NAME,
    PIPE_PREFIX,
)


DEFAULT_PORTS = {"JImmm": 9674, "Clash": 7897}
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
AUTO = "自动检测"
MANUAL = "手动指定端口"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
HOME = Path.home()
LOCAL = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local"))
ROAMING = Path(os.environ.get("APPDATA", HOME / "AppData/Roaming"))
DATA = macos.DATA if IS_MAC else (linux.DATA if IS_LINUX else LOCAL / "AIProxyLauncher")
WINDOWS = Path(os.environ.get("SystemRoot", "C:/Windows"))
MAX_CONTROLLER_RESPONSE = 8 * 1024 * 1024
OAUTH_TOKEN = "oauth2.googleapis.com/token"
DAILY_CODE_ASSIST = "daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
GOOGLE = ("accounts.google.com", OAUTH_TOKEN, DAILY_CODE_ASSIST, "cloudcode-pa.googleapis.com", "businessaicode.googleapis.com")
CLAUDE = ("claude.ai",)
CHATGPT = ("chatgpt.com", "ws.chatgpt.com")
CODEX_RESPONSES = "chatgpt.com/backend-api/codex/responses"
LABELS = {
    "proxy": "节点代理",
    "http_proxy": "代理连通",
    "socks5_proxy": "SOCKS5 代理连通",
    "tun_route": "系统路由连通",
    "direct": "直连",
    "unknown": "未确认出口",
}
PASSING_ROUTES = {"proxy", "http_proxy", "socks5_proxy", "tun_route"}
SUPPORTED_WIN_BROWSERS = {
    "msedge.exe": "Microsoft Edge",
    "chrome.exe": "Google Chrome",
    "brave.exe": "Brave",
    "vivaldi.exe": "Vivaldi",
    "chromium.exe": "Chromium",
    "arc.exe": "Arc",
    "opera.exe": "Opera",
}


@dataclass(frozen=True)
class Target:
    name: str
    kind: str
    hosts: tuple[str, ...]
    description: str
    url: str = ""
    app_id: str = ""
    custom: bool = False


TARGETS: dict[str, Target] = {
    "ide": Target("Antigravity IDE", "electron", GOOGLE, "用代理参数启动编辑器"),
    "agent": Target("Antigravity Agent", "electron", GOOGLE, "用代理参数启动独立客户端"),
    "antigravity_web": Target("Antigravity 网页版", "web", ("antigravity.google.com", "accounts.google.com"),
                              "独立浏览器窗口", "https://antigravity.google.com/"),
    "gemini": Target("Gemini 网页版", "web", ("gemini.google.com", "accounts.google.com"),
                     "独立浏览器窗口", "https://gemini.google.com/app"),
    "claude": Target("Claude 网页版", "web", CLAUDE, "独立浏览器窗口", "https://claude.ai/"),
    "chatgpt": Target("ChatGPT 网页版", "web", CHATGPT, "独立浏览器窗口", "https://chatgpt.com/"),
    "code": Target("Claude Code", "cli", ("api.anthropic.com",), "在终端里用代理变量启动"),
    "claude_desktop": Target("Claude 桌面版", "store", CLAUDE + ("api.anthropic.com",), "通过代理软件的虚拟网卡联网", app_id="Claude_pzs8sxrjxfjjc!Claude"),
    "chatgpt_desktop": Target("ChatGPT / Codex 桌面版", "codex", (*CHATGPT, CODEX_RESPONSES), "启动时更新 Codex 的代理端口", app_id="OpenAI.Codex_2p2nqsd0c76g0!App"),
}

PRESET_TARGETS: dict[str, Target] = {
    "cursor": Target("Cursor IDE", "electron", ("api2.cursor.sh",), "用代理参数启动 Cursor"),
    "windsurf": Target("Windsurf IDE", "electron", ("codeium.com",), "用代理参数启动 Windsurf"),
    "vscode": Target("VS Code", "electron", ("api.githubcopilot.com",), "用代理参数启动 VS Code"),
    "gemini_cli": Target("Gemini CLI", "cli", ("generativelanguage.googleapis.com",), "在终端里用代理变量启动 Gemini CLI"),
    "codex_cli": Target("Codex CLI", "cli", ("api.openai.com",), "在终端里用代理变量启动 Codex CLI"),
    "grok": Target("Grok 网页版", "web", ("grok.com",), "独立浏览器窗口", "https://grok.com/"),
    "perplexity": Target("Perplexity 网页版", "web", ("www.perplexity.ai",), "独立浏览器窗口", "https://www.perplexity.ai/"),
}

ALL_BUILTIN_TARGETS: dict[str, Target] = {**TARGETS, **PRESET_TARGETS}


@dataclass(frozen=True)
class Probe:
    host: str
    connected: bool
    detail: str
    route: str = "unknown"
    latency_ms: int | None = None
    node: str = ""
    region: str = ""


class AlreadyRunningError(ValueError):
    """Raised when a single-instance desktop app is already running and cannot take new startup flags."""


@dataclass(frozen=True)
class Report:
    mode: str
    tun: bool | None
    probes: dict[str, Probe]
    port: int
    issue: str = ""
    provider: str = ""
    detected: tuple[str, ...] = ()
    endpoint: Controller | None = field(default=None, repr=False)
    region: str = ""
    latency_ms: int | None = None
    scheme: str = "http"


def parse_port(value: str) -> int:
    match = re.fullmatch(r"(?:(?:http://)?(?:127\.0\.0\.1|localhost):)?([0-9]{1,5})", value.strip(), re.I)
    if match and 1 <= int(match[1]) <= 65535:
        return int(match[1])
    raise ValueError("请输入本机 HTTP 代理端口，例如 7890 或 10808。")


def proxy_env(port: int, base=None) -> dict[str, str]:
    base = os.environ if base is None else base
    keys = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
    env = {key: value for key, value in base.items() if key.upper() not in keys}
    if port > 0:
        proxy = f"http://127.0.0.1:{port}"
        no_proxy = "localhost,127.0.0.1,::1"
        env.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, ALL_PROXY=proxy, NO_PROXY=no_proxy)
        if IS_LINUX:
            env.update(http_proxy=proxy, https_proxy=proxy, all_proxy=proxy, no_proxy=no_proxy)
    return env


def chromium_args(port: int) -> list[str]:
    if port <= 0:
        return ["--disable-quic"]
    return [f"--proxy-server=http://127.0.0.1:{port}", "--proxy-bypass-list=localhost;127.0.0.1;[::1]", "--disable-quic"]


def first_file(*paths: Path) -> Path | None:
    for path in paths:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def parse_custom_targets(saved: dict) -> tuple[dict[str, Target], list[str], set[str]]:
    """Load enabled preset keys, custom targets, and hidden target keys from saved settings."""
    enabled_presets = [
        key for key in (saved.get("enabled_presets") or ())
        if isinstance(key, str) and key in PRESET_TARGETS
    ]
    hidden = {
        key for key in (saved.get("hidden_targets") or ())
        if isinstance(key, str)
    }
    custom: dict[str, Target] = {}
    for item in saved.get("custom_targets") or ():
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        name = str(item.get("name") or "").strip()
        kind = str(item.get("kind") or "").strip()
        if not key or not name or kind not in {"web", "electron", "cli"} or key in ALL_BUILTIN_TARGETS:
            continue
        url = str(item.get("url") or "").strip()
        raw_hosts = item.get("hosts")
        hosts: list[str] = []
        if isinstance(raw_hosts, list):
            for h in raw_hosts:
                text = str(h).strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
                if text:
                    hosts.append(text)
        if not hosts and url:
            parsed = urlparse(url if "://" in url else f"https://{url}")
            if parsed.hostname:
                hosts.append(parsed.hostname.lower())
        if not hosts:
            continue
        desc = str(item.get("description") or "").strip() or (
            "独立浏览器窗口" if kind == "web" else ("在终端里用代理变量启动" if kind == "cli" else "用代理参数启动应用")
        )
        custom[key] = Target(name, kind, tuple(dict.fromkeys(hosts)), desc, url=url, custom=True)
    return custom, enabled_presets, hidden


def build_active_targets(custom: dict[str, Target] | None = None, enabled_presets: list[str] | None = None,
                         hidden: set[str] | None = None) -> dict[str, Target]:
    result: dict[str, Target] = {}
    hidden_set = hidden or set()
    for key, target in TARGETS.items():
        if key not in hidden_set:
            result[key] = target
    for key in enabled_presets or ():
        if key in PRESET_TARGETS and key not in hidden_set:
            result[key] = PRESET_TARGETS[key]
    for key, target in (custom or {}).items():
        if key not in hidden_set:
            result[key] = target
    return result or dict(TARGETS)


def serialize_custom_targets(custom: dict[str, Target]) -> list[dict]:
    return [
        {
            "key": key,
            "name": target.name,
            "kind": target.kind,
            "hosts": list(target.hosts),
            "description": target.description,
            "url": target.url,
        }
        for key, target in custom.items()
        if target.custom
    ]


def _running_executable(exe_names: tuple[str, ...]) -> Path | None:
    if IS_MAC or (not IS_LINUX and sys.platform != "win32"):
        return None
    wanted = {name.casefold() for name in exe_names}
    try:
        for pid, (name, _parent) in process_snapshot().items():
            if name.casefold() in wanted:
                resolved = linux.process_executable_path(pid) if IS_LINUX else proxies.process_executable_path(pid)
                if resolved:
                    return resolved
    except OSError:
        pass
    return None


def _which_cli(*names: str) -> Path | None:
    for name in names:
        if resolved := shutil.which(name):
            return Path(resolved)
    return None


def detect_paths() -> dict[str, Path | None]:
    if IS_MAC:
        return macos.detect_paths()
    if IS_LINUX:
        return linux.detect_paths()
    program_roots = [Path(p) for key in ("ProgramFiles(x86)", "ProgramFiles") if (p := os.environ.get(key))]
    browser_paths = (
        "Microsoft/Edge/Application/msedge.exe",
        "Google/Chrome/Application/chrome.exe",
        "BraveSoftware/Brave-Browser/Application/brave.exe",
        "Vivaldi/Application/vivaldi.exe",
        "Chromium/Application/chrome.exe",
    )
    return {
        "ide": (
            first_file(LOCAL / "Programs/Antigravity IDE/Antigravity IDE.exe")
            or proxies.find_registered_app(("antigravity ide", "google antigravity ide"), ("Antigravity IDE.exe",))
            or _running_executable(("Antigravity IDE.exe",))
        ),
        "agent": (
            first_file(LOCAL / "Programs/Antigravity/Antigravity.exe")
            or proxies.find_registered_app(("antigravity", "google antigravity", "antigravity agent"), ("Antigravity.exe",))
            or _running_executable(("Antigravity.exe",))
        ),
        "cursor": (
            first_file(LOCAL / "Programs/cursor/Cursor.exe", *(r / "cursor/Cursor.exe" for r in program_roots))
            or proxies.find_registered_app(("cursor",), ("Cursor.exe",))
            or _running_executable(("Cursor.exe",))
        ),
        "windsurf": (
            first_file(LOCAL / "Programs/Windsurf/Windsurf.exe", *(r / "Windsurf/Windsurf.exe" for r in program_roots))
            or proxies.find_registered_app(("windsurf",), ("Windsurf.exe",))
            or _running_executable(("Windsurf.exe",))
        ),
        "vscode": (
            first_file(LOCAL / "Programs/Microsoft VS Code/Code.exe", *(r / "Microsoft VS Code/Code.exe" for r in program_roots))
            or proxies.find_registered_app(("microsoft visual studio code",), ("Code.exe",))
            or _running_executable(("Code.exe",))
        ),
        "code": first_file(
            ROAMING / "npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
            HOME / ".local/bin/claude.exe",
            _which_cli("claude.exe", "claude.cmd") or HOME / ".claude/bin/claude.exe",
        ),
        "gemini_cli": _which_cli("gemini.exe", "gemini.cmd"),
        "codex_cli": _which_cli("codex.exe", "codex.cmd"),
        "web": first_file(*(root / name for name in browser_paths for root in (*program_roots, LOCAL))),
    }


def registered_proxy_apps() -> dict[str, Path | None]:
    return proxies.registered_proxy_apps()


def detected_proxy_apps() -> dict[str, Path | None]:
    try:
        snapshot = process_snapshot()
    except (OSError, subprocess.SubprocessError):
        snapshot = None
    return proxies.detected_proxy_apps(snapshot, is_mac=IS_MAC, is_linux=IS_LINUX)


def proxy_preferences(saved: dict) -> tuple[str, dict[str, str]]:
    selected = saved.get("provider")
    valid = isinstance(selected, str) and bool(selected.strip())
    provider = selected.strip() if saved.get("automatic_detection") is True and valid else AUTO
    ports = saved.get("ports")
    if not isinstance(ports, dict):
        ports = {"JImmm": saved.get("port")}
    result = {key: str(ports.get(key) or "") for key in DEFAULT_PORTS}
    for key, value in ports.items():
        if isinstance(key, str) and key not in result:
            result[key] = str(value or "")
    return provider, result


def read_settings(path: Path) -> dict:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        return saved if isinstance(saved, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
        tmp = Path(output.name)
        try:
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.close()
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def clash_config_files() -> list[tuple[str, Path]]:
    return proxies.config_files(is_mac=IS_MAC, is_linux=IS_LINUX)


def _provider_matches(name: str, provider: str | None) -> bool:
    if not provider or provider == AUTO:
        return True
    if provider == "JImmm":
        return name in {"JImmm", "Clash Party"} if (IS_MAC or IS_LINUX) else name == "JImmm"
    if provider == "Clash":
        return name != "JImmm"
    return name.casefold() == provider.casefold()


def proxy_controllers(provider: str | None = None) -> list[Controller]:
    if provider == "JImmm" and not IS_MAC and not IS_LINUX:
        return [JIMMM_CONTROLLER]
    pipes: list[Controller] = []
    http: list[Controller] = []
    for name, path in clash_config_files():
        if not _provider_matches(name, provider):
            continue
        if path.suffix.lower() == ".json":
            http.extend(proxies.parse_json_controller_file(path, name))
        else:
            found_pipes, found_http = proxies.parse_yaml_controller_file(path, name, is_mac=IS_MAC, is_linux=IS_LINUX)
            for item in found_pipes:
                if provider == "Clash" and item.pipe.casefold() == JIMMM_CONTROLLER.pipe.casefold():
                    continue
                pipes.append(item)
            http.extend(found_http)
    if IS_MAC:
        for name, path in macos.sockets():
            if _provider_matches(name, provider) and path.is_socket():
                pipes.append(Controller(unix=str(path), name=name))
    elif IS_LINUX:
        for name, path in linux.sockets():
            if _provider_matches(name, provider) and path.is_socket():
                pipes.append(Controller(unix=str(path), name=name))
    elif provider in {None, AUTO, "Clash", "Clash Verge Rev"}:
        pipes.append(Controller(pipe="verge-mihomo", name="Clash Verge Rev"))
    return list(dict.fromkeys([*pipes, *http]))


def clash_controllers() -> list[Controller]:
    return proxy_controllers("Clash")


def all_controllers() -> list[Controller]:
    return list(dict.fromkeys([*proxy_controllers("JImmm"), *clash_controllers()]))


def detect_system_tun() -> bool:
    return proxies.detect_system_tun(is_mac=IS_MAC, is_linux=IS_LINUX)


def discover_live_proxies(custom: Controller | None = None,
                          exclude_ports: set[int] | None = None) -> proxies.DiscoverySnapshot:
    try:
        processes = process_snapshot()
    except (OSError, subprocess.SubprocessError):
        processes = {}
    listeners = proxies.listening_tcp_ports(is_mac=IS_MAC, is_linux=IS_LINUX)
    sys_port = system_proxy_port()
    env_ports = proxies.env_proxy_ports()
    static_controllers = all_controllers()
    detected = proxies.detected_proxy_apps(processes, is_mac=IS_MAC, is_linux=IS_LINUX)
    return proxies.discover_proxies(
        processes,
        listeners,
        sys_port,
        env_ports,
        static_controllers,
        custom,
        is_mac=IS_MAC,
        is_linux=IS_LINUX,
        detected_apps=detected,
        exclude_ports=exclude_ports,
    )


def controller(path: str, endpoint: Controller) -> dict:
    if path not in {"/configs", "/connections", "/proxies", "/rules"}:
        raise ValueError("Unsupported proxy status endpoint")
    return controller_request(path, endpoint, "GET", None, timeout=4) or {}


def controller_request(path: str, endpoint: Controller, method: str, data: dict | None,
                       timeout: int) -> dict | None:
    body = json.dumps(data, ensure_ascii=True).encode("ascii") if data is not None else b""
    headers = {"Content-Type": "application/json", "Content-Length": str(len(body))} if body else {}
    if endpoint.port:
        return http_controller(path, endpoint, method, body, headers, timeout)
    if endpoint.unix:
        return http_controller(path, endpoint, method, body, headers, timeout)
    if not PIPE_NAME.fullmatch(endpoint.pipe):
        raise ValueError("Unsupported local pipe")
    wire = f"{method} {path} HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n"
    wire += "".join(f"{name}: {value}\r\n" for name, value in headers.items())
    raw_wire = (wire + "\r\n").encode("ascii") + body
    try:
        raw = win32net.pipe_http_request(endpoint.pipe, raw_wire, timeout=float(timeout), max_bytes=MAX_CONTROLLER_RESPONSE)
        if raw is None:
            return None
        resp_headers, separator, resp_body = raw.partition(b"\r\n\r\n")
        if separator and resp_headers.splitlines()[0].split()[1] in {b"200", b"204"}:
            return json_object(resp_body) if resp_body else {}
    except (OSError, ValueError, IndexError):
        pass
    return None


def json_object(body: bytes) -> dict:
    data = json.loads(body)
    return data if isinstance(data, dict) else {}


def http_controller(path: str, endpoint: Controller, method: str, body: bytes, headers: dict, timeout: int) -> dict | None:
    class UnixConnection(http.client.HTTPConnection):
        def connect(self):
            info = os.stat(endpoint.unix)
            owner_ok = not hasattr(os, "getuid") or info.st_uid in {0, os.getuid()}
            if not stat.S_ISSOCK(info.st_mode) or not owner_ok:
                raise OSError("Invalid local socket")
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(timeout)
            self.sock.connect(endpoint.unix)

    connection = (UnixConnection("localhost", timeout=timeout) if endpoint.unix
                  else http.client.HTTPConnection("127.0.0.1", endpoint.port, timeout=timeout))
    if endpoint.secret:
        headers["Authorization"] = f"Bearer {endpoint.secret}"
    try:
        connection.request(method, path, body or None, headers=headers)
        with connection.getresponse() as response:
            if response.status == 204:
                return {}
            if response.status == 200:
                body = response.read(MAX_CONTROLLER_RESPONSE + 1)
                if len(body) <= MAX_CONTROLLER_RESPONSE:
                    return json_object(body)
    except (OSError, http.client.HTTPException, ValueError):
        pass
    finally:
        connection.close()
    return None


def observed_route_detail(snapshot: dict, host: str, source_port: int, inbound_port: int) -> tuple[str, str]:
    """Return (route_kind, matched_exit_node_name) from a controller /connections snapshot."""
    for row in snapshot.get("connections") or []:
        meta = row.get("metadata", {})
        inbound = str(meta.get("inboundPort") or "").strip()
        if (meta.get("sourceIP") not in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
                or str(meta.get("sourcePort")) != str(source_port)
                or meta.get("host", "").lower().rstrip(".") != host
                or (inbound and inbound != "0" and inbound != str(inbound_port))):
            continue
        chains = [c for c in (row.get("chains") or ()) if isinstance(c, str) and c.strip()]
        node = chains[0].strip() if chains else ""
        return connection_route(row), node
    return "unknown", ""


def observed_route(snapshot: dict, host: str, source_port: int, inbound_port: int) -> str:
    return observed_route_detail(snapshot, host, source_port, inbound_port)[0]


def connection_route(row: dict) -> str:
    chain = {name.upper() for name in row.get("chains") or [] if isinstance(name, str)}
    if chain & {"DIRECT", "COMPATIBLE", "PASS"}:
        return "direct"
    return "proxy" if chain and not chain & {"REJECT", "REJECT-DROP", "DNS"} else "unknown"


def tcp_connections() -> list[tuple[int, int, str, int]]:
    if IS_MAC:
        return macos.tcp_connections()
    if IS_LINUX:
        return linux.tcp_connections()
    native = win32net.windows_tcp_tables()
    if native is not None:
        return native[0]
    output = subprocess.check_output([str(WINDOWS / "System32/netstat.exe"), "-ano", "-p", "tcp"],
                                     timeout=5, creationflags=NO_WINDOW)
    sockets = []
    for line in output.decode("ascii", errors="replace").splitlines():
        parts = line.split()
        if len(parts) != 5 or parts[3] != "ESTABLISHED":
            continue
        try:
            remote, remote_port = parts[2].rsplit(":", 1)
            sockets.append((int(parts[4]), int(parts[1].rsplit(":", 1)[1]), remote, int(remote_port)))
        except (ValueError, IndexError):
            continue
    return sockets


def _app_process_map(paths: dict[str, Path | None], targets: dict[str, Target] = TARGETS) -> dict[str, str]:
    default_win = {
        "ide": "Antigravity IDE.exe",
        "agent": "Antigravity.exe",
        "code": "claude.exe",
        "claude_desktop": "Claude.exe",
        "chatgpt_desktop": "ChatGPT.exe",
        "cursor": "Cursor.exe",
        "windsurf": "Windsurf.exe",
        "vscode": "Code.exe",
        "gemini_cli": "gemini.exe",
        "codex_cli": "codex.exe",
    }
    owned: dict[str, str] = {}
    if IS_MAC:
        for key, target in targets.items():
            if target.kind == "web":
                continue
            for name in macos.APP_NAMES.get(key, ()):
                owned[name.casefold()] = key
            path = paths.get(key)
            if path:
                try:
                    binary = macos.executable(path).name
                    if binary.casefold() not in {"electron", "app"}:
                        owned[binary.casefold()] = key
                    owned[path.stem.casefold()] = key
                except ValueError:
                    pass
            elif key == "code":
                owned["claude"] = key
    elif IS_LINUX:
        for key, target in targets.items():
            if target.kind == "web":
                continue
            for name in linux.APP_PROCESS_NAMES.get(key, ()):
                owned[name.casefold()] = key
            path = paths.get(key)
            if path:
                for cand in (path.name, path.resolve(strict=False).name):
                    if cand and cand.casefold() not in {"electron", "app", "sh", "bash", "python", "python3"}:
                        owned[cand.casefold()] = key
    else:
        for key, target in targets.items():
            if target.kind == "web":
                continue
            path = paths.get(key)
            exe = path.name if path else default_win.get(key, "")
            if exe:
                owned[exe.casefold()] = key
    return owned


def running_apps(processes: dict[int, tuple[str, int]], paths: dict[str, Path | None],
                 targets: dict[str, Target] = TARGETS) -> set[str]:
    owned = _app_process_map(paths, targets)
    found: set[str] = set()
    for name, _parent in processes.values():
        key = owned.get(name.casefold())
        if key:
            found.add(key)
    return found


def app_connections(processes: dict[int, tuple[str, int]], sockets: list[tuple[int, int, str, int]],
                    providers: list[tuple[str, int, list[dict] | None]], paths: dict[str, Path | None],
                    targets: dict[str, Target] = TARGETS) -> dict[str, list[str]]:
    owned = _app_process_map(paths, targets)
    browser = {host.split("/", 1)[0]: key for key, target in targets.items() if target.kind == "web"
               for host in target.hosts if host != "accounts.google.com"}
    browser_names = {
        *SUPPORTED_WIN_BROWSERS,
        *(name.casefold() for name in macos.BROWSERS),
        *(name.casefold() for name in linux.BROWSER_PROCESS_NAMES),
    }
    result: dict[str, set[str]] = {}
    unconfirmed: dict[str, set[str]] = {}
    confirmed: dict[str, set[str]] = {}
    local = {"127.0.0.1", "[::1]", "[::ffff:127.0.0.1]"}
    port_info: dict[int, tuple[str, bool]] = {}
    rows_by_port: dict[str, list[tuple[str, int, dict]]] = {}
    for provider, port, rows in providers:
        has_controller = rows is not None
        if port not in port_info or has_controller:
            port_info[port] = (provider, has_controller)
        for row in rows or ():
            metadata = row.get("metadata") or {}
            rows_by_port.setdefault(str(metadata.get("sourcePort")), []).append((provider, port, row))

    for pid, source, remote, port in sockets:
        application = ""
        is_browser = False
        visited = set()
        while pid in processes and pid not in visited:
            visited.add(pid)
            name, pid = processes[pid]
            name = name.casefold()
            if name in owned:
                application = owned[name]
                break
            is_browser |= name in browser_names
        if not application and not is_browser:
            continue
        matched = False
        for provider, proxy_port, row in rows_by_port.get(str(source), ()):
            meta = row["metadata"]
            if str(meta.get("network", "tcp")).lower() != "tcp":
                continue
            tun = str(meta.get("type", "")).casefold() == "tun"
            inbound = str(meta.get("inboundPort") or "").strip()
            if tun:
                if remote in local or str(meta.get("destinationPort")) != str(port):
                    continue
                addresses = {str(meta.get(field) or "") for field in ("destinationIP", "remoteDestination")}
                if remote.startswith("198.18.") and any(ip.startswith("198.18.") for ip in addresses) and remote not in addresses:
                    continue
            elif remote not in local or port != proxy_port or (inbound and inbound != "0" and inbound != str(port)):
                continue
            host = str(meta.get("host") or meta.get("sniffHost") or "").lower().rstrip(".")
            key = application or browser.get(host)
            if key:
                label = f"{provider} TUN" if tun else f"{provider} :{port}"
                route = connection_route(row)
                result.setdefault(key, set()).add(f"{label} · {LABELS[route]}")
                confirmed.setdefault(key, set()).add(label)
                matched = True
        if not matched and application:
            if remote in local and port in port_info:
                provider_name, has_controller = port_info[port]
                label = f"{provider_name} :{port}"
                if has_controller:
                    unconfirmed.setdefault(application, set()).add(label)
                else:
                    result.setdefault(application, set()).add(f"{label} · 代理连接中")
                    confirmed.setdefault(application, set()).add(label)
            elif remote.startswith(("198.18.", "198.19.")):
                result.setdefault(application, set()).add("系统 TUN · 虚拟网卡连接中")
    for key, labels in unconfirmed.items():
        for label in labels - confirmed.get(key, set()):
            result.setdefault(key, set()).add(f"{label} · 出口待核对")
    return {key: sorted(labels) for key, labels in result.items()}


def live_status(paths: dict[str, Path | None], custom: Controller | None,
                targets: dict[str, Target] = TARGETS,
                *, relay_port: int | None = None) -> tuple[dict[str, list[str]], set[str]]:
    snapshot = discover_live_proxies(custom, exclude_ports={relay_port} if relay_port else None)
    endpoints = snapshot.controllers

    def read(endpoint: Controller) -> list[tuple[str, int, list[dict]]]:
        config = controller("/configs", endpoint)
        if not config:
            return []
        ports = _controller_ports(config, endpoint, snapshot.controller_sibling_ports)
        if not ports:
            return []
        name = endpoint.name or ("JImmm" if endpoint == JIMMM_CONTROLLER else "Clash")
        rows = controller("/connections", endpoint).get("connections") or []
        return [(name, port, [row for row in rows if str((row.get("metadata") or {}).get("inboundPort") or ports[0])
                             in {str(port), "0"}]) for port in ports]

    processes = process_snapshot()
    running = running_apps(processes, paths, targets)
    sockets = tcp_connections()
    reports: dict[int, tuple[str, int, list[dict] | None]] = {}
    if endpoints:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(endpoints))) as pool:
            for entries in pool.map(read, endpoints):
                for name, port, rows in entries:
                    reports.setdefault(port, (name, port, rows))
    for candidate in snapshot.http_proxies:
        reports.setdefault(candidate.port, (candidate.name, candidate.port, None))
    if relay_port:
        reports.setdefault(relay_port, ("统一中继", relay_port, None))
    if not reports and not any(remote.startswith(("198.18.", "198.19.")) for _, _, remote, _ in sockets):
        return {}, running
    return app_connections(processes, sockets, list(reports.values()), paths, targets=targets), running


def live_routes(paths: dict[str, Path | None], custom: Controller | None,
                targets: dict[str, Target] = TARGETS) -> dict[str, list[str]]:
    routes, _ = live_status(paths, custom, targets)
    return routes


def _speaks_http_locally(port: int, timeout: float = 0.5) -> bool:
    """Distinguish a real HTTP proxy (slow upstream) from a silent non-HTTP binary IPC port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"GET / HTTP/1.0\r\n\r\n")
            data = sock.recv(16)
            return data.upper().startswith(b"HTTP/1.")
    except OSError:
        return False


def probe_http_proxy_port(port: int, test_host: str = "accounts.google.com", timeout: float = 2.0) -> str:
    """Classify a local listening port as 'http_proxy', 'socks5', 'not_proxy', or 'closed'."""
    host = test_host.split("/", 1)[0]
    authority = f"{host}:443"
    # Phase 1: loopback TCP connect succeeds in <2ms when alive; timeout here means port is closed/unresponsive!
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=min(0.6, timeout))
    except OSError:
        return "closed"
    # Phase 2: send HTTP CONNECT and classify response
    try:
        with sock:
            sock.settimeout(timeout)
            sock.sendall(f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode("ascii"))
            with sock.makefile("rb") as reader:
                line = reader.readline(512)
        if not line:
            return "socks5" if proxies.probe_socks5_port(port) else "not_proxy"
        parts = line.split()
        if len(parts) >= 2 and parts[0].upper().startswith(b"HTTP/1."):
            if parts[1] in {b"200", b"407", b"502", b"503", b"504"}:
                return "http_proxy"
            if parts[1] in {b"400", b"404", b"405", b"501"}:
                return "not_proxy"
            return "http_proxy"
        return "socks5" if proxies.probe_socks5_port(port) else "not_proxy"
    except TimeoutError:
        # Silent binary IPC ports (e.g. FlClash/Flutter UI IPC) time out on HTTP CONNECT;
        # only classify as http_proxy if the listener actually responds to a local HTTP request.
        return "http_proxy" if _speaks_http_locally(port) else "not_proxy"
    except OSError:
        return "socks5" if proxies.probe_socks5_port(port) else "not_proxy"


def open_tunnel(host: str, port: int, timeout: float) -> tuple[socket.socket | None, str]:
    connection = None
    try:
        connection = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        authority = f"{host}:443"
        connection.sendall(f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode("ascii"))
        with connection.makefile("rb") as reader:
            status = reader.readline(4096).split()
            for _ in range(32):
                line = reader.readline(4096)
                if line == b"\r\n":
                    break
                if not line:
                    raise OSError("Incomplete proxy response")
            else:
                raise OSError("Proxy response headers too large")
        if len(status) >= 2 and status[1] == b"200":
            return connection, ""
        code = status[1].decode("ascii", errors="replace") if len(status) >= 2 else "未知"
        detail = f"本机代理拒绝建立连接（HTTP {code}）"
    except ConnectionRefusedError:
        detail = "本机代理端口未开启"
    except TimeoutError:
        detail = "本机代理响应超时"
    except OSError:
        detail = "无法通过本机代理建立连接"
    if connection:
        connection.close()
    return None, detail


def open_socks5_tunnel(host: str, port: int, timeout: float = 8) -> tuple[socket.socket | None, str]:
    try:
        conn = relay.dial_socks5_upstream(port, host, 443, timeout=timeout)
        return conn, ""
    except ConnectionRefusedError:
        return None, "本机 SOCKS5 代理端口未开启"
    except TimeoutError:
        return None, "本机 SOCKS5 代理响应超时"
    except OSError:
        return None, "无法通过本机 SOCKS5 代理建立连接"


def connect_probe(service: str, port: int | None, timeout: float = 8, endpoint: Controller | None = None,
                  *, allow_uncontrolled: bool = False, scheme: str = "http") -> Probe:
    started = time.perf_counter()
    host, _, path = service.partition("/")
    if port is None:
        try:
            connection = socket.create_connection((host, 443), timeout=timeout)
        except OSError:
            return Probe(service, False, "系统路由连接失败")
    else:
        if scheme == "socks5":
            connection, detail = open_socks5_tunnel(host, port, timeout)
        else:
            connection, detail = open_tunnel(host, port, timeout)
        if connection is None:
            return Probe(service, False, detail)
    with connection:
        source_port = connection.getsockname()[1]
        try:
            context = proxies.create_ssl_context()
            with context.wrap_socket(connection, server_hostname=host) as tls:
                route = "unknown"
                node = ""
                if endpoint:
                    snapshot = controller("/connections", endpoint)
                    if port is not None:
                        route, node = observed_route_detail(snapshot, host, source_port, port)
                    else:
                        for row in snapshot.get("connections") or []:
                            meta = row.get("metadata") or {}
                            destination = str(meta.get("host") or meta.get("sniffHost") or "").lower().rstrip(".")
                            if (str(meta.get("type", "")).casefold() == "tun"
                                    and str(meta.get("sourcePort")) == str(source_port) and destination == host):
                                route = connection_route(row)
                                chains = [c for c in (row.get("chains") or ()) if isinstance(c, str) and c.strip()]
                                node = chains[0].strip() if chains else ""
                                break
                elif allow_uncontrolled:
                    if port is None:
                        route = "tun_route"
                    elif scheme == "socks5":
                        route = "socks5_proxy"
                    else:
                        route = "http_proxy"
                inferred_region = proxies.infer_region_from_node(node) if node else ""
                if host in {"claude.ai", "chatgpt.com", "gemini.google.com", "antigravity.google.com"} or service in {OAUTH_TOKEN, DAILY_CODE_ASSIST}:
                    method = "HEAD"
                    headers = "Connection: close\r\n"
                    if service == CODEX_RESPONSES:
                        method = "GET"
                        key = base64.b64encode(os.urandom(16)).decode("ascii")
                        headers = ("Connection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\n"
                                   f"Sec-WebSocket-Key: {key}\r\nOrigin: https://chatgpt.com\r\n")
                    elif service in {OAUTH_TOKEN, DAILY_CODE_ASSIST}:
                        method = "POST"
                        headers += "Content-Type: application/x-www-form-urlencoded\r\nContent-Length: 0\r\n"
                    request = f"{method} /{path} HTTP/1.1\r\nHost: {host}\r\n{headers}\r\n".encode("ascii")
                    tls.sendall(request)
                    response = http.client.HTTPResponse(tls, method=method)
                    try:
                        response.begin()
                        elapsed = max(1, int(round((time.perf_counter() - started) * 1000)))
                        if service == CODEX_RESPONSES:
                            ready = response.status in {101, 401}
                            detail = f"Codex 服务有响应（HTTP {response.status}）" if ready else f"Codex 握手异常（HTTP {response.status}）"
                            return Probe(service, ready, detail, route, elapsed, node, inferred_region)
                        if service in {OAUTH_TOKEN, DAILY_CODE_ASSIST}:
                            expected = {400, 401} if service == OAUTH_TOKEN else {401}
                            status = response.status
                            return Probe(service, status in expected, f"Google 接口有响应（HTTP {status}）", route, elapsed, node, inferred_region)
                        if response.status in {429, 451} or response.status >= 500:
                            return Probe(service, False, f"网站返回 HTTP {response.status}", route, elapsed, node, inferred_region)
                        suffix = "，可能需要登录或浏览器验证" if response.status == 403 else ""
                        return Probe(service, True, f"网站有响应（HTTP {response.status}）{suffix}", route, elapsed, node, inferred_region)
                    finally:
                        response.close()
                elapsed = max(1, int(round((time.perf_counter() - started) * 1000)))
                return Probe(service, True, "TLS 已连接", route, elapsed, node, inferred_region)
        except ssl.SSLCertVerificationError:
            detail = "服务证书校验失败"
        except TimeoutError:
            detail = "端口有响应，服务连接超时"
        except (OSError, http.client.HTTPException):
            detail = "端口有响应，服务连接中断"
    return Probe(service, False, detail)


def probe_with_retry(host: str, port: int | None, endpoint: Controller | None, *, allow_uncontrolled: bool = False,
                     scheme: str = "http") -> Probe:
    kwargs: dict = {"endpoint": endpoint}
    if allow_uncontrolled:
        kwargs["allow_uncontrolled"] = True
    if scheme != "http":
        kwargs["scheme"] = scheme
    first = connect_probe(host, port, **kwargs)
    if first.route != "direct" and not first.connected and first.detail in {
        "端口有响应，服务连接超时", "端口有响应，服务连接中断",
    }:
        return connect_probe(host, port, **kwargs)
    return first


def route_score(report: Report, needs_tun: bool) -> tuple[bool, int, bool, bool]:
    ready = sum(p.connected and p.route in PASSING_ROUTES for p in report.probes.values())
    region_ok = not proxies.is_restricted_region(report.region)
    return ready > 0 and (not needs_tun or report.tun is True), ready, region_ok, report.endpoint is not None


def _controller_ports(config: dict, endpoint: Controller,
                      sibling_map: dict[Controller, list[int]] | None = None,
                      test_host: str = "accounts.google.com") -> list[int]:
    ports = list(dict.fromkeys(
        value for key in ("mixed-port", "port")
        if type(value := config.get(key)) is int and 0 < value <= 65535
    ))
    if ports or not sibling_map:
        return ports
    if not isinstance(config.get("mode"), str):
        return []
    siblings = sibling_map.get(endpoint, ())
    for candidate in siblings:
        if probe_http_proxy_port(candidate, test_host=test_host, timeout=1.5) == "http_proxy":
            return [candidate]
    return []


def _probe_hosts_and_region(hosts: list[str], port: int | None, endpoint: Controller | None,
                            *, allow_uncontrolled: bool = False, scheme: str = "http") -> tuple[list[Probe], str, int | None]:
    # Probe per-AI-domain Cloudflare trace (chatgpt.com / claude.ai) when those domains are checked,
    # rather than relying only on www.cloudflare.com which can sit in a different rule group!
    trace_hosts: list[str] = []
    if any(h.startswith("chatgpt.com") for h in hosts):
        trace_hosts.append("chatgpt.com")
    if any(h.startswith("claude.ai") for h in hosts):
        trace_hosts.append("claude.ai")
    if not trace_hosts:
        trace_hosts.append("www.cloudflare.com")

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(12, max(2, len(hosts) + len(trace_hosts)))) as pool:
        if scheme != "http":
            trace_futures = {th: pool.submit(proxies.probe_exit_region, port, 3.0, th, scheme=scheme) for th in trace_hosts}
        else:
            trace_futures = {th: pool.submit(proxies.probe_exit_region, port, 3.0, th) for th in trace_hosts}
        if allow_uncontrolled and scheme != "http":
            probe_futures = [pool.submit(probe_with_retry, host, port, None, allow_uncontrolled=True, scheme=scheme) for host in hosts]
        elif allow_uncontrolled:
            probe_futures = [pool.submit(probe_with_retry, host, port, None, allow_uncontrolled=True) for host in hosts]
        else:
            probe_futures = [pool.submit(probe_with_retry, host, port, endpoint) for host in hosts]
        probes = [f.result() for f in probe_futures]
        trace_results: dict[str, tuple[str, int | None]] = {}
        for th, fut in trace_futures.items():
            try:
                trace_results[th] = fut.result(timeout=3.5)
            except Exception:
                trace_results[th] = ("", None)

    # Enrich probes with true per-domain trace region when node name didn't already supply one
    enriched: list[Probe] = []
    for p in probes:
        dom = p.host.split("/", 1)[0]
        if not p.region and dom in trace_results and trace_results[dom][0]:
            enriched.append(replace(p, region=trace_results[dom][0]))
        else:
            enriched.append(p)
    probes = enriched

    # Derive representative region: prefer actual AI domain regions (chatgpt / claude / google node) over generic CF
    region = ""
    latency_ms: int | None = None
    for th in trace_hosts:
        reg, lat = trace_results.get(th, ("", None))
        if reg and not region:
            region = reg
        if lat is not None and latency_ms is None:
            latency_ms = lat
    if not region:
        for p in probes:
            if p.region:
                region = p.region
                break
    if latency_ms is None:
        samples = [p.latency_ms for p in probes if p.connected and p.latency_ms is not None]
        if samples:
            latency_ms = int(sum(samples) / len(samples))
    return probes, region, latency_ms


def check_http_proxy(port: int, hosts: list[str], provider: str, *, tun: bool | None = None) -> Report:
    probes, region, latency_ms = _probe_hosts_and_region(hosts, port, None, allow_uncontrolled=True)
    return Report(
        "http", tun, {probe.host: probe for probe in probes}, port,
        provider=provider, endpoint=None, region=region, latency_ms=latency_ms,
        scheme="http",
    )


def check_socks5_proxy(port: int, hosts: list[str], provider: str, *, tun: bool | None = None) -> Report:
    """Validate a SOCKS5-only local proxy directly; apps will auto-bridge via the local HTTP relay."""
    probes, region, latency_ms = _probe_hosts_and_region(
        hosts, port, None, allow_uncontrolled=True, scheme="socks5",
    )
    return Report(
        "socks5", tun, {probe.host: probe for probe in probes}, port,
        provider=provider, endpoint=None, region=region, latency_ms=latency_ms,
        scheme="socks5",
    )


def check_tun_only(hosts: list[str], provider: str = "系统 TUN", *, require_non_cn_region: bool = False) -> Report | None:
    """Validate pure-TUN or router/旁路由 transparent proxy setups that expose no local HTTP port."""
    probes, region, latency_ms = _probe_hosts_and_region(hosts, None, None, allow_uncontrolled=True)
    connected = [p for p in probes if p.connected and p.route in PASSING_ROUTES]
    if not connected or region.upper() == "CN":
        return None
    if require_non_cn_region and not region:
        return None
    return Report(
        "tun", True, {probe.host: probe for probe in probes}, 0,
        provider=provider, endpoint=None, region=region, latency_ms=latency_ms,
        scheme="tun",
    )


def check(port: int | None, hosts: list[str], provider: str = "JImmm", custom: Controller | None = None,
          *, auto_tun: bool | None = None) -> Report:
    if provider == MANUAL:
        if port is None:
            return Report("", None, {}, 0, "请填写本机 HTTP 代理端口，例如 7890 或 10808。", MANUAL)
        if custom:
            config = controller("/configs", custom)
            known_ports = [value for key in ("mixed-port", "port")
                           if type(value := config.get(key)) is int and 0 < value <= 65535]
            if config and (not known_ports or port in known_ports):
                tun_config = config.get("tun")
                tun = tun_config.get("enable") if isinstance(tun_config, dict) else None
                probes, region, latency_ms = _probe_hosts_and_region(hosts, port, custom)
                return Report(
                    config.get("mode", ""), tun if isinstance(tun, bool) else None,
                    {probe.host: probe for probe in probes}, port,
                    provider=custom.name or f"自定义代理 :{port}", endpoint=custom,
                    region=region, latency_ms=latency_ms,
                )
        tun_on = detect_system_tun()
        http_rep = check_http_proxy(port, hosts, f"手动端口 :{port}", tun=tun_on)
        if not any(p.connected for p in http_rep.probes.values()) and proxies.probe_socks5_port(port, timeout=0.4):
            return check_socks5_proxy(port, hosts, f"手动端口 :{port}", tun=tun_on)
        return http_rep

    default_port = DEFAULT_PORTS.get(provider, 0)
    snapshot: proxies.DiscoverySnapshot | None = None
    if provider == "JImmm":
        endpoints = proxy_controllers("JImmm")
    elif provider == "Clash" and custom and auto_tun is None:
        endpoints = [custom]
    elif provider == "Clash":
        endpoints = ([custom] if custom else []) + clash_controllers()
    else:
        snapshot = discover_live_proxies(custom)
        endpoints = [
            ctrl for ctrl in snapshot.controllers
            if (custom and ctrl == custom) or ctrl.name.casefold() == provider.casefold()
        ]
    reported_ports = set()
    best = None
    checked = set()
    sibling_map = snapshot.controller_sibling_ports if snapshot else None
    first_host = hosts[0] if hosts else "accounts.google.com"
    for endpoint in endpoints:
        config = controller("/configs", endpoint)
        known_ports = _controller_ports(config, endpoint, sibling_map, test_host=first_host)
        if not known_ports or (port is not None and port not in known_ports):
            reported_ports.update(known_ports)
            continue
        proxy_port = port if port is not None else known_ports[0]
        if proxy_port in checked:
            continue
        checked.add(proxy_port)
        tun_config = config.get("tun")
        tun = tun_config.get("enable") if isinstance(tun_config, dict) else None
        probes, region, latency_ms = _probe_hosts_and_region(hosts, proxy_port, endpoint)
        report = Report(
            config.get("mode", ""), tun if isinstance(tun, bool) else None,
            {probe.host: probe for probe in probes}, proxy_port,
            provider=endpoint.name or provider, endpoint=endpoint,
            region=region, latency_ms=latency_ms,
        )
        if auto_tun is None or route_score(report, auto_tun)[:2] == (True, len(hosts)):
            return report
        if best is None or route_score(report, auto_tun) > route_score(best, auto_tun):
            best = report
    if best is not None:
        return best

    if reported_ports:
        choices = "、".join(map(str, sorted(reported_ports)))
        issue = f"端口 {port} 与 {provider} 的 HTTP 或混合端口不一致（{choices}）。请留空自动选择，或填写正确端口。"
        return Report("", None, {}, port or default_port, issue, provider)

    if provider not in {"JImmm", "Clash"}:
        if provider in {"系统 TUN", "透明代理 / 旁路由"} and port is None:
            tun_rep = check_tun_only(hosts, provider, require_non_cn_region=(provider == "透明代理 / 旁路由"))
            if tun_rep is not None:
                return tun_rep
            return Report("", False, {}, 0, f"{provider} 当前无法连通目标 AI 服务。", provider)
        target_port = port
        socks_fallback_port: int | None = None
        if target_port is None and snapshot:
            matching = [item for item in snapshot.http_proxies if item.name.casefold() == provider.casefold()]
            if not matching and provider == "系统代理":
                sys_port = system_proxy_port()
                if sys_port:
                    target_port = sys_port
            for candidate in matching:
                if candidate.source in {"system", "env"}:
                    target_port = candidate.port
                    break
                kind = probe_http_proxy_port(candidate.port, test_host=first_host)
                if kind == "http_proxy":
                    target_port = candidate.port
                    break
                if kind == "socks5" and socks_fallback_port is None:
                    socks_fallback_port = candidate.port
        if target_port:
            return check_http_proxy(target_port, hosts, provider, tun=detect_system_tun())
        if socks_fallback_port:
            return check_socks5_proxy(socks_fallback_port, hosts, provider, tun=detect_system_tun())
        issue = f"未检测到 {provider} 的可用本地代理端口。请先打开 {provider} 并开启 HTTP / 混合代理端口，或手动填写端口。"
        return Report("", None, {}, port or default_port, issue, provider)

    if custom and provider == "Clash":
        issue = "无法通过指定的控制接口读取代理状态。请检查本机控制端口和密钥。"
    elif provider == "Clash":
        issue = "未读取到 Clash 的本机接口。请先打开 Clash；其他版本可在「控制接口」填写本机控制端口，或切换到「自动检测」。"
    else:
        issue = "未读取到 JImmm / Clash Party 的本机接口。请先打开代理并连接节点。"
    return Report("", None, {}, port or default_port, issue, provider)


def check_reports(hosts: list[str], needs_tun: bool, custom: Controller | None = None,
                  *, hints: list[str] | None = None) -> tuple[list[Report], tuple[str, ...]]:
    snapshot = discover_live_proxies(custom)
    detected = dict(snapshot.detected_apps)
    if hints is not None:
        for item in snapshot.hints:
            if item not in hints:
                hints.append(item)

    first_host = hosts[0] if hosts else "accounts.google.com"
    controller_reports: list[Report] = []
    covered_ports: set[int] = set()

    if snapshot.controllers:
        def read_config(endpoint: Controller):
            return endpoint, controller("/configs", endpoint)

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(snapshot.controllers))) as pool:
            configs = list(pool.map(read_config, snapshot.controllers))

        active_endpoints: list[tuple[Controller, dict, int]] = []
        for endpoint, config in configs:
            if not config:
                continue
            known_ports = _controller_ports(config, endpoint, snapshot.controller_sibling_ports, test_host=first_host)
            if not known_ports:
                continue
            proxy_port = known_ports[0]
            if proxy_port in covered_ports:
                covered_ports.update(known_ports)
                continue
            covered_ports.update(known_ports)
            active_endpoints.append((endpoint, config, proxy_port))

        def probe_endpoint(item: tuple[Controller, dict, int]) -> Report:
            endpoint, config, proxy_port = item
            tun_config = config.get("tun")
            tun = tun_config.get("enable") if isinstance(tun_config, dict) else None
            probes, region, latency_ms = _probe_hosts_and_region(hosts, proxy_port, endpoint)
            name = endpoint.name or ("JImmm" if endpoint == JIMMM_CONTROLLER else "Clash")
            return Report(
                config.get("mode", ""),
                tun if isinstance(tun, bool) else None,
                {probe.host: probe for probe in probes},
                proxy_port,
                provider=name,
                endpoint=endpoint,
                region=region,
                latency_ms=latency_ms,
            )

        if active_endpoints:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(active_endpoints))) as pool:
                controller_reports = list(pool.map(probe_endpoint, active_endpoints))

    confirmed_names: set[str] = {r.provider.casefold() for r in controller_reports}
    remaining = [
        cand for cand in snapshot.http_proxies
        if cand.port not in covered_ports
        and not (cand.source == "process" and cand.name.casefold() in confirmed_names)
    ]
    selected_http: list[proxies.HTTPProxyCandidate] = []
    socks_only_by_name: dict[str, list[int]] = {}
    seen_app_names: set[str] = set(confirmed_names)

    if remaining:
        process_candidates = [c for c in remaining if c.source == "process"]
        classifications: dict[int, str] = {}
        if process_candidates:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(process_candidates))) as pool:
                results = pool.map(lambda c: (c.port, probe_http_proxy_port(c.port, test_host=first_host)),
                                   process_candidates)
                classifications = dict(results)

        for cand in remaining:
            name_key = cand.name.casefold()
            if cand.source in {"system", "env"}:
                selected_http.append(cand)
                confirmed_names.add(name_key)
                seen_app_names.add(name_key)
                continue
            kind = classifications.get(cand.port, "not_proxy")
            if kind == "http_proxy":
                if name_key not in seen_app_names:
                    selected_http.append(cand)
                    confirmed_names.add(name_key)
                    seen_app_names.add(name_key)
            elif kind == "socks5":
                socks_only_by_name.setdefault(cand.name, []).append(cand.port)

    controller_owns_tun = any(report.tun is True for report in controller_reports)
    os_tun: bool | None = None

    def _get_os_tun() -> bool:
        nonlocal os_tun
        if os_tun is None:
            os_tun = False if controller_owns_tun else detect_system_tun()
        return os_tun

    http_reports: list[Report] = []
    if selected_http:
        tun_flag = _get_os_tun()
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(selected_http))) as pool:
            http_reports = list(pool.map(
                lambda cand: check_http_proxy(cand.port, hosts, cand.name, tun=tun_flag),
                selected_http,
            ))

    # Auto-verify and bridge SOCKS5-only clients (e.g. Shadowsocks, SSH -D, Tor, sing-box/xray SOCKS inbound)
    socks_reports: list[Report] = []
    socks_candidates = [
        (name, sorted(set(ports)))
        for name, ports in socks_only_by_name.items()
        if name.casefold() not in confirmed_names
    ]
    if socks_candidates:
        tun_flag = _get_os_tun()
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(socks_candidates))) as pool:
            probed_socks = list(pool.map(
                lambda item: (item[0], item[1], check_socks5_proxy(item[1][0], hosts, item[0], tun=tun_flag)),
                socks_candidates,
            ))
        for name, ports, srep in probed_socks:
            if any(p.connected and p.route in PASSING_ROUTES for p in srep.probes.values()):
                socks_reports.append(srep)
                confirmed_names.add(name.casefold())
                if hints is not None:
                    note = f"检测到 {name} 仅监听 SOCKS5 端口（:{ports[0]}），已自动通过内置中继桥接为本地 HTTP 代理。"
                    if note not in hints:
                        hints.append(note)
            elif hints is not None:
                port_text = "、".join(f":{p}" for p in ports)
                note = f"检测到 {name} 仅监听 SOCKS5 端口（{port_text}），请在代理软件中开启 HTTP 或 Mixed 混合代理端口。"
                if note not in hints:
                    hints.append(note)

    reports = [report for report in (*controller_reports, *http_reports, *socks_reports) if not report.issue]
    any_healthy = any(
        any(p.connected and p.route in PASSING_ROUTES for p in r.probes.values())
        for r in reports
    )
    if not any_healthy:
        if detect_system_tun():
            tun_report = check_tun_only(hosts, "系统 TUN")
            if tun_report is not None:
                reports.append(tun_report)
        else:
            # Universal ground-truth fallback for unknown TUN VPNs, WireGuard, or router/旁路由 transparent gateways:
            # 1-shot Cloudflare trace on chatgpt.com; strictly requires a confirmed non-CN exit country.
            direct_region, _ = proxies.probe_exit_region(None, 2.0, "chatgpt.com")
            if direct_region and direct_region.upper() != "CN":
                tun_report = check_tun_only(hosts, "透明代理 / 旁路由", require_non_cn_region=True)
                if tun_report is not None:
                    reports.append(tun_report)
    for report in reports:
        detected.setdefault(report.provider, None)
    return reports, tuple(detected)


def check_auto(hosts: list[str], needs_tun: bool, custom: Controller | None = None) -> Report:
    available, detected = check_reports(hosts, needs_tun, custom)
    if available:
        best = max(available, key=lambda item: route_score(item, needs_tun))
        return replace(best, detected=detected)
    return Report("", None, {}, 0, "没有找到可用的本机代理。请打开代理软件并连接节点，或选择「手动指定端口」填写 HTTP 代理端口。",
                  AUTO, detected)


def requires_tun(target: Target) -> bool:
    return target.kind in {"store", "electron"} or (IS_MAC and target.kind == "codex")


def app_report(target: Target, reports: list[Report], preferred_port: int | None = None) -> Report:
    def score(report: Report) -> tuple[bool, int, bool, bool, bool, bool]:
        if report.issue:
            return False, -1, False, False, False, False
        probes = [report.probes[host] for host in target.hosts if host in report.probes]
        ready = bool(probes) and block_reason(probes, requires_tun(target), report.tun, report.provider) is None
        healthy = sum(probe.connected and probe.route in PASSING_ROUTES for probe in probes)
        region_ok = not proxies.is_restricted_region(report.region)
        return ready, healthy, region_ok, report.port == preferred_port, report.endpoint is not None, report.tun is True

    return max(reports, key=score)


def system_proxy_port() -> int | None:
    if IS_MAC:
        return macos.system_proxy_port()
    if IS_LINUX:
        return linux.system_proxy_port()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            if winreg.QueryValueEx(key, "ProxyEnable")[0] != 1:
                return None
            value = winreg.QueryValueEx(key, "ProxyServer")[0]
        https = next((entry[6:] for entry in value.split(";") if entry.lower().startswith("https=")), value)
        return parse_port(https)
    except (OSError, ValueError, AttributeError):
        return None


def block_reason(probes: list[Probe], needs_tun: bool, tun: bool | None, provider: str = "JImmm") -> str | None:
    if needs_tun and tun is not True:
        return f"请在 {provider} 开启 TUN 后重试。"
    for probe in probes:
        if probe.route == "direct":
            return f"{probe.host} 命中直连。请在 {provider} 调整规则或选择全局节点。"
        if not probe.connected:
            return f"{probe.host} · {probe.detail}。"
        if probe.route not in PASSING_ROUTES:
            return f"无法确认 {probe.host} 走了节点。请确认 {provider} 正在运行并核对端口。"
    return None


def process_snapshot() -> dict[int, tuple[str, int]]:
    if IS_MAC:
        return macos.process_snapshot()
    if IS_LINUX:
        return linux.process_snapshot()

    class ProcessEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
                    ("default_heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
                    ("name", wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        processes = {}
        entry = ProcessEntry(size=ctypes.sizeof(ProcessEntry))
        found = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while found:
            processes[entry.pid] = (entry.name, entry.parent)
            found = kernel.Process32NextW(handle, ctypes.byref(entry))
        return processes
    finally:
        kernel.CloseHandle(handle)


def process_running(executable: str) -> bool:
    return any(name.casefold() == executable.casefold() for name, _ in process_snapshot().values())


def _target_exe_name(target: Target, path: Path | None) -> str:
    if target.kind == "codex":
        return "ChatGPT.exe"
    if target.kind == "store":
        return "Claude.exe"
    return path.name if path else ""


def _windows_matching_hwnds(exe_name: str) -> list[int]:
    if sys.platform != "win32" or not exe_name:
        return []
    pids = {pid for pid, (name, _) in process_snapshot().items() if name.casefold() == exe_name.casefold()}
    if not pids:
        return []
    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found: list[int] = []

    def enum_cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    return found


def focus_app(target: Target, path: Path | None) -> bool:
    """Bring an already-running desktop application window to the foreground."""
    if IS_MAC:
        return macos.focus_app(path)
    if IS_LINUX:
        return linux.focus_app(target, path)
    if sys.platform != "win32":
        return False
    exe = _target_exe_name(target, path)
    hwnds = _windows_matching_hwnds(exe)
    if not hwnds:
        return False
    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    try:
        user32.ShowWindow(hwnds[0], SW_RESTORE)
        user32.SetForegroundWindow(hwnds[0])
        return True
    except Exception:
        return False


def close_app_gracefully(target: Target, path: Path | None, timeout: float = 5.0) -> bool:
    """Politely ask a running desktop app to close (never force-killing unsaved work)."""
    if IS_MAC:
        return macos.close_app(path, timeout=timeout)
    if IS_LINUX:
        return linux.close_app(target, path, timeout=timeout)
    if sys.platform != "win32":
        return False
    exe = _target_exe_name(target, path)
    if not exe or not process_running(exe):
        return True
    user32 = ctypes.windll.user32
    WM_CLOSE = 0x0010
    for hwnd in _windows_matching_hwnds(exe):
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_running(exe):
            return True
        time.sleep(0.25)
    return not process_running(exe)


def restart_and_launch(target: Target, path: Path | None, port: int, data_dir: Path = DATA,
                       *, stable_profile: bool = False) -> None:
    """Gracefully close a running single-instance app, then re-launch it with the target proxy."""
    if not close_app_gracefully(target, path, timeout=5.0):
        raise ValueError(f"{target.name} 未能自动关闭（可能有未保存内容或后台托盘常驻），请手动保存并退出后重试。")
    launch(target, path, port, data_dir, stable_profile=stable_profile)


def codex_dotenv() -> Path:
    return Path(os.environ.get("CODEX_HOME") or HOME / ".codex") / ".env"


def write_bytes(path: Path, data: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        tmp = Path(output.name)
        try:
            output.write(data)
            output.close()
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def launch_codex(target: Target, port: int, data_dir: Path | None = None) -> None:
    if process_running("ChatGPT.exe"):
        raise AlreadyRunningError("ChatGPT / Codex 已在运行。请保存工作并彻底退出，再点「打开」切换代理。")
    if port > 0:
        with_codex_proxy(
            port,
            lambda: subprocess.Popen([str(WINDOWS / "explorer.exe"), f"shell:AppsFolder\\{target.app_id}"]),
            data_dir=data_dir,
        )
    else:
        subprocess.Popen([str(WINDOWS / "explorer.exe"), f"shell:AppsFolder\\{target.app_id}"])


def with_codex_proxy(port: int, start, *, data_dir: Path | None = None) -> None:
    path = codex_dotenv()
    original = path.read_bytes() if path.exists() else None
    current = (original or b"").decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in current else "\n"
    settings = proxy_env(port, {})
    names = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "WS_PROXY", "WSS_PROXY"}
    line = re.compile(r"^\s*(?:export\s+)?(" + "|".join(sorted(names)) + r")\s*=", re.I)
    preserved = [text for text in current.splitlines(keepends=True) if not line.match(text)]
    if preserved and not preserved[-1].endswith(("\n", "\r")):
        preserved[-1] += newline
    content = ("".join(preserved) + "".join(f"{key}={value}{newline}" for key, value in settings.items())).encode("utf-8")
    if original and original.startswith(b"\xef\xbb\xbf"):
        content = b"\xef\xbb\xbf" + content
    backup: Path | None = None
    if content != original:
        path.parent.mkdir(parents=True, exist_ok=True)
        if original is not None:
            backup = path.with_name(path.name + ".ai-proxy-launcher.bak")
            try:
                with backup.open("xb") as output:
                    output.write(original)
            except FileExistsError:
                pass
        if (path.read_bytes() if path.exists() else None) != original:
            raise ValueError("Codex 代理配置刚被其他程序修改。请重新点击「打开」。")
        write_bytes(path, content)
    try:
        start()
    except (OSError, subprocess.SubprocessError):
        if content != original and path.read_bytes() == content:
            if original is None:
                path.unlink()
            else:
                write_bytes(path, original)
        raise
    if content != original:
        try:
            import envcheck
            envcheck.record_file_change(
                path,
                source="Codex 配置 (.codex/.env)",
                existed_before=original is not None,
                backup_path=backup if (backup and backup.is_file()) else None,
                data_dir=DATA if data_dir is None else data_dir,
            )
        except Exception:
            pass


def launch(target: Target, path: Path | None, port: int, data_dir: Path = DATA, *, stable_profile: bool = False) -> None:
    if IS_MAC:
        macos.launch(target, path, port, data_dir, stable_profile=stable_profile)
        return
    if IS_LINUX:
        linux.launch(target, path, port, data_dir, stable_profile=stable_profile)
        return
    if target.kind == "codex":
        launch_codex(target, port, data_dir=data_dir)
        return
    if target.kind == "store":
        subprocess.Popen([str(WINDOWS / "explorer.exe"), f"shell:AppsFolder\\{target.app_id}"])
        return
    valid_suffixes = {".exe", ".cmd", ".bat"} if target.kind == "cli" else {".exe"}
    if path is None or not path.is_file() or path.suffix.lower() not in valid_suffixes:
        raise ValueError("找不到程序，请先选择对应的可执行文件。")
    args = [str(path)]
    if target.kind == "electron":
        if process_running(path.name):
            raise AlreadyRunningError("应用已在运行。请先保存工作并完全退出原窗口，再用启动器打开。")
        args.extend(chromium_args(port))
    if target.kind == "web":
        if path.name.casefold() not in SUPPORTED_WIN_BROWSERS:
            raise ValueError("请选择 Edge、Chrome、Brave、Vivaldi、Arc 或 Chromium 等 Chromium 内核浏览器。")
        profile_name = f"{path.stem}-tun" if port <= 0 else (f"{path.stem}-relay" if stable_profile else f"{path.stem}-{port}")
        profile = data_dir / "Browser" / profile_name
        profile.mkdir(parents=True, exist_ok=True)
        args.extend(chromium_args(port))
        args.extend([f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", f"--app={target.url}"])
    if target.kind == "cli" and path.suffix.lower() in {".cmd", ".bat"}:
        args = [str(WINDOWS / "System32/cmd.exe"), "/k", str(path)]
    subprocess.Popen(args, env=proxy_env(port), cwd=str(HOME if target.kind == "cli" else path.parent),
                     creationflags=NEW_CONSOLE if target.kind == "cli" else 0)


def main() -> int:
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--provider", default=AUTO)
    parser.add_argument("--port")
    parser.add_argument("--controller-port")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--ui-smoke", action="store_true")
    parser.add_argument("--minimized", action="store_true")
    parser.add_argument("--rollback-list", action="store_true", help="列出本软件可撤销的外部修改")
    parser.add_argument("--rollback", action="store_true", help="一键撤销本软件的所有外部配置与自启修改")
    parser.add_argument("--reset-app-data", action="store_true", help="配合 --rollback 重置本软件 settings.json")
    parser.add_argument("--clear-browser-profiles", action="store_true", help="配合 --rollback 清理独立浏览器资料目录")
    args = parser.parse_args()
    if args.rollback_list or args.rollback:
        import envcheck
        if args.rollback_list and not args.rollback:
            items = envcheck.list_rollback_items(data_dir=DATA, include_runtime=False)
            payload = [
                {
                    "id": it.id,
                    "category": it.category,
                    "title": it.title,
                    "detail": it.detail,
                    "path": str(it.path) if it.path else None,
                    "backup_path": str(it.backup_path) if it.backup_path else None,
                }
                for it in items
            ]
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        done, errors = envcheck.rollback_all_external(data_dir=DATA, include_runtime=False)
        if args.reset_app_data or args.clear_browser_profiles:
            done.extend(envcheck.reset_app_data(DATA, clear_browser=args.clear_browser_profiles))
        for msg in done:
            print(f"[OK] {msg}")
        for err in errors:
            print(f"[ERROR] {err}", file=sys.stderr)
        if not done and not errors:
            print("[OK] 未发现需要撤销的外部修改。")
        return 1 if errors else 0
    if args.check:
        from dataclasses import asdict
        hosts = list(dict.fromkeys(host for target in TARGETS.values() for host in target.hosts))
        custom = Controller(port=parse_port(args.controller_port)) if args.controller_port else None
        report = (check_auto(hosts, False, custom) if args.provider == AUTO
                  else check(parse_port(args.port) if args.port else None, hosts, args.provider, custom))
        contents = asdict(report, dict_factory=lambda items: {key: value for key, value in items if key != "endpoint"})
        text = json.dumps(contents, ensure_ascii=False, indent=2)
        if args.report:
            args.report.write_text(text, encoding="utf-8")
        else:
            print(text)
        return 0 if not report.issue and all(block_reason([probe], False, report.tun, args.provider) is None
                                             for probe in report.probes.values()) else 1
    guard = None
    if not args.ui_smoke:
        guard = win32net.SingleInstance(DATA)
        if not guard.try_acquire_or_wake():
            return 0
    try:
        from interface import show
        show(smoke=args.ui_smoke, report=args.report, minimized=args.minimized, instance_guard=guard)
    finally:
        if guard:
            guard.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
