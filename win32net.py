"""Zero-subprocess Win32 named pipe HTTP I/O, kernel TCP table, single-instance lock, and login autostart."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import socket
import struct
import sys
import threading

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    import winreg

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_FLAG_OVERLAPPED = 0x40000000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    ERROR_IO_PENDING = 997
    ERROR_BROKEN_PIPE = 109
    ERROR_MORE_DATA = 234
    ERROR_PIPE_BUSY = 231
    ERROR_ALREADY_EXISTS = 183
    WAIT_OBJECT_0 = 0x00000000

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.WaitNamedPipeW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD)
    kernel32.WaitNamedPipeW.restype = wintypes.BOOL
    kernel32.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.ResetEvent.argtypes = (wintypes.HANDLE,)
    kernel32.ResetEvent.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WriteFile.argtypes = (
        wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED),
    )
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = (
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED),
    )
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.GetOverlappedResult.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(OVERLAPPED),
        ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
    )
    kernel32.GetOverlappedResult.restype = wintypes.BOOL
    kernel32.CancelIoEx.argtypes = (wintypes.HANDLE, ctypes.POINTER(OVERLAPPED))
    kernel32.CancelIoEx.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    AF_INET = 2
    AF_INET6 = 23
    TCP_TABLE_OWNER_PID_ALL = 5
    MIB_TCP_STATE_LISTEN = 2
    MIB_TCP_STATE_ESTAB = 5
    ERROR_INSUFFICIENT_BUFFER = 122

    class MIB_TCPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwState", wintypes.DWORD),
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    class MIB_TCP6ROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("ucLocalAddr", ctypes.c_ubyte * 16),
            ("dwLocalScopeId", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("ucRemoteAddr", ctypes.c_ubyte * 16),
            ("dwRemoteScopeId", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwState", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    iphlpapi.GetExtendedTcpTable.argtypes = (
        wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
        wintypes.ULONG, wintypes.DWORD, wintypes.ULONG,
    )
    iphlpapi.GetExtendedTcpTable.restype = wintypes.DWORD


def _wait_overlapped(handle, ov, timeout_ms: int) -> int | None:
    wait_res = kernel32.WaitForSingleObject(ov.hEvent, timeout_ms)
    if wait_res != WAIT_OBJECT_0:
        kernel32.CancelIoEx(handle, ctypes.byref(ov))
        return None
    transferred = wintypes.DWORD(0)
    if kernel32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(transferred), False):
        return transferred.value
    err = ctypes.get_last_error()
    if err == ERROR_MORE_DATA:
        return transferred.value
    return None


def pipe_http_request(pipe_name: str, payload: bytes, timeout: float = 4.0, max_bytes: int = 8 * 1024 * 1024) -> bytes | None:
    """Send an HTTP/1.0 wire payload to a local Windows named pipe using native overlapped Win32 I/O."""
    if sys.platform != "win32":
        return None
    full_path = r"\\.\pipe\\" + pipe_name
    timeout_ms = max(200, int(timeout * 1000))
    connect_ms = min(850, timeout_ms)

    handle = kernel32.CreateFileW(
        full_path, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None
    )
    if handle == INVALID_HANDLE_VALUE:
        err = ctypes.get_last_error()
        if err == ERROR_PIPE_BUSY and kernel32.WaitNamedPipeW(full_path, connect_ms):
            handle = kernel32.CreateFileW(
                full_path, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None
            )
        if handle == INVALID_HANDLE_VALUE:
            return None

    event = kernel32.CreateEventW(None, True, False, None)
    if not event:
        kernel32.CloseHandle(handle)
        return None

    try:
        # Write full request payload
        view = memoryview(payload)
        sent = 0
        while sent < len(payload):
            kernel32.ResetEvent(event)
            ov = OVERLAPPED(hEvent=event)
            written = wintypes.DWORD(0)
            chunk = bytes(view[sent:])
            ok = kernel32.WriteFile(handle, chunk, len(chunk), ctypes.byref(written), ctypes.byref(ov))
            if not ok:
                err = ctypes.get_last_error()
                if err != ERROR_IO_PENDING:
                    return None
                count = _wait_overlapped(handle, ov, timeout_ms)
                if count is None or count <= 0:
                    return None
                sent += count
            else:
                sent += written.value

        # Read full HTTP/1.0 response until pipe closes
        chunks: list[bytes] = []
        total = 0
        buf = ctypes.create_string_buffer(16384)
        while True:
            kernel32.ResetEvent(event)
            ov = OVERLAPPED(hEvent=event)
            got = wintypes.DWORD(0)
            ok = kernel32.ReadFile(handle, buf, len(buf), ctypes.byref(got), ctypes.byref(ov))
            if ok:
                count = got.value
            else:
                err = ctypes.get_last_error()
                if err == ERROR_BROKEN_PIPE:
                    break
                if err == ERROR_MORE_DATA:
                    count = got.value
                elif err == ERROR_IO_PENDING:
                    count = _wait_overlapped(handle, ov, timeout_ms)
                    if count is None:
                        if chunks:
                            break
                        return None
                else:
                    break
            if count <= 0:
                break
            total += count
            if total > max_bytes:
                return None
            chunks.append(buf.raw[:count])
        return b"".join(chunks)
    finally:
        kernel32.CloseHandle(event)
        kernel32.CloseHandle(handle)


def _ntohs_port(raw_dword: int) -> int:
    return socket.ntohs(raw_dword & 0xFFFF)


def _read_ipv4_table() -> tuple[list[tuple[int, int, str, int]], list[tuple[int, str, int]]]:
    established: list[tuple[int, int, str, int]] = []
    listening: list[tuple[int, str, int]] = []
    size = wintypes.DWORD(0)
    res = iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
    if res != ERROR_INSUFFICIENT_BUFFER or size.value < 4:
        return established, listening
    buf = ctypes.create_string_buffer(size.value + 4096)
    size.value = len(buf)
    res = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
    if res != 0:
        return established, listening
    count = struct.unpack_from("<I", buf.raw, 0)[0]
    row_size = ctypes.sizeof(MIB_TCPROW_OWNER_PID)
    for i in range(count):
        offset = 4 + i * row_size
        if offset + row_size > len(buf):
            break
        row = MIB_TCPROW_OWNER_PID.from_buffer_copy(buf, offset)
        pid = int(row.dwOwningPid)
        local_port = _ntohs_port(row.dwLocalPort)
        if row.dwState == MIB_TCP_STATE_ESTAB:
            remote_ip = socket.inet_ntoa(struct.pack("<I", row.dwRemoteAddr))
            remote_port = _ntohs_port(row.dwRemotePort)
            established.append((pid, local_port, remote_ip, remote_port))
        elif row.dwState == MIB_TCP_STATE_LISTEN and pid > 0 and 1 <= local_port <= 65535:
            local_ip = socket.inet_ntoa(struct.pack("<I", row.dwLocalAddr))
            if local_ip in {"127.0.0.1", "0.0.0.0"}:
                listening.append((pid, local_ip, local_port))
    return established, listening


def _read_ipv6_table() -> tuple[list[tuple[int, int, str, int]], list[tuple[int, str, int]]]:
    established: list[tuple[int, int, str, int]] = []
    listening: list[tuple[int, str, int]] = []
    size = wintypes.DWORD(0)
    res = iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, AF_INET6, TCP_TABLE_OWNER_PID_ALL, 0)
    if res != ERROR_INSUFFICIENT_BUFFER or size.value < 4:
        return established, listening
    buf = ctypes.create_string_buffer(size.value + 4096)
    size.value = len(buf)
    res = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, AF_INET6, TCP_TABLE_OWNER_PID_ALL, 0)
    if res != 0:
        return established, listening
    count = struct.unpack_from("<I", buf.raw, 0)[0]
    row_size = ctypes.sizeof(MIB_TCP6ROW_OWNER_PID)
    for i in range(count):
        offset = 4 + i * row_size
        if offset + row_size > len(buf):
            break
        row = MIB_TCP6ROW_OWNER_PID.from_buffer_copy(buf, offset)
        pid = int(row.dwOwningPid)
        local_port = _ntohs_port(row.dwLocalPort)
        if row.dwState == MIB_TCP_STATE_ESTAB:
            remote_ip = "[" + socket.inet_ntop(socket.AF_INET6, bytes(row.ucRemoteAddr)) + "]"
            remote_port = _ntohs_port(row.dwRemotePort)
            established.append((pid, local_port, remote_ip, remote_port))
        elif row.dwState == MIB_TCP_STATE_LISTEN and pid > 0 and 1 <= local_port <= 65535:
            raw_ip = socket.inet_ntop(socket.AF_INET6, bytes(row.ucLocalAddr))
            if raw_ip in {"::1", "::"}:
                listening.append((pid, f"[{raw_ip}]", local_port))
    return established, listening


def windows_tcp_tables() -> tuple[list[tuple[int, int, str, int]], list[tuple[int, str, int]]] | None:
    """Read kernel IPv4 + IPv6 TCP tables via iphlpapi with zero subprocess overhead."""
    if sys.platform != "win32":
        return None
    try:
        est4, lis4 = _read_ipv4_table()
        est6, lis6 = _read_ipv6_table()
        seen_lis: dict[tuple[int, int], str] = {}
        for pid, ip, port in (*lis4, *lis6):
            key = (pid, port)
            if key not in seen_lis or ip in {"127.0.0.1", "[::1]"}:
                seen_lis[key] = ip
        return [*est4, *est6], [(pid, ip, port) for (pid, port), ip in seen_lis.items()]
    except Exception:
        return None


class SingleInstance:
    """Cross-platform single-instance guard that wakes up the existing window on duplicate launch."""

    def __init__(self, data_dir: Path, name: str = "AIProxyLauncher"):
        self._data_dir = data_dir
        self._info_path = data_dir / "instance.json"
        self._name = name
        self._mutex = None
        self._sock: socket.socket | None = None
        self._token = ""
        self._on_wake = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def try_acquire_or_wake(self) -> bool:
        """Return True if this process owns the single-instance lock; False if existing instance was woken."""
        if sys.platform == "win32":
            handle = kernel32.CreateMutexW(None, True, f"Local\\{self._name}.SingleInstance")
            if handle:
                if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                    kernel32.CloseHandle(handle)
                    self._wake_existing()
                    return False
                self._mutex = handle
                return True
        # POSIX / macOS fallback: try pinging existing loopback listener
        if self._wake_existing():
            return False
        return True

    def _wake_existing(self) -> bool:
        try:
            info = json.loads(self._info_path.read_text(encoding="utf-8"))
            port = int(info.get("port", 0))
            token = str(info.get("token", ""))
            if not (1 <= port <= 65535) or not token:
                return False
            with socket.create_connection(("127.0.0.1", port), timeout=0.6) as conn:
                conn.sendall(f"WAKE {token}\n".encode("ascii"))
                reply = conn.recv(64)
                return reply.startswith(b"OK")
        except (OSError, ValueError):
            return False

    def start_listener(self, on_wake) -> None:
        self._on_wake = on_wake
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.bind(("127.0.0.1", 0))
            srv.listen(4)
            srv.settimeout(0.5)
            port = srv.getsockname()[1]
            self._sock = srv
            self._token = secrets.token_hex(16)
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._info_path.write_text(json.dumps({"port": port, "token": self._token, "pid": os.getpid()}), encoding="utf-8")
            self._thread = threading.Thread(target=self._serve, daemon=True, name="SingleInstanceWake")
            self._thread.start()
        except OSError:
            pass

    def _serve(self) -> None:
        assert self._sock is not None
        expected = f"WAKE {self._token}"
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn:
                try:
                    conn.settimeout(0.5)
                    line = conn.recv(128).decode("ascii", errors="replace").strip()
                    if line == expected:
                        conn.sendall(b"OK\n")
                        if self._on_wake:
                            self._on_wake()
                except OSError:
                    pass

    def release(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        try:
            self._info_path.unlink(missing_ok=True)
        except OSError:
            pass
        if sys.platform == "win32" and self._mutex:
            kernel32.CloseHandle(self._mutex)
            self._mutex = None


# ── Auto-start at OS login ──────────────────────────────────────────────

WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WIN_RUN_VALUE = "AIProxyLauncher"
MAC_PLIST = Path.home() / "Library/LaunchAgents/local.aiproxylauncher.plist"
LINUX_AUTOSTART = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart/aiproxylauncher.desktop"


def autostart_command(script_path: Path) -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if (sys.platform == "win32" and pythonw.is_file()) else exe
    return f'"{runner}" "{script_path}" --minimized'


def autostart_enabled() -> bool:
    if sys.platform == "win32":
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0, winreg.KEY_READ) as key:
                val = winreg.QueryValueEx(key, WIN_RUN_VALUE)[0]
                return bool(isinstance(val, str) and val.strip())
        except OSError:
            return False
    if sys.platform == "darwin":
        return MAC_PLIST.is_file()
    if sys.platform.startswith("linux"):
        return LINUX_AUTOSTART.is_file()
    return False


def set_autostart(enabled: bool, script_path: Path) -> None:
    if sys.platform == "win32":
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, WIN_RUN_VALUE, 0, winreg.REG_SZ, autostart_command(script_path))
            else:
                try:
                    winreg.DeleteValue(key, WIN_RUN_VALUE)
                except FileNotFoundError:
                    pass
        return
    if sys.platform == "darwin":
        import plistlib
        if enabled:
            MAC_PLIST.parent.mkdir(parents=True, exist_ok=True)
            args = [sys.executable, "--minimized"] if getattr(sys, "frozen", False) else [sys.executable, str(script_path), "--minimized"]
            payload = {
                "Label": "local.aiproxylauncher",
                "ProgramArguments": args,
                "RunAtLoad": True,
            }
            MAC_PLIST.write_bytes(plistlib.dumps(payload))
        else:
            MAC_PLIST.unlink(missing_ok=True)
        return
    if sys.platform.startswith("linux"):
        if enabled:
            LINUX_AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
            exec_cmd = autostart_command(script_path)
            desktop_content = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=AI 代理启动器\n"
                "Comment=Cross-platform AI Proxy Launcher\n"
                f"Exec={exec_cmd}\n"
                "Terminal=false\n"
                "X-GNOME-Autostart-enabled=true\n"
            )
            LINUX_AUTOSTART.write_text(desktop_content, encoding="utf-8")
        else:
            LINUX_AUTOSTART.unlink(missing_ok=True)


# ── Windows PE version info (friendly display name for white-label apps) ──

_GENERIC_PE_NAMES = {
    "", "electron", "chromium", "flutter", "windows", "application", "app",
    "microsoft", "python", "node", "node.js",
}


def pe_display_name(exe_path: Path) -> str:
    """Read ProductName or FileDescription from a Windows PE executable using version.dll."""
    if sys.platform != "win32":
        return ""
    try:
        path_str = str(exe_path)
        ver = ctypes.WinDLL("version", use_last_error=True)
        ver.GetFileVersionInfoSizeW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
        ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        ver.GetFileVersionInfoW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID)
        ver.GetFileVersionInfoW.restype = wintypes.BOOL
        ver.VerQueryValueW.argtypes = (
            wintypes.LPCVOID, wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.UINT),
        )
        ver.VerQueryValueW.restype = wintypes.BOOL

        dummy = wintypes.DWORD(0)
        size = ver.GetFileVersionInfoSizeW(path_str, ctypes.byref(dummy))
        if not size or size > 2 * 1024 * 1024:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path_str, 0, size, buf):
            return ""

        # Query translation table for language/codepage pairs
        lp = wintypes.LPVOID()
        length = wintypes.UINT(0)
        langs: list[tuple[int, int]] = []
        if ver.VerQueryValueW(buf, r"\VarFileInfo\Translation", ctypes.byref(lp), ctypes.byref(length)) and lp.value and length.value >= 4:
            raw = ctypes.string_at(lp.value, min(length.value, 32))
            for offset in range(0, len(raw) - 3, 4):
                lang, cp = struct.unpack_from("<HH", raw, offset)
                langs.append((lang, cp))
        langs.extend([(0x0804, 0x04B0), (0x0409, 0x04B0), (0x0409, 0x04E4)])

        for field in ("ProductName", "FileDescription"):
            for lang, cp in dict.fromkeys(langs):
                sub = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\{field}"
                vp = wintypes.LPVOID()
                vlen = wintypes.UINT(0)
                if ver.VerQueryValueW(buf, sub, ctypes.byref(vp), ctypes.byref(vlen)) and vp.value and vlen.value > 1:
                    text = ctypes.wstring_at(vp.value, vlen.value).rstrip("\x00").strip()
                    if text and text.casefold() not in _GENERIC_PE_NAMES and len(text) <= 40:
                        return text
    except Exception:
        pass
    return ""

