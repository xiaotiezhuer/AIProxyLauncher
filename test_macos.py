import plistlib
import socket
import threading
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import launcher
import macos
import repair


class MacOSTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(launcher, "IS_LINUX", False))
        self.enterContext(patch.object(repair, "IS_LINUX", False))

    def bundle(self, root, name, executable):
        app = root / (name + ".app")
        binary = app / "Contents" / "MacOS" / executable
        binary.parent.mkdir(parents=True)
        binary.touch()
        (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": executable}))
        return app, binary

    def test_discovery_and_launcher_resolve_application_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle, binary = self.bundle(root, "Antigravity IDE", "Electron")
            with patch.object(macos, "APPLICATIONS", (root,)):
                self.assertEqual(macos.detect_paths()["ide"], bundle)
            self.assertEqual(macos.executable(bundle), binary)

    def test_web_launch_uses_an_isolated_profile_and_checked_port(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            browser, binary = self.bundle(root, "Google Chrome", "Google Chrome")
            with patch.object(macos.subprocess, "Popen") as start:
                macos.launch(launcher.TARGETS["chatgpt"], browser, 7897, root, False)
            self.assertEqual(start.call_args.args[0][0], str(binary))
            self.assertIn("--proxy-server=http://127.0.0.1:7897", start.call_args.args[0])
            self.assertIn("--app=https://chatgpt.com/", start.call_args.args[0])

    def test_macos_lsof_only_attributes_established_connections(self):
        output = "p21\nfcwd\nnignored\nf8\nn127.0.0.1:43000->127.0.0.1:7897\nf9\nn[::1]:43001->[::1]:9097\np26\nf10\nn*:7897\n"
        self.assertEqual(macos.parse_tcp_connections(output),
                         [(21, 43000, "127.0.0.1", 7897), (21, 43001, "[::1]", 9097)])

    def test_only_mac_desktop_requires_tun_for_native_chat_client(self):
        with patch.object(launcher, "IS_MAC", True):
            self.assertTrue(launcher.requires_tun(launcher.TARGETS["chatgpt_desktop"]))
            self.assertFalse(launcher.requires_tun(launcher.TARGETS["chatgpt"]))
        with patch.object(launcher, "IS_MAC", False):
            self.assertFalse(launcher.requires_tun(launcher.TARGETS["chatgpt_desktop"]))

    def test_mac_codex_updates_its_own_dotenv_at_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle, _ = self.bundle(root, "ChatGPT", "ChatGPT")
            config = root / ".env"
            config.write_text("HTTPS_PROXY=http://127.0.0.1:9674\n", encoding="utf-8")
            with patch.object(launcher, "codex_dotenv", return_value=config), patch.object(macos, "open_app") as open_app:
                macos.launch(launcher.TARGETS["chatgpt_desktop"], bundle, 7897, root, False)
            self.assertIn(":7897", config.read_text(encoding="utf-8"))
            self.assertNotIn(":9674", config.read_text(encoding="utf-8"))
            open_app.assert_called_once_with(bundle)

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix sockets unavailable")
    def test_controller_can_read_over_local_unix_socket(self):
        with tempfile.TemporaryDirectory() as temp, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            path = str(Path(temp) / "m.sock")
            server.bind(path)
            server.listen()
            received = []

            def respond():
                with server.accept()[0] as connection:
                    received.append(connection.recv(4096).splitlines()[0])
                    body = b'{"mixed-port":7897}'
                    headers = f"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: {len(body)}\r\n\r\n"
                    connection.sendall(headers.encode("ascii") + body)

            thread = threading.Thread(target=respond, daemon=True)
            thread.start()
            self.assertEqual(launcher.controller("/configs", launcher.Controller(unix=path)), {"mixed-port": 7897})
            thread.join(1)
            self.assertEqual(received, [b"GET /configs HTTP/1.1"])

    def test_unix_controller_discovery_reads_local_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = Path(temp) / "clash-verge.yaml"
            cfg.write_text('external-controller-unix: "/tmp/verge/verge-mihomo.sock"\n'
                           'external-controller: "127.0.0.1:9097"\nsecret: "demo"\n', encoding="utf-8")
            with patch.object(launcher, "IS_MAC", True), \
                    patch.object(launcher, "clash_config_files", return_value=[("Clash Verge Rev", cfg)]):
                controls = launcher.clash_controllers()
            self.assertIn(launcher.Controller(unix="/tmp/verge/verge-mihomo.sock", name="Clash Verge Rev"), controls)
            self.assertIn(launcher.Controller(port=9097, name="Clash Verge Rev", secret="demo"), controls)

    def test_system_proxy_preference_only_uses_an_enabled_loopback_listener(self):
        output = "<dictionary> {\n HTTPEnable : 1\n HTTPProxy : 127.0.0.1\n HTTPPort : 7897\n HTTPSProxy : 203.0.113.2\n HTTPSPort : 8000\n HTTPSEnable : 1\n}\n"
        with patch.object(macos.subprocess, "check_output", return_value=output):
            self.assertEqual(macos.system_proxy_port(), 7897)

    def test_mac_tun_failure_reads_mac_service_hint_without_invoking_windows_setup(self):
        endpoint = launcher.Controller(name="Clash Verge Rev", unix="/tmp/verge/verge-mihomo.sock")
        probes = {host: launcher.Probe(host, True, "", "proxy") for host in launcher.GOOGLE}
        report = launcher.Report("rule", False, probes, 7897, provider=endpoint.name, endpoint=endpoint)
        with patch.object(repair, "IS_MAC", True), patch.object(repair, "check", return_value=report), \
                patch.object(repair, "system_proxy_port", return_value=7897), \
                patch.object(repair, "proxy_controllers", return_value=[]), \
                patch.object(repair, "prepare_tun", side_effect=ValueError("utun unavailable")), \
                patch.object(repair, "setup_clash_service") as windows_setup, \
                patch.object(macos.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "state = running", "")):
            reports, _, _, notes = repair.prepare_apps(["ide"], None, "Clash", None, False, True, lambda _: None)
        windows_setup.assert_not_called()
        self.assertFalse(reports[0].tun)
        self.assertIn("Clash 后台服务正在运行", notes[-1])

    def test_macos_detects_diverse_proxy_bundles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("FlClash", "v2rayN", "Hiddify", "Surge", "Stash", "Clash Party", "V2rayU", "NekoBox"):
                self.bundle(root, name, name)
            with patch.object(macos, "APPLICATIONS", (root,)):
                found = macos.proxy_apps()
            for expected in ("FlClash", "v2rayN", "Hiddify", "Surge", "Stash", "Clash Party", "V2rayU", "NekoBox"):
                self.assertIn(expected, found)

    def test_macos_listening_ports_and_utun_route_detection(self):
        lsof_out = (
            "p101\nf5\nn127.0.0.1:10808\nf6\nn*:7890\nf7\nn192.168.1.8:8080\n"
            "p202\nf8\nn[::1]:6152\nf9\nn127.0.0.1:41000->127.0.0.1:7890\n"
        )
        self.assertEqual(
            set(macos.parse_listening_ports(lsof_out)),
            {(101, "127.0.0.1", 10808), (101, "*", 7890), (202, "[::1]", 6152)},
        )

        idle_netstat = (
            "Destination        Gateway            Flags        Netif Expire\n"
            "default            192.168.1.1        UGScg          en0\n"
            "169.254            link#4             UCS            utun0\n"
        )
        self.assertFalse(macos.parse_mac_tun_routes(idle_netstat))

        fakeip_netstat = (
            "Destination        Gateway            Flags        Netif Expire\n"
            "default            192.168.1.1        UGScg          en0\n"
            "198.18/15          utun4              USc            utun4\n"
        )
        self.assertTrue(macos.parse_mac_tun_routes(fakeip_netstat))

        split_netstat = (
            "Destination        Gateway            Flags        Netif Expire\n"
            "0/1                172.19.0.2         UGSc           utun3\n"
            "128.0/1            172.19.0.2         UGSc           utun3\n"
        )
        self.assertTrue(macos.parse_mac_tun_routes(split_netstat))

        default_tun = (
            "Destination        Gateway            Flags        Netif Expire\n"
            "default            link#15            UCS            utun2\n"
        )
        self.assertTrue(macos.parse_mac_tun_routes(default_tun))

    def test_macos_supports_chromium_family_browsers_and_preset_apps(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            brave, binary = self.bundle(root, "Brave Browser", "Brave Browser")
            cursor, _ = self.bundle(root, "Cursor", "Cursor")
            with patch.object(macos, "APPLICATIONS", (root,)):
                detected = macos.detect_paths()
                self.assertEqual(detected["cursor"], cursor)
            with patch.object(macos.subprocess, "Popen") as start:
                macos.launch(launcher.TARGETS["chatgpt"], brave, 7897, root, False)
            self.assertEqual(start.call_args.args[0][0], str(binary))
            with patch.object(macos.subprocess, "Popen") as start:
                macos.launch(launcher.TARGETS["chatgpt"], brave, 17890, root, False, stable_profile=True)
            self.assertIn(f"--user-data-dir={root / 'Browser' / 'Brave Browser-relay'}", start.call_args.args[0])

    def test_create_ssl_context_falls_back_to_system_ca_bundle_and_keychain(self):
        import proxies
        import sys

        class FakeCtx:
            def __init__(self, count: int, tag: str):
                self._count = count
                self.tag = tag

            def cert_store_stats(self):
                return {"x509_ca": self._count}

        # 1. Empty default trust store -> loads /etc/ssl/cert.pem via cafile
        def fake_create(cafile=None, cadata=None):
            if cafile == "/etc/ssl/cert.pem":
                return FakeCtx(150, "file")
            return FakeCtx(0, "empty")

        with patch.object(proxies, "_SSL_CA_SOURCE", None), \
                patch.object(proxies.ssl, "create_default_context", side_effect=fake_create), \
                patch.object(proxies.os.path, "isfile", side_effect=lambda p: p == "/etc/ssl/cert.pem"):
            ctx = proxies.create_ssl_context()
            self.assertEqual(ctx.tag, "file")

        # 2. No CA file on disk on macOS -> falls back to SystemRootCertificates.keychain
        def fake_create_keychain(cafile=None, cadata=None):
            if cadata and "BEGIN CERTIFICATE" in cadata:
                return FakeCtx(120, "keychain")
            return FakeCtx(0, "empty")

        with patch.object(proxies, "_SSL_CA_SOURCE", None), \
                patch.object(proxies, "sys", type("S", (), {"platform": "darwin"})()), \
                patch.object(proxies.ssl, "create_default_context", side_effect=fake_create_keychain), \
                patch.object(proxies.os.path, "isfile", return_value=False), \
                patch.object(proxies.subprocess, "check_output", return_value="-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n"):
            ctx = proxies.create_ssl_context()
            self.assertEqual(ctx.tag, "keychain")

        # 3. Live verification on macOS CI runner: real context & Apple system CA bundle both load >= 10 CAs
        if sys.platform == "darwin":
            import ssl
            proxies._SSL_CA_SOURCE = None
            real_ctx = proxies.create_ssl_context()
            self.assertGreaterEqual(real_ctx.cert_store_stats().get("x509_ca", 0), 10)
            self.assertTrue(proxies.os.path.isfile("/etc/ssl/cert.pem"))
            apple_ctx = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
            self.assertGreaterEqual(apple_ctx.cert_store_stats().get("x509_ca", 0), 10)

    def test_macos_jimmm_socket_labeling_and_root_tun_port_attribution(self):
        import proxies

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.bundle(root, "Jimmm", "Jimmm")
            with patch.object(macos, "APPLICATIONS", (root,)):
                self.assertEqual(macos.party_socket_label(), "JImmm")
                socks = macos.sockets()
                self.assertTrue(any(label == "JImmm" and path.name == "mihomo-party.sock" for label, path in socks))
                self.assertTrue(any(label == "JImmm" and path.name == "jimmm.sock" for label, path in socks))

        # Root-owned TUN core hidden from non-root lsof: system proxy :9674 resolves to JImmm when Jimmm is running
        snap = proxies.discover_proxies(
            processes={101: ("Jimmm", 1), 102: ("mihomo", 101)},
            listeners=[],
            sys_port=9674,
            env_ports=[],
            static_controllers=[],
            is_mac=True,
            detected_apps={"JImmm": Path("/Applications/Jimmm.app")},
        )
        self.assertEqual(len(snap.http_proxies), 1)
        self.assertEqual(snap.http_proxies[0].name, "JImmm")
        self.assertEqual(snap.http_proxies[0].port, 9674)
