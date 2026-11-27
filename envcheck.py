"""Audit, repair, and rollback proxy settings in user environment, dev tool configs, and runtime state."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import tempfile
import threading

if sys.platform == "win32":
    import ctypes
    import winreg


HOME = Path.home()
BACKUP_SUFFIX = ".ai-proxy-launcher.bak"
JOURNAL_FILENAME = "rollback_journal.json"
LOOPBACK_PROXY = re.compile(
    r"^(?:https?://|socks5h?://)?(?:127\.0\.0\.1|localhost|\[::1\]):([0-9]{1,5})/?$",
    re.I,
)
TRACKED_WINENV_KEYS = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return HOME / "Library/Application Support/AIProxyLauncher"
    return Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local")) / "AIProxyLauncher"


@dataclass(frozen=True)
class RuntimeChange:
    id: str
    kind: str  # "node" or "tun"
    provider: str
    endpoint: object
    target: str
    previous_value: object
    current_value: object
    recorded_at: str


@dataclass(frozen=True)
class RollbackItem:
    id: str
    category: str  # "file", "winenv", "autostart", "runtime"
    title: str
    detail: str
    path: Path | None = None
    backup_path: Path | None = None
    existed_before: bool = True
    previous_value: str | bool | None = None
    key: str = ""
    runtime_change: RuntimeChange | None = None


_RUNTIME_LOCK = threading.Lock()
_SESSION_RUNTIME_CHANGES: dict[str, RuntimeChange] = {}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _journal_path(data_dir: Path | None = None) -> Path:
    return (data_dir or default_data_dir()) / JOURNAL_FILENAME


def _load_journal(data_dir: Path | None = None) -> dict:
    path = _journal_path(data_dir)
    if not path.is_file():
        return {"version": 1, "files": {}, "winenv": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {
                "version": 1,
                "files": raw.get("files") if isinstance(raw.get("files"), dict) else {},
                "winenv": raw.get("winenv") if isinstance(raw.get("winenv"), dict) else {},
            }
    except (OSError, ValueError):
        pass
    return {"version": 1, "files": {}, "winenv": {}}


def _save_journal(journal: dict, data_dir: Path | None = None) -> None:
    path = _journal_path(data_dir)
    files = journal.get("files") or {}
    winenv = journal.get("winenv") or {}
    if not files and not winenv:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "files": files, "winenv": winenv}, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False, newline="\n") as out:
            tmp = Path(out.name)
            try:
                out.write(payload)
                out.close()
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
    except OSError:
        pass


def record_file_change(
    path: Path,
    *,
    source: str,
    existed_before: bool,
    backup_path: Path | None = None,
    data_dir: Path | None = None,
) -> None:
    """Persist the original state of a modified/created external file (first-write-wins)."""
    journal = _load_journal(data_dir)
    key = str(path.resolve(strict=False))
    if key in journal["files"]:
        # Preserve original existed_before; only fill backup_path if missing.
        entry = journal["files"][key]
        if backup_path and not entry.get("backup_path"):
            entry["backup_path"] = str(backup_path)
            _save_journal(journal, data_dir)
        return
    journal["files"][key] = {
        "path": str(path),
        "source": source,
        "existed_before": bool(existed_before),
        "backup_path": str(backup_path) if backup_path else "",
        "recorded_at": _now_iso(),
    }
    _save_journal(journal, data_dir)


def record_winenv_change(
    key: str,
    *,
    existed_before: bool,
    previous_value: str | None,
    new_value: str,
    data_dir: Path | None = None,
) -> None:
    """Persist the original value of a modified Windows user environment variable (first-write-wins)."""
    if key.upper() not in TRACKED_WINENV_KEYS:
        return
    journal = _load_journal(data_dir)
    existing = journal["winenv"].get(key)
    if isinstance(existing, dict):
        # If reverted back to original state, drop entry; otherwise keep original previous_value.
        orig_existed = bool(existing.get("existed_before"))
        orig_val = existing.get("previous_value")
        if (orig_existed and new_value == orig_val) or (not orig_existed and not new_value):
            journal["winenv"].pop(key, None)
        else:
            existing["new_value"] = new_value
        _save_journal(journal, data_dir)
        return
    journal["winenv"][key] = {
        "key": key,
        "existed_before": bool(existed_before),
        "previous_value": previous_value if existed_before else None,
        "new_value": new_value,
        "recorded_at": _now_iso(),
    }
    _save_journal(journal, data_dir)


def record_runtime_node_change(endpoint: object, group_name: str, previous_node: str, new_node: str) -> None:
    """Record a reversible Selector node switch performed during this session (first-write-wins)."""
    if not endpoint or not group_name or not previous_node or not new_node or previous_node == new_node:
        return
    provider = getattr(endpoint, "name", "") or "代理软件"
    cid = f"runtime:node:{provider}:{group_name}"
    with _RUNTIME_LOCK:
        existing = _SESSION_RUNTIME_CHANGES.get(cid)
        orig_prev = existing.previous_value if existing else previous_node
        if orig_prev == new_node:
            _SESSION_RUNTIME_CHANGES.pop(cid, None)
            return
        _SESSION_RUNTIME_CHANGES[cid] = RuntimeChange(
            id=cid,
            kind="node",
            provider=provider,
            endpoint=endpoint,
            target=group_name,
            previous_value=orig_prev,
            current_value=new_node,
            recorded_at=_now_iso(),
        )


def record_runtime_tun_change(endpoint: object, previous_enabled: bool, new_enabled: bool) -> None:
    """Record a reversible TUN toggle performed during this session (first-write-wins)."""
    if not endpoint or previous_enabled == new_enabled:
        return
    provider = getattr(endpoint, "name", "") or "代理软件"
    cid = f"runtime:tun:{provider}"
    with _RUNTIME_LOCK:
        existing = _SESSION_RUNTIME_CHANGES.get(cid)
        orig_prev = existing.previous_value if existing else previous_enabled
        if orig_prev == new_enabled:
            _SESSION_RUNTIME_CHANGES.pop(cid, None)
            return
        _SESSION_RUNTIME_CHANGES[cid] = RuntimeChange(
            id=cid,
            kind="tun",
            provider=provider,
            endpoint=endpoint,
            target="TUN 虚拟网卡",
            previous_value=orig_prev,
            current_value=new_enabled,
            recorded_at=_now_iso(),
        )


def clear_runtime_changes() -> None:
    with _RUNTIME_LOCK:
        _SESSION_RUNTIME_CHANGES.clear()


def _source_label_for_path(path: Path) -> str:
    name = path.name.lower()
    if name == ".env" and path.parent.name.lower() == ".codex":
        return "Codex 配置 (.codex/.env)"
    if name == ".gitconfig":
        return "Git 配置 (.gitconfig)"
    if name == ".npmrc":
        return "npm 配置 (.npmrc)"
    return f"配置文件 ({path.name})"


@dataclass(frozen=True)
class EnvConflict:
    id: str
    source: str
    key: str
    current_value: str
    reason: str
    action: str  # "sync" or "clear"
    target_port: int | None = None
    path: Path | None = None


def _extract_local_port(value: str) -> int | None:
    match = LOOPBACK_PROXY.fullmatch(value.strip().strip('"').strip("'"))
    if match and 1 <= int(match[1]) <= 65535:
        return int(match[1])
    return None


def port_listening(port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _backup_and_write_text(
    path: Path,
    text: str,
    *,
    source: str = "",
    data_dir: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    backup: Path | None = None
    if existed:
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        if not backup.exists():
            try:
                backup.write_bytes(path.read_bytes())
            except OSError:
                pass
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False, newline="") as out:
        tmp = Path(out.name)
        try:
            out.write(text)
            out.close()
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    record_file_change(
        path,
        source=source or _source_label_for_path(path),
        existed_before=existed,
        backup_path=backup if (backup and backup.is_file()) else None,
        data_dir=data_dir,
    )


def _read_windows_user_env() -> dict[str, str]:
    if sys.platform != "win32":
        return {}
    result: dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ) as key:
            count = winreg.QueryInfoKey(key)[1]
            for i in range(count):
                name, value, _kind = winreg.EnumValue(key, i)
                if isinstance(name, str) and isinstance(value, str):
                    result[name] = value
    except OSError:
        pass
    return result


def _broadcast_env_change() -> None:
    if sys.platform != "win32":
        return
    try:
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 1000, None
        )
    except Exception:
        pass


def audit_environment(
    active_ports: list[int],
    preferred_port: int | None = None,
    *,
    home: Path | None = None,
    codex_env: Path | None = None,
    user_env: dict[str, str] | None = None,
    process_env: dict[str, str] | None = None,
    port_checker=port_listening,
) -> list[EnvConflict]:
    """Inspect user environment variables, .codex/.env, .gitconfig, and .npmrc for stale proxy settings."""
    root = home or HOME
    active_set = {p for p in active_ports if isinstance(p, int) and 1 <= p <= 65535}
    target = preferred_port if preferred_port in active_set else (active_ports[0] if active_ports else None)
    conflicts: list[EnvConflict] = []

    def is_stale(port: int) -> bool:
        if port in active_set:
            return False
        return not port_checker(port)

    # 1. Windows User Environment Variables (registry)
    reg_env = _read_windows_user_env() if user_env is None else user_env
    for name, value in reg_env.items():
        upper = name.upper()
        if upper in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}:
            port = _extract_local_port(value)
            if port and is_stale(port):
                conflicts.append(EnvConflict(
                    id=f"winenv:{name}",
                    source="Windows 用户环境变量",
                    key=name,
                    current_value=value,
                    reason=f"指向未监听的旧端口 :{port}" + (f"（当前可用 :{target}）" if target else ""),
                    action="sync" if target else "clear",
                    target_port=target,
                ))
        elif upper == "NO_PROXY" and value.strip() == "*":
            conflicts.append(EnvConflict(
                id=f"winenv:{name}",
                source="Windows 用户环境变量",
                key=name,
                current_value=value,
                reason="NO_PROXY=* 会导致所有应用强制绕过代理直连",
                action="clear",
            ))

    # Also warn about non-persistent current process env NO_PROXY=* if not already in registry
    proc_env = os.environ if process_env is None else process_env
    for name, value in proc_env.items():
        if name.upper() == "NO_PROXY" and value.strip() == "*" and f"winenv:{name}" not in {c.id for c in conflicts}:
            conflicts.append(EnvConflict(
                id=f"procenv:{name}",
                source="当前进程环境变量",
                key=name,
                current_value=value,
                reason="当前会话存在 NO_PROXY=*（请检查系统环境变量或 shell 启动脚本）",
                action="clear",
            ))

    # 2. ~/.codex/.env
    dotenv_path = codex_env or (Path(os.environ.get("CODEX_HOME") or (root / ".codex")) / ".env")
    if dotenv_path.is_file():
        try:
            text = dotenv_path.read_text(encoding="utf-8-sig")
            seen_ports: set[int] = set()
            for line in text.splitlines():
                m = re.match(r"^\s*(?:export\s+)?(HTTP_PROXY|HTTPS_PROXY|ALL_PROXY)\s*=\s*(.+?)\s*$", line, re.I)
                if m:
                    port = _extract_local_port(m[2])
                    if port and port not in seen_ports and (is_stale(port) or (target and port != target)):
                        seen_ports.add(port)
                        status = "端口未监听" if is_stale(port) else f"与当前代理 :{target} 不一致"
                        conflicts.append(EnvConflict(
                            id=f"codex:{port}",
                            source="Codex 配置 (.codex/.env)",
                            key=m[1].upper(),
                            current_value=m[2].strip(),
                            reason=f"旧端口 :{port}（{status}）",
                            action="sync" if target else "clear",
                            target_port=target,
                            path=dotenv_path,
                        ))
        except OSError:
            pass

    # 3. ~/.gitconfig
    gitconfig = root / ".gitconfig"
    if gitconfig.is_file():
        try:
            section = ""
            for line in gitconfig.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    section = stripped[1:-1].strip().lower()
                    continue
                if section in {"http", "https"}:
                    m = re.match(r"^proxy\s*=\s*(.+?)\s*$", stripped, re.I)
                    if m:
                        port = _extract_local_port(m[1])
                        if port and is_stale(port):
                            conflicts.append(EnvConflict(
                                id=f"git:{section}.proxy",
                                source="Git 配置 (.gitconfig)",
                                key=f"{section}.proxy",
                                current_value=m[1].strip(),
                                reason=f"指向未监听的旧端口 :{port}",
                                action="sync" if target else "clear",
                                target_port=target,
                                path=gitconfig,
                            ))
        except OSError:
            pass

    # 4. ~/.npmrc
    npmrc = root / ".npmrc"
    if npmrc.is_file():
        try:
            for line in npmrc.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                m = re.match(r"^\s*(proxy|https-proxy)\s*=\s*(.+?)\s*$", line, re.I)
                if m:
                    port = _extract_local_port(m[2])
                    if port and is_stale(port):
                        conflicts.append(EnvConflict(
                            id=f"npm:{m[1].lower()}",
                            source="npm 配置 (.npmrc)",
                            key=m[1].lower(),
                            current_value=m[2].strip(),
                            reason=f"指向未监听的旧端口 :{port}",
                            action="sync" if target else "clear",
                            target_port=target,
                            path=npmrc,
                        ))
        except OSError:
            pass

    return conflicts


def fix_conflict(conflict: EnvConflict, *, data_dir: Path | None = None) -> str:
    """Apply a safe repair for a single EnvConflict (with automatic .bak backup & rollback journal)."""
    new_url = f"http://127.0.0.1:{conflict.target_port}" if (conflict.action == "sync" and conflict.target_port) else ""

    if conflict.id.startswith("winenv:"):
        if sys.platform != "win32":
            raise OSError("仅 Windows 支持修改注册表用户环境变量")
        reg_env = _read_windows_user_env()
        existed_before = conflict.key in reg_env
        prev_val = reg_env.get(conflict.key)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE) as key:
            if new_url:
                winreg.SetValueEx(key, conflict.key, 0, winreg.REG_SZ, new_url)
                os.environ[conflict.key] = new_url
            else:
                try:
                    winreg.DeleteValue(key, conflict.key)
                except FileNotFoundError:
                    pass
                os.environ.pop(conflict.key, None)
        record_winenv_change(
            conflict.key,
            existed_before=existed_before,
            previous_value=prev_val,
            new_value=new_url,
            data_dir=data_dir,
        )
        _broadcast_env_change()
        return f"已更新环境变量 {conflict.key}" if new_url else f"已清理失效环境变量 {conflict.key}"

    if conflict.id.startswith("procenv:"):
        os.environ.pop(conflict.key, None)
        return f"已清理当前会话的 {conflict.key}"

    if conflict.id.startswith("codex:") and conflict.path:
        text = conflict.path.read_text(encoding="utf-8-sig")
        newline = "\r\n" if "\r\n" in text else "\n"
        pattern = re.compile(r"^\s*(?:export\s+)?(HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY|WS_PROXY|WSS_PROXY)\s*=", re.I)
        kept = [line for line in text.splitlines(keepends=True) if not pattern.match(line)]
        if kept and not kept[-1].endswith(("\n", "\r")):
            kept[-1] += newline
        if new_url:
            kept.extend([
                f"HTTP_PROXY={new_url}{newline}",
                f"HTTPS_PROXY={new_url}{newline}",
                f"ALL_PROXY={new_url}{newline}",
                f"NO_PROXY=localhost,127.0.0.1,::1{newline}",
            ])
        _backup_and_write_text(conflict.path, "".join(kept), source=conflict.source, data_dir=data_dir)
        return f"已同步 Codex 配置端口至 :{conflict.target_port}" if new_url else "已清理 Codex 失效代理配置"

    if conflict.id.startswith("git:") and conflict.path:
        target_sec = conflict.key.split(".", 1)[0].lower()
        text = conflict.path.read_text(encoding="utf-8-sig", errors="replace")
        newline = "\r\n" if "\r\n" in text else "\n"
        section = ""
        out_lines: list[str] = []
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].strip().lower()
                out_lines.append(line)
                continue
            if section == target_sec and re.match(r"^proxy\s*=", stripped, re.I):
                if new_url:
                    indent = line[:len(line) - len(line.lstrip())]
                    out_lines.append(f"{indent}proxy = {new_url}{newline}")
                continue
            out_lines.append(line)
        _backup_and_write_text(conflict.path, "".join(out_lines), source=conflict.source, data_dir=data_dir)
        return f"已更新 Git {conflict.key} 至 :{conflict.target_port}" if new_url else f"已清理 Git {conflict.key}"

    if conflict.id.startswith("npm:") and conflict.path:
        text = conflict.path.read_text(encoding="utf-8-sig", errors="replace")
        newline = "\r\n" if "\r\n" in text else "\n"
        pattern = re.compile(rf"^\s*{re.escape(conflict.key)}\s*=", re.I)
        out_lines = []
        for line in text.splitlines(keepends=True):
            if pattern.match(line):
                if new_url:
                    out_lines.append(f"{conflict.key}={new_url}{newline}")
                continue
            out_lines.append(line)
        _backup_and_write_text(conflict.path, "".join(out_lines), source=conflict.source, data_dir=data_dir)
        return f"已更新 npm {conflict.key} 至 :{conflict.target_port}" if new_url else f"已清理 npm {conflict.key}"

    raise ValueError("暂不支持自动修复该条目")


def list_rollback_items(
    *,
    data_dir: Path | None = None,
    home: Path | None = None,
    codex_env: Path | None = None,
    include_runtime: bool = True,
    autostart_checker=None,
) -> list[RollbackItem]:
    """Discover all reversible modifications made by AIProxyLauncher (files, env vars, autostart, session proxy state)."""
    root = home or HOME
    journal = _load_journal(data_dir)
    items: list[RollbackItem] = []
    seen_paths: set[str] = set()

    # 1. File modifications (both journal-tracked and standalone .ai-proxy-launcher.bak files)
    known_files = [
        codex_env or (Path(os.environ.get("CODEX_HOME") or (root / ".codex")) / ".env"),
        root / ".gitconfig",
        root / ".npmrc",
    ]
    journal_files = journal.get("files") or {}

    def add_file_candidate(path: Path, source: str, existed_before: bool, bak_hint: Path | None) -> None:
        key = str(path.resolve(strict=False))
        if key in seen_paths:
            return
        bak = bak_hint if (bak_hint and bak_hint.is_file()) else path.with_name(path.name + BACKUP_SUFFIX)
        has_bak = bak.is_file()
        if has_bak:
            seen_paths.add(key)
            items.append(RollbackItem(
                id=f"file:{key}",
                category="file",
                title=source or _source_label_for_path(path),
                detail=f"从备份 {bak.name} 还原原始内容并移除备份文件（{path}）",
                path=path,
                backup_path=bak,
                existed_before=True,
            ))
        elif not existed_before and path.is_file():
            seen_paths.add(key)
            items.append(RollbackItem(
                id=f"file:{key}",
                category="file",
                title=source or _source_label_for_path(path),
                detail=f"删除由本软件新建的文件（修改前该文件不存在：{path}）",
                path=path,
                backup_path=None,
                existed_before=False,
            ))

    stale_journal_keys: list[str] = []
    for jkey, entry in list(journal_files.items()):
        if not isinstance(entry, dict) or not entry.get("path"):
            stale_journal_keys.append(jkey)
            continue
        p = Path(str(entry["path"]))
        bak_p = Path(str(entry["backup_path"])) if entry.get("backup_path") else None
        before_count = len(items)
        add_file_candidate(
            p,
            str(entry.get("source") or ""),
            bool(entry.get("existed_before", True)),
            bak_p,
        )
        if len(items) == before_count and str(p.resolve(strict=False)) not in seen_paths:
            stale_journal_keys.append(jkey)

    if stale_journal_keys:
        for k in stale_journal_keys:
            journal["files"].pop(k, None)
        _save_journal(journal, data_dir)

    for candidate in known_files:
        add_file_candidate(candidate, _source_label_for_path(candidate), True, None)

    # 2. Windows user environment variable changes recorded in journal
    for name, entry in (journal.get("winenv") or {}).items():
        if not isinstance(entry, dict):
            continue
        existed_before = bool(entry.get("existed_before", False))
        prev = entry.get("previous_value")
        cur_new = entry.get("new_value") or "(已清理)"
        detail = (
            f"恢复 Windows 用户环境变量 {name} 为修改前的值：{prev}（当前修改为：{cur_new}）"
            if existed_before and prev is not None
            else f"删除由本软件新增的 Windows 用户环境变量 {name}"
        )
        items.append(RollbackItem(
            id=f"winenv:{name}",
            category="winenv",
            title=f"Windows 用户环境变量 · {name}",
            detail=detail,
            key=name,
            existed_before=existed_before,
            previous_value=str(prev) if (existed_before and prev is not None) else None,
        ))

    # 3. Login autostart entry
    if autostart_checker is None:
        try:
            import win32net
            autostart_checker = win32net.autostart_enabled
        except Exception:
            autostart_checker = lambda: False
    try:
        if autostart_checker():
            loc = "~/Library/LaunchAgents/local.aiproxylauncher.plist" if sys.platform == "darwin" else r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run\AIProxyLauncher"
            items.append(RollbackItem(
                id="autostart",
                category="autostart",
                title="开机静默自启",
                detail=f"取消开机自启注册项（{loc}）",
            ))
    except OSError:
        pass

    # 4. In-memory session proxy runtime changes (node switches / TUN toggles)
    if include_runtime:
        with _RUNTIME_LOCK:
            changes = list(_SESSION_RUNTIME_CHANGES.values())
        for ch in changes:
            if ch.kind == "node":
                items.append(RollbackItem(
                    id=ch.id,
                    category="runtime",
                    title=f"代理节点切换 · {ch.provider} / {ch.target}",
                    detail=f"将分流组「{ch.target}」从「{ch.current_value}」切回原节点「{ch.previous_value}」",
                    previous_value=str(ch.previous_value),
                    key=ch.target,
                    runtime_change=ch,
                ))
            elif ch.kind == "tun":
                prev_txt = "开启" if ch.previous_value else "关闭"
                cur_txt = "开启" if ch.current_value else "关闭"
                items.append(RollbackItem(
                    id=ch.id,
                    category="runtime",
                    title=f"虚拟网卡 (TUN) · {ch.provider}",
                    detail=f"将 {ch.provider} 的 TUN 从「{cur_txt}」恢复为修改前的「{prev_txt}」",
                    previous_value=bool(ch.previous_value),
                    key="tun",
                    runtime_change=ch,
                ))

    return items


def rollback_item(
    item: RollbackItem,
    *,
    data_dir: Path | None = None,
    script_path: Path | None = None,
    winenv_writer=None,
    autostart_disabler=None,
    node_switcher=None,
    tun_setter=None,
) -> str:
    """Safely revert a single RollbackItem and update the rollback journal."""
    if item.category == "file" and item.path:
        if item.backup_path and item.backup_path.is_file():
            data = item.backup_path.read_bytes()
            item.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=item.path.parent, delete=False) as out:
                tmp = Path(out.name)
                try:
                    out.write(data)
                    out.close()
                    os.replace(tmp, item.path)
                finally:
                    tmp.unlink(missing_ok=True)
            item.backup_path.unlink(missing_ok=True)
            msg = f"已从备份还原 {item.title}"
        elif not item.existed_before:
            item.path.unlink(missing_ok=True)
            msg = f"已移除新建文件 {item.title}"
        else:
            raise ValueError(f"未找到 {item.title} 的备份文件，无法自动还原")
        journal = _load_journal(data_dir)
        journal["files"].pop(str(item.path.resolve(strict=False)), None)
        _save_journal(journal, data_dir)
        return msg

    if item.category == "winenv" and item.key:
        if winenv_writer is not None:
            winenv_writer(item.key, item.previous_value if item.existed_before else None)
        else:
            if sys.platform != "win32":
                raise OSError("仅 Windows 支持还原注册表用户环境变量")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE) as reg_key:
                if item.existed_before and isinstance(item.previous_value, str):
                    winreg.SetValueEx(reg_key, item.key, 0, winreg.REG_SZ, item.previous_value)
                    os.environ[item.key] = item.previous_value
                else:
                    try:
                        winreg.DeleteValue(reg_key, item.key)
                    except FileNotFoundError:
                        pass
                    os.environ.pop(item.key, None)
            _broadcast_env_change()
        journal = _load_journal(data_dir)
        journal["winenv"].pop(item.key, None)
        _save_journal(journal, data_dir)
        return (
            f"已恢复环境变量 {item.key} = {item.previous_value}"
            if item.existed_before and item.previous_value is not None
            else f"已移除环境变量 {item.key}"
        )

    if item.category == "autostart":
        if autostart_disabler is not None:
            autostart_disabler()
        else:
            import win32net
            target_script = script_path or Path(__file__).with_name("launcher.py").resolve()
            win32net.set_autostart(False, target_script)
        return "已关闭开机静默自启并移除系统自启项"

    if item.category == "runtime" and item.runtime_change is not None:
        ch = item.runtime_change
        if ch.kind == "node":
            if node_switcher is not None:
                node_switcher(ch.endpoint, ch.target, str(ch.previous_value))
            else:
                import repair
                repair.switch_group_node(ch.endpoint, ch.target, str(ch.previous_value), record=False)
            with _RUNTIME_LOCK:
                _SESSION_RUNTIME_CHANGES.pop(ch.id, None)
            return f"已将分流组 {ch.target} 切回原节点 {ch.previous_value}"
        if ch.kind == "tun":
            if tun_setter is not None:
                tun_setter(ch.endpoint, bool(ch.previous_value))
            else:
                import repair
                repair.set_tun(ch.endpoint, bool(ch.previous_value), record=False)
            with _RUNTIME_LOCK:
                _SESSION_RUNTIME_CHANGES.pop(ch.id, None)
            return f"已将 {ch.provider} 虚拟网卡恢复为{'开启' if ch.previous_value else '关闭'}"

    raise ValueError("不支持撤销该条目")


def rollback_all_external(
    *,
    data_dir: Path | None = None,
    home: Path | None = None,
    codex_env: Path | None = None,
    include_runtime: bool = True,
    script_path: Path | None = None,
    autostart_checker=None,
    autostart_disabler=None,
    winenv_writer=None,
    node_switcher=None,
    tun_setter=None,
) -> tuple[list[str], list[str]]:
    """Revert all external config files, env vars, autostart entries, and session proxy state changes."""
    items = list_rollback_items(
        data_dir=data_dir,
        home=home,
        codex_env=codex_env,
        include_runtime=include_runtime,
        autostart_checker=autostart_checker,
    )
    done: list[str] = []
    errors: list[str] = []
    for item in items:
        try:
            done.append(rollback_item(
                item,
                data_dir=data_dir,
                script_path=script_path,
                winenv_writer=winenv_writer,
                autostart_disabler=autostart_disabler,
                node_switcher=node_switcher,
                tun_setter=tun_setter,
            ))
        except (OSError, ValueError) as err:
            errors.append(f"{item.title}：{err}")
    return done, errors


def reset_app_data(data_dir: Path | None = None, *, clear_browser: bool = False) -> list[str]:
    """Reset AIProxyLauncher's own settings.json and optionally remove isolated Browser profiles."""
    root = data_dir or default_data_dir()
    actions: list[str] = []
    settings = root / "settings.json"
    if settings.is_file():
        settings.unlink(missing_ok=True)
        actions.append(f"已重置软件配置（{settings}）")
    if clear_browser:
        browser_dir = root / "Browser"
        if browser_dir.exists():
            shutil.rmtree(browser_dir)
            actions.append(f"已清理独立浏览器资料目录（{browser_dir}）")
    return actions


@dataclass(frozen=True)
class HealthFinding:
    category: str
    status: str  # "ok", "warn", "error"
    title: str
    detail: str


def _has_global_ipv6() -> str:
    """Return local global-unicast IPv6 address if OS has an active IPv6 default route, else ''."""
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as s:
            s.settimeout(0.4)
            s.connect(("2001:4860:4860::8888", 80))
            addr = s.getsockname()[0]
        lower = addr.lower()
        if lower in {"::1", "::"} or lower.startswith(("fe80:", "fc", "fd")):
            return ""
        return addr
    except OSError:
        return ""


def _resolve_ipv4_hosts(hosts: tuple[str, ...]) -> dict[str, list[str]]:
    resolved: dict[str, list[str]] = {}
    for host in hosts:
        try:
            infos = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
            ips = list(dict.fromkeys(info[4][0] for info in infos))
            resolved[host] = ips
        except OSError:
            resolved[host] = []
    return resolved


def diagnose_network_health(
    tun_active: bool,
    *,
    resolver=_resolve_ipv4_hosts,
    ipv6_detector=_has_global_ipv6,
) -> list[HealthFinding]:
    """Check hidden root causes of 'green probe but broken app': DNS poisoning/Fake-IP & IPv6 leak."""
    findings: list[HealthFinding] = []
    hosts = ("chatgpt.com", "claude.ai", "accounts.google.com")
    resolved = resolver(hosts)

    all_ips = [ip for ips in resolved.values() for ip in ips]
    fake_ips = [ip for ip in all_ips if ip.startswith(("198.18.", "198.19."))]
    bogon_ips = [ip for ip in all_ips if ip.startswith(("127.", "0.", "169.254."))]

    if bogon_ips:
        findings.append(HealthFinding(
            "DNS", "error", "检测到异常/环回 DNS 解析",
            f"部分 AI 域名解析到了异常地址（{', '.join(sorted(set(bogon_ips)))}），请检查 hosts 文件或代理 DNS 设置。",
        ))
    elif fake_ips:
        findings.append(HealthFinding(
            "DNS", "ok", "Fake-IP DNS 接管正常",
            f"AI 域名已由代理 Fake-IP 接管（示例 {fake_ips[0]}），可有效防止本地 DNS 污染。",
        ))
    elif not all_ips:
        findings.append(HealthFinding(
            "DNS", "warn", "系统 DNS 暂时无法解析 AI 域名",
            "若使用浏览器/代理端口一般不受影响；桌面客户端依赖 TUN 时建议开启代理软件的 DNS 劫持 / Fake-IP。",
        ))
    else:
        # Check if multiple unrelated AI domains resolved to the exact same non-Fake-IP (classic DNS poisoning sign)
        first_ips = [ips[0] for ips in resolved.values() if ips]
        if len(first_ips) >= 3 and len(set(first_ips)) == 1:
            findings.append(HealthFinding(
                "DNS", "error", "DNS 疑似存在污染",
                f"多个不同 AI 域名均解析到同一 IP（{first_ips[0]}），建议在代理软件开启 Fake-IP 或加密 DNS。",
            ))
        elif tun_active:
            findings.append(HealthFinding(
                "DNS", "warn", "TUN 已开但未检测到 Fake-IP",
                f"当前解析为真实 IP（如 {first_ips[0]}）；若桌面端仍超时，建议在代理软件启用 Fake-IP / DNS 劫持。",
            ))
        else:
            findings.append(HealthFinding(
                "DNS", "ok", "系统 DNS 解析可用",
                "未发现环回或明显冲突解析（桌面端走 TUN 时建议配合 Fake-IP 使用）。",
            ))

    global_v6 = ipv6_detector()
    if global_v6:
        # Mask last segments for privacy
        masked_v6 = ":".join(global_v6.split(":")[:3]) + "::…"
        findings.append(HealthFinding(
            "IPv6", "warn", "检测到公网 IPv6（注意潜在泄漏/超时）",
            f"本机存在公网 IPv6 出口（{masked_v6}）。若桌面客户端在 TUN 下加载缓慢，请在代理软件开启 IPv6 接管或关闭网卡 IPv6。",
        ))
    else:
        findings.append(HealthFinding(
            "IPv6", "ok", "无公网 IPv6 泄漏风险",
            "当前未检测到公网 IPv6 默认路由。",
        ))

    return findings


def build_diagnostic_report(
    reports: list,
    detected: tuple[str, ...],
    findings: list[HealthFinding],
    conflicts: list[EnvConflict],
    *,
    relay_port: int | None = None,
    provider_mode: str = "",
) -> str:
    """Build a sanitized plain-text diagnostic summary safe to paste into issues or support chats."""
    import platform
    lines = [
        "=== AI 代理启动器 · 脱敏诊断报告 ===",
        f"系统: {platform.system()} {platform.release()} ({platform.machine()}) / Python {platform.python_version()}",
        f"代理模式: {provider_mode or '自动检测'} | 检测到客户端: {'、'.join(detected) or '无'}",
        f"统一本地中继: {'已开启 127.0.0.1:' + str(relay_port) if relay_port else '未开启'}",
        "",
        "[活跃代理与服务探针]",
    ]
    if not reports:
        lines.append("- 无可用代理")
    for rep in reports:
        if getattr(rep, "issue", ""):
            lines.append(f"- {rep.provider}: 错误 - {rep.issue}")
            continue
        reg = f" {rep.region}" if getattr(rep, "region", "") else ""
        lat = f" {rep.latency_ms}ms" if getattr(rep, "latency_ms", None) is not None else ""
        lines.append(f"- {rep.provider} :{rep.port} | mode={rep.mode} | tun={rep.tun} | api={rep.endpoint is not None}{reg}{lat}")
        for host, pr in (rep.probes or {}).items():
            node = f" | node={pr.node}" if getattr(pr, "node", "") else ""
            plat = f" | {pr.latency_ms}ms" if getattr(pr, "latency_ms", None) is not None else ""
            lines.append(f"    * {host}: ok={pr.connected} route={pr.route}{node}{plat} ({pr.detail})")
    lines.append("")
    lines.append("[网络底层体检 (DNS / IPv6)]")
    for f in findings:
        lines.append(f"- [{f.status.upper()}] {f.category} - {f.title}: {f.detail}")
    lines.append("")
    lines.append("[本机环境配置体检]")
    if not conflicts:
        lines.append("- 无冲突项")
    else:
        for c in conflicts:
            lines.append(f"- {c.source} | {c.key}={c.current_value} ({c.reason})")
    return "\n".join(lines)


