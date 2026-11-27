"""Lightweight local HTTP/CONNECT proxy relay with HTTP + SOCKS5 upstream support and zero-restart failover."""
from __future__ import annotations

import socket
import struct
import threading
from urllib.parse import urlsplit


DEFAULT_RELAY_PORT = 17890
UpstreamSpec = int | tuple[str, int]


def _normalize_upstream(spec: UpstreamSpec | None, self_port: int | None) -> tuple[str, int] | None:
    if spec is None:
        return None
    if isinstance(spec, tuple):
        scheme, port = str(spec[0]).lower(), int(spec[1])
    else:
        scheme, port = "http", int(spec)
    if scheme not in {"http", "socks5"} or not (1 <= port <= 65535) or port == self_port:
        return None
    return (scheme, port)


def dial_socks5_upstream(socks_port: int, target_host: str, target_port: int, timeout: float = 3.0) -> socket.socket:
    """Open a TCP tunnel to (target_host, target_port) via a local RFC 1928 SOCKS5 proxy."""
    sock = socket.create_connection(("127.0.0.1", socks_port), timeout=min(0.35, timeout))
    try:
        sock.settimeout(timeout)
        sock.sendall(b"\x05\x01\x00")
        greeting = _recv_exact(sock, 2)
        if greeting != b"\x05\x00":
            raise OSError("SOCKS5 handshake rejected")
        host_bytes = target_host.encode("idna")
        if len(host_bytes) > 255:
            raise OSError("Target host too long")
        req = b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + struct.pack("!H", target_port)
        sock.sendall(req)
        header = _recv_exact(sock, 4)
        if len(header) < 4 or header[0] != 5 or header[1] != 0:
            raise OSError(f"SOCKS5 connect failed (rep={header[1] if len(header) > 1 else -1})")
        atyp = header[3]
        if atyp == 1:
            _recv_exact(sock, 6)
        elif atyp == 3:
            alen = _recv_exact(sock, 1)[0]
            _recv_exact(sock, alen + 2)
        elif atyp == 4:
            _recv_exact(sock, 18)
        else:
            raise OSError("Unsupported SOCKS5 address type")
        return sock
    except OSError:
        sock.close()
        raise


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    buf = bytearray()
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise OSError("Unexpected EOF during SOCKS5 handshake")
        buf.extend(chunk)
    return bytes(buf)


class LocalProxyRelay:
    """Binds strictly on 127.0.0.1 and forwards HTTP/CONNECT requests to hot-swappable local HTTP or SOCKS5 upstreams.

    Never dials remote hosts directly: if no local upstream proxy is reachable, returns HTTP 502.
    """

    def __init__(self, preferred_port: int = DEFAULT_RELAY_PORT):
        self._preferred_port = preferred_port
        self._lock = threading.Lock()
        self._default_upstream: tuple[str, int] | None = None
        self._host_upstreams: dict[str, tuple[str, int]] = {}
        self._fallbacks: list[tuple[str, int]] = []
        self._server: socket.socket | None = None
        self._port: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_conns = 0
        self._total_requests = 0

    @property
    def port(self) -> int | None:
        return self._port

    @property
    def running(self) -> bool:
        return self._server is not None and not self._stop.is_set()

    def stats(self) -> tuple[int, int, int | None]:
        with self._lock:
            up_port = self._default_upstream[1] if self._default_upstream else None
            return self._active_conns, self._total_requests, up_port

    def update_routes(
        self,
        default_upstream: UpstreamSpec | None,
        host_upstreams: dict[str, UpstreamSpec] | None = None,
        fallbacks: list[UpstreamSpec] | None = None,
    ) -> None:
        with self._lock:
            self._default_upstream = _normalize_upstream(default_upstream, self._port)
            cleaned_hosts: dict[str, tuple[str, int]] = {}
            for host, upstream in (host_upstreams or {}).items():
                h = host.split("/", 1)[0].strip().lower().rstrip(".")
                norm = _normalize_upstream(upstream, self._port)
                if h and norm:
                    cleaned_hosts[h] = norm
            self._host_upstreams = cleaned_hosts
            cleaned_fb: list[tuple[str, int]] = []
            for item in fallbacks or ():
                norm = _normalize_upstream(item, self._port)
                if norm and norm != self._default_upstream and norm not in cleaned_fb:
                    cleaned_fb.append(norm)
            self._fallbacks = cleaned_fb

    def start(self) -> int:
        if self.running and self._port:
            return self._port
        self._stop.clear()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bound_port = None
        for candidate in (self._preferred_port, self._preferred_port + 1, self._preferred_port + 2, 0):
            try:
                srv.bind(("127.0.0.1", candidate))
                bound_port = srv.getsockname()[1]
                break
            except OSError:
                continue
        if bound_port is None:
            srv.close()
            raise OSError("无法在 127.0.0.1 上启动本地中继端口")
        srv.listen(128)
        srv.settimeout(0.5)
        self._server = srv
        self._port = bound_port
        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="LocalProxyRelay")
        self._thread.start()
        return bound_port

    def stop(self) -> None:
        self._stop.set()
        if self._server:
            try:
                self._server.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._port = None

    def _select_upstreams(self, host: str) -> list[tuple[str, int]]:
        h = host.strip().lower().rstrip(".")
        with self._lock:
            ordered: list[tuple[str, int]] = []
            if h in self._host_upstreams:
                ordered.append(self._host_upstreams[h])
            else:
                for pattern, target in self._host_upstreams.items():
                    if h.endswith("." + pattern):
                        ordered.append(target)
                        break
            if self._default_upstream:
                ordered.append(self._default_upstream)
            ordered.extend(self._fallbacks)
            result: list[tuple[str, int]] = []
            for spec in ordered:
                if spec[1] != self._port and spec not in result:
                    result.append(spec)
            return result

    def _accept_loop(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                client, _addr = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()

    def _parse_request_target(self, first_line: bytes, headers: bytes) -> tuple[str, str, int, bytes]:
        """Return (method, host, port, origin_form_first_line) for HTTP/CONNECT requests."""
        method = ""
        host = ""
        port = 80
        origin_first = first_line
        try:
            parts = first_line.decode("latin1").split()
            if len(parts) >= 3:
                method, target, version = parts[0].upper(), parts[1], parts[2]
                if method == "CONNECT":
                    raw_host, _, raw_port = target.rpartition(":")
                    host = raw_host.strip("[]") if raw_host else target.strip("[]")
                    port = int(raw_port) if raw_port.isdigit() else 443
                    return method, host, port, first_line
                if "://" in target:
                    parsed = urlsplit(target)
                    host = parsed.hostname or ""
                    port = parsed.port or (443 if parsed.scheme == "https" else 80)
                    path = parsed.path or "/"
                    if parsed.query:
                        path += "?" + parsed.query
                    origin_first = f"{method} {path} {version}".encode("latin1")
        except ValueError:
            pass
        if not host:
            for raw_line in headers.splitlines():
                if raw_line.lower().startswith(b"host:"):
                    val = raw_line.split(b":", 1)[1].strip().decode("latin1", errors="ignore")
                    if ":" in val and not val.endswith("]"):
                        h, _, p = val.rpartition(":")
                        host = h.strip("[]")
                        if p.isdigit():
                            port = int(p)
                    else:
                        host = val.strip("[]")
                    break
        return method, host, port, origin_first

    def _handle_client(self, client: socket.socket) -> None:
        with self._lock:
            self._active_conns += 1
        upstream: socket.socket | None = None
        try:
            client.settimeout(10.0)
            buf = bytearray()
            while b"\r\n\r\n" not in buf and len(buf) < 65536:
                chunk = client.recv(8192)
                if not chunk:
                    return
                buf.extend(chunk)
            sep = buf.find(b"\r\n\r\n")
            if sep < 0:
                client.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
                return
            head = bytes(buf[:sep + 4])
            rest = bytes(buf[sep + 4:])
            first_line, _, header_block = head.partition(b"\r\n")
            method, host, target_port, origin_first = self._parse_request_target(first_line, header_block)
            upstreams = self._select_upstreams(host)
            if not upstreams or not host:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                return
            with self._lock:
                self._total_requests += 1

            chosen_scheme = "http"
            for scheme, port in upstreams:
                try:
                    if scheme == "socks5":
                        upstream = dial_socks5_upstream(port, host, target_port, timeout=3.5)
                    else:
                        # Loopback TCP connect succeeds in <1ms when alive; short timeout avoids Windows' 2s SYN retry.
                        upstream = socket.create_connection(("127.0.0.1", port), timeout=0.35)
                    chosen_scheme = scheme
                    break
                except OSError:
                    upstream = None
            if upstream is None:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                return

            if chosen_scheme == "socks5":
                if method == "CONNECT":
                    client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    if rest:
                        upstream.sendall(rest)
                else:
                    filtered_headers = b"\r\n".join(
                        line for line in header_block.split(b"\r\n")
                        if not line.lower().startswith(b"proxy-connection:")
                    )
                    upstream.sendall(origin_first + b"\r\n" + filtered_headers)
                    if rest:
                        upstream.sendall(rest)
            else:
                upstream.sendall(head)
                if rest:
                    upstream.sendall(rest)

            client.settimeout(None)
            upstream.settimeout(None)
            t1 = threading.Thread(target=_pipe_bytes, args=(client, upstream), daemon=True)
            t2 = threading.Thread(target=_pipe_bytes, args=(upstream, client), daemon=True)
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        except OSError:
            pass
        finally:
            for sock in (upstream, client):
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass
            with self._lock:
                self._active_conns = max(0, self._active_conns - 1)


def _pipe_bytes(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(32768)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass
