import json
import os
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import launcher as app
import proxies
import repair


class CheckTests(unittest.TestCase):
    _shared_tk_root = None

    @classmethod
    def _get_tk_root(cls):
        import tkinter as tk
        if cls._shared_tk_root is None:
            cls._shared_tk_root = tk.Tk()
            cls._shared_tk_root.withdraw()
        else:
            for child in list(cls._shared_tk_root.winfo_children()):
                try:
                    child.destroy()
                except tk.TclError:
                    pass
        return cls._shared_tk_root

    @classmethod
    def tearDownClass(cls):
        if cls._shared_tk_root is not None:
            try:
                cls._shared_tk_root.destroy()
            except Exception:
                pass
            cls._shared_tk_root = None
        super().tearDownClass()

    def setUp(self):
        # Exercise the Windows behavior independently of the machine running this suite.
        import proxies
        self.enterContext(patch.object(app, "IS_MAC", False))
        self.enterContext(patch.object(app, "IS_LINUX", False))
        self.enterContext(patch.object(repair, "IS_MAC", False))
        self.enterContext(patch.object(repair, "IS_LINUX", False))
        self.enterContext(patch.object(repair, "system_proxy_port", return_value=None))
        self.enterContext(patch.object(app, "detect_system_tun", return_value=False))
        self.enterContext(patch.object(proxies, "probe_exit_region", return_value=("", None)))

    def test_antigravity_covers_the_login_and_daily_model_endpoints(self):
        self.assertIn("oauth2.googleapis.com/token", app.TARGETS["agent"].hosts)
        self.assertIn("daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist", app.TARGETS["ide"].hosts)
        self.assertEqual(app.TARGETS["antigravity_web"].url, "https://antigravity.google.com/")

    def test_tun_preparation_disables_the_other_core_then_verifies(self):
        clash = app.Controller(pipe="verge-mihomo", name="Clash Verge Rev")
        active = {clash: False, app.JIMMM_CONTROLLER: True}
        changes = []

        def update(endpoint, enable):
            active[endpoint] = enable
            changes.append((endpoint, enable))

        with patch.object(repair, "tun_enabled", side_effect=active.__getitem__), \
                patch.object(repair, "set_tun", side_effect=update):
            result = repair.prepare_tun(clash, app.JIMMM_CONTROLLER, lambda _: None)
        self.assertEqual(changes, [(app.JIMMM_CONTROLLER, False), (clash, True)])
        self.assertTrue(result)

    def test_tun_preparation_restores_original_owner_if_verification_fails(self):
        clash = app.Controller(pipe="verge-mihomo", name="Clash Verge Rev")
        active = {clash: False, app.JIMMM_CONTROLLER: True}

        def validate(_):
            raise ValueError("系统线路超时")

        with patch.object(repair, "tun_enabled", side_effect=active.__getitem__), \
                patch.object(repair, "set_tun", side_effect=active.__setitem__):
            with self.assertRaisesRegex(ValueError, "系统线路超时"):
                repair.prepare_tun(clash, app.JIMMM_CONTROLLER, validate)
        self.assertEqual(active, {clash: False, app.JIMMM_CONTROLLER: True})

    def test_check_all_and_web_launch_do_not_toggle_tun(self):
        probes = {host: app.Probe(host, True, "", "proxy") for host in app.CHATGPT}
        report = app.Report("rule", False, probes, 7897,
                            provider="Clash Verge Rev", endpoint=app.Controller(pipe="verge-mihomo"))
        with patch.object(repair, "check", return_value=report), \
                patch.object(repair, "clash_service_state") as service, \
                patch.object(repair, "prepare_tun") as switch:
            for opening in (False, True):
                repair.prepare_apps(["chatgpt"], None, "Clash", None, False, opening, lambda _: None)
            service.assert_not_called()
            switch.assert_not_called()

    def test_google_node_change_restores_previous_node_if_probe_still_fails(self):
        group = "谷歌服务"
        controller = app.Controller(pipe="verge-mihomo")
        endpoint = app.OAUTH_TOKEN
        failure = app.Probe(endpoint, False, "超时")
        report = app.Report("rule", False, {endpoint: failure}, 7897, provider="Clash", endpoint=controller)
        groups = {group: {"type": "Selector", "all": ["Old", "New"], "now": "Old"},
                  "Old": {"type": "AnyTLS"}, "New": {"type": "AnyTLS", "alive": True}}
        writes = []

        def request(path, control, method, data, timeout):
            if method == "GET":
                return {"delay": 20} if "/delay?" in path else groups[group]
            writes.append(data["name"])
            groups[group]["now"] = data["name"]
            return {}

        with patch.object(repair, "controller_request", side_effect=request), \
                patch.object(repair, "controller", side_effect=[
                    {"proxies": groups}, {"rules": [{"proxy": group, "type": "GeoSite", "payload": "google"}]},
                ]), patch.object(repair, "connect_probe", return_value=failure):
            self.assertEqual(repair.repair_proxy_nodes(report, [endpoint]), [])
        self.assertEqual(writes, ["New", "Old"])

    def test_service_setup_uses_already_running_service_without_elevation(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "clash-verge.exe"
            binary.touch()
            with patch.object(repair, "detected_proxy_apps", return_value={"Clash Verge Rev": binary}), \
                    patch.object(repair, "clash_service_state", return_value="Running"), \
                    patch.object(repair, "powershell") as run:
                self.assertEqual(repair.setup_clash_service(), (binary, False))
                run.assert_not_called()

    def test_auto_falls_back_to_another_tun_after_first_fails(self):
        clash = app.Controller(pipe="verge-mihomo", name="Clash Verge Rev")
        probes = {host: app.Probe(host, True, "", "proxy") for host in app.GOOGLE}
        j = app.Report("rule", True, probes, 9674, provider="JImmm", endpoint=app.JIMMM_CONTROLLER)
        c = app.Report("rule", False, probes, 7897, provider=clash.name, endpoint=clash)
        with patch.object(repair, "check_reports", return_value=([j, c], ())), \
                patch.object(repair, "system_proxy_port", return_value=7897), \
                patch.object(repair, "tun_enabled", return_value=False), \
                patch.object(repair, "clash_controllers", return_value=[]), \
                patch.object(repair, "clash_service_state", return_value="Running"), \
                patch.object(repair, "clash_uses_service", return_value=True), \
                patch.object(repair, "prepare_tun", side_effect=[ValueError("超时"), False]) as switch:
            reports, _, _, messages = repair.prepare_apps(["ide"], None, app.AUTO, None, True, True, lambda _: None)
        self.assertEqual([call.args[0] for call in switch.call_args_list], [clash, app.JIMMM_CONTROLLER])
        self.assertEqual(app.app_report(app.TARGETS["ide"], reports, 7897).port, 9674)
        self.assertTrue(any("超时" in message for message in messages))

    def test_auto_uses_the_healthy_provider_for_each_app(self):
        google = app.TARGETS["agent"].hosts
        codex = app.TARGETS["chatgpt_desktop"].hosts
        def report(provider, port, good, bad):
            probes = {host: app.Probe(host, True, "", "proxy") for host in good}
            probes.update({host: app.Probe(host, False, "超时") for host in bad})
            return app.Report("rule", True, probes, port, provider=provider)
        jimmm = report("JImmm", 9674, google, codex)
        clash = report("Clash Verge Rev", 7897, codex, google)
        self.assertEqual(app.app_report(app.TARGETS["agent"], [jimmm, clash]).port, 9674)
        self.assertEqual(app.app_report(app.TARGETS["chatgpt_desktop"], [jimmm, clash]).port, 7897)

    def test_auto_prefers_windows_proxy_when_both_pass(self):
        target = app.TARGETS["chatgpt_desktop"]
        probes = {host: app.Probe(host, True, "", "proxy") for host in target.hosts}
        jimmm = app.Report("rule", False, probes, 9674, provider="JImmm")
        clash = app.Report("rule", True, probes, 7897, provider="Clash")
        self.assertEqual(app.app_report(target, [jimmm, clash], preferred_port=9674).port, 9674)

    def test_codex_service_probe_requires_websocket_endpoint_response(self):
        service = app.CODEX_RESPONSES
        for status, connected in [(401, True), (101, True), (403, False), (200, False)]:
            with self.subTest(status=status), patch.object(app, "open_tunnel") as tunnel, \
                    patch.object(app.ssl, "create_default_context") as context, \
                    patch.object(app.http.client, "HTTPResponse") as response:
                from unittest.mock import MagicMock
                connection = MagicMock()
                connection.getsockname.return_value = ("127.0.0.1", 12345)
                tunnel.return_value = (connection, "")
                response.return_value.status = status
                self.assertEqual(app.connect_probe(service, 7897).connected, connected)
                tunnel.assert_called_once_with("chatgpt.com", 7897, 8)
                tls = context.return_value.wrap_socket.return_value.__enter__.return_value
                request = tls.sendall.call_args.args[0]
                self.assertIn(b"GET /backend-api/codex/responses", request)
                self.assertIn(b"Upgrade: websocket", request)

    def test_codex_launch_switches_dotenv_and_preserves_unrelated_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            env = Path(temp) / ".env"
            original = b'OTHER=value\r\nHTTPS_PROXY="http://127.0.0.1:9674"\r\n'
            env.write_bytes(original)
            with patch.object(app, "codex_dotenv", return_value=env), \
                    patch.object(app, "process_running", return_value=False), \
                    patch.object(app.subprocess, "Popen") as popen:
                app.launch(app.TARGETS["chatgpt_desktop"], None, 7897)
                self.assertIn(b"http://127.0.0.1:7897", env.read_bytes())
                self.assertNotIn(b":9674", env.read_bytes())
                self.assertIn(b"OTHER=value\r\n", env.read_bytes())
                popen.assert_called_once()
                self.assertEqual([p.read_bytes() for p in Path(temp).glob("*.bak")], [original])

    def test_codex_launch_does_not_change_proxy_while_app_is_running(self):
        with patch.object(app, "process_running", return_value=True), patch.object(app, "codex_dotenv") as path:
            with self.assertRaisesRegex(ValueError, "已在运行"):
                app.launch(app.TARGETS["chatgpt_desktop"], None, 7897)
            path.assert_not_called()

    def test_codex_live_route_uses_desktop_process_tree_and_reports_actual_port(self):
        processes = {10: ("ChatGPT.exe", 1), 11: ("codex.exe", 10),
                     20: ("Antigravity IDE.exe", 1), 21: ("codex.exe", 20)}
        sockets = [(11, 4001, "127.0.0.1", 9674), (21, 4002, "127.0.0.1", 7897)]
        conn = {"metadata": {"sourceIP": "127.0.0.1", "sourcePort": "4001",
                             "host": "chatgpt.com", "inboundPort": "9674"}, "chains": ["DIRECT"]}
        routes = app.app_connections(processes, sockets, [("JImmm", 9674, [conn]),
                                                           ("Clash Verge Rev", 7897, [])], {})
        self.assertEqual(routes["chatgpt_desktop"], ["JImmm :9674 · 直连"])
        self.assertEqual(routes["ide"], ["Clash Verge Rev :7897 · 出口待核对"])

    def test_browsers_and_native_desktop_can_show_tun_without_inventing_a_port(self):
        processes = {10: ("Chrome.exe", 1), 11: ("chrome.exe", 10), 20: ("Claude.exe", 1)}
        sockets = [(11, 4001, "198.18.0.20", 443), (20, 4002, "198.18.0.21", 443)]
        def row(source, host, dest):
            return {"metadata": {"sourcePort": str(source), "destinationPort": "443",
                                 "destinationIP": dest, "host": host, "type": "Tun"}, "chains": ["US1"]}
        rows = [row(4001, "chatgpt.com", "198.18.0.20"), row(4002, "claude.ai", "198.18.0.21")]
        routes = app.app_connections(processes, sockets, [("Clash", 7897, rows)], {})
        self.assertEqual(routes, {"chatgpt": ["Clash TUN · 节点代理"], "claude_desktop": ["Clash TUN · 节点代理"]})

    def test_clash_uses_its_controller_and_reports_the_discovered_http_port(self):
        control = app.Controller(pipe="verge-mihomo")
        probe = app.Probe("chatgpt.com", True, "TLS 已连接", "proxy")
        config = {"mode": "rule", "mixed-port": 7898, "port": 7899, "tun": {"enable": True}}
        with patch.object(app, "clash_controllers", return_value=[control]), \
                patch.object(app, "controller", return_value=config) as api, \
                patch.object(app, "probe_with_retry", return_value=probe) as run:
            result = app.check(None, [probe.host], "Clash")
            self.assertEqual((result.port, result.mode, result.tun, result.issue), (7898, "rule", True, ""))
            api.assert_called_once_with("/configs", control)
            run.assert_called_once_with(probe.host, 7898, control)

    def test_wrong_provider_port_is_rejected_before_any_service_probe(self):
        control = app.Controller(pipe="verge-mihomo")
        with patch.object(app, "clash_controllers", return_value=[control]), \
                patch.object(app, "controller", return_value={"mixed-port": 7897}), \
                patch.object(app, "probe_with_retry") as run:
            result = app.check(9674, ["chatgpt.com"], "Clash")
            self.assertIn("7897", result.issue)
            self.assertIsNone(result.tun)
            run.assert_not_called()

    def test_explicit_clash_controller_does_not_fall_back_to_another_client(self):
        control = app.Controller(port=9090, secret="test-secret")
        with patch.object(app, "clash_controllers") as discover, \
                patch.object(app, "controller", return_value={}) as api, \
                patch.object(app, "probe_with_retry") as run:
            result = app.check(7890, ["chatgpt.com"], "Clash", control)
            self.assertTrue(result.issue)
            discover.assert_not_called()
            api.assert_called_once_with("/configs", control)
            run.assert_not_called()

    def test_clash_http_controller_uses_bearer_and_get_only(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer

        seen = []

        class LocalController(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"mixed-port":7890}')

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), LocalController) as server:
            control = app.Controller(port=server.server_port, secret="test-secret")
            thread = threading.Thread(target=server.handle_request, daemon=True)
            thread.start()
            self.assertEqual(app.controller("/configs", control), {"mixed-port": 7890})
            thread.join(2)
            with self.assertRaises(ValueError):
                app.controller("/configs?untrusted=1", control)
        self.assertEqual(seen, [("/configs", "Bearer test-secret")])

    def test_legacy_settings_keep_jimmm_port_and_clash_has_own_port(self):
        self.assertEqual(app.proxy_preferences({"port": "9676"}), (app.AUTO, {"JImmm": "9676", "Clash": ""}))
        self.assertEqual(app.proxy_preferences({"provider": "Clash"})[0], app.AUTO)
        saved = {"provider": "Clash", "automatic_detection": True, "ports": {"JImmm": "9676", "Clash": "7899"}}
        self.assertEqual(app.proxy_preferences(saved), ("Clash", saved["ports"]))

    def test_auto_falls_back_to_clash_when_jimmm_route_is_direct(self):
        host = "chatgpt.com"
        direct = app.Report("rule", True, {host: app.Probe(host, True, "TLS", "direct")}, 9674, provider="JImmm")
        good = app.Report("rule", True, {host: app.Probe(host, True, "TLS", "proxy")}, 7897, provider="Clash Verge Rev")
        with patch.object(app, "check_reports", return_value=([direct, good], ("JImmm", "Clash Verge Rev"))):
            result = app.check_auto([host], needs_tun=True)
        self.assertEqual((result.provider, result.port), ("Clash Verge Rev", 7897))
        self.assertEqual(result.detected, ("JImmm", "Clash Verge Rev"))

    def test_auto_prefers_a_tun_ready_client_for_desktop_apps(self):
        host = "chatgpt.com"
        ok = {host: app.Probe(host, True, "TLS", "proxy")}
        jimmm = app.Report("rule", False, ok, 9674, provider="JImmm")
        clash = app.Report("rule", True, ok, 7897, provider="Clash Verge Rev")
        with patch.object(app, "check_reports", return_value=([jimmm, clash], ())):
            self.assertEqual(app.check_auto([host], needs_tun=True).port, 7897)

    def test_configured_clash_controller_is_detected_without_using_remote_addresses(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            safe = root / "config.yaml"
            safe.write_text('external-controller: "localhost:9876" # local\nsecret: "local-demo"\n', encoding="utf-8")
            remote = root / "remote.yaml"
            remote.write_text("external-controller: 192.0.2.1:9876\n", encoding="utf-8")
            with patch.object(app, "clash_config_files", return_value=[("Mihomo", safe), ("Clash for Windows", remote)]):
                controls = app.clash_controllers()
            self.assertIn(app.Controller(port=9876, secret="local-demo", name="Mihomo"), controls)
            self.assertEqual([c.port for c in controls if c.port], [9876])

    def test_only_local_proxy_ports(self):
        self.assertEqual(app.parse_port("http://127.0.0.1:9674"), 9674)
        self.assertEqual(app.parse_port("7897"), 7897)
        for value in ("0", "65536", "user@127.0.0.1:9674", "1.2.3.4:9999", "socks5://127.0.0.1:9674"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.parse_port(value)

    def test_proxy_has_no_external_bypass_or_direct_fallback(self):
        env = app.proxy_env(9674, {"https_proxy": "old", "NO_PROXY": "*", "X": "keep"})
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:9674")
        self.assertNotIn("https_proxy", env)
        self.assertEqual(env["NO_PROXY"], "localhost,127.0.0.1,::1")
        self.assertEqual(env["X"], "keep")
        args = app.chromium_args(9674)
        self.assertIn("--proxy-server=http://127.0.0.1:9674", args)
        self.assertFalse(any("direct://" in arg for arg in args))

    def test_route_matches_local_socket_and_exact_destination(self):
        def connection(host, port, chain, inbound=9674):
            return {"metadata": {"sourceIP": "127.0.0.1", "sourcePort": str(port), "host": host,
                                 "inboundPort": str(inbound)}, "chains": chain}
        rows = {"connections": [connection("chatgpt.com", 9111, ["wrong port"], 9999),
                                connection("other.test", 9111, ["unrelated"]),
                                connection("chatgpt.com", 9111, ["DIRECT", "Jimmm"]),
                                connection("claude.ai", 9112, ["US-01", "Jimmm"])]}
        self.assertEqual(app.observed_route(rows, "chatgpt.com", 9111, 9674), "direct")
        self.assertEqual(app.observed_route(rows, "claude.ai", 9112, 9674), "proxy")
        self.assertEqual(app.observed_route(rows, "missing.test", 9111, 9674), "unknown")

    def test_launch_gate_and_native_tun_requirement(self):
        result = app.Probe("chatgpt.com", True, "TLS 已连接", "direct")
        self.assertIsNotNone(app.block_reason([result], False, True))
        result = app.Probe("chatgpt.com", True, "TLS 已连接", "unknown")
        self.assertIsNotNone(app.block_reason([result], False, False))
        result = app.Probe("chatgpt.com", True, "TLS 已连接", "proxy")
        self.assertIsNone(app.block_reason([result], False, False))
        self.assertIsNone(app.block_reason([result], True, True))
        self.assertIsNotNone(app.block_reason([result], True, None))

    def test_connect_failure_is_reported_without_contacting_origin(self):
        class Refuse(socketserver.StreamRequestHandler):
            def handle(self):
                seen.append(self.rfile.readline())
                self.wfile.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")

        seen = []
        with socketserver.TCPServer(("127.0.0.1", 0), Refuse) as server:
            thread = threading.Thread(target=server.handle_request, daemon=True)
            thread.start()
            result = app.connect_probe("never-resolve.invalid", server.server_address[1])
            thread.join(2)
        self.assertFalse(result.connected)
        self.assertIn("407", result.detail)
        self.assertEqual(seen, [b"CONNECT never-resolve.invalid:443 HTTP/1.1\r\n"])

    def test_web_launch_uses_isolated_profile_and_local_proxy(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app.subprocess, "Popen") as popen:
            root = Path(temp)
            browser = root / "msedge.exe"
            browser.touch()
            app.launch(app.TARGETS["chatgpt"], browser, 9674, root)
            args = popen.call_args.args[0]
            self.assertIn(f"--user-data-dir={root / 'Browser' / 'msedge-9674'}", args)
            self.assertIn("--proxy-server=http://127.0.0.1:9674", args)
            self.assertEqual(args[-1], "--app=https://chatgpt.com/")
            self.assertEqual(popen.call_args.kwargs["env"]["HTTPS_PROXY"], "http://127.0.0.1:9674")

    def test_only_transient_service_failures_get_one_retry(self):
        host = "chatgpt.com"
        timeout = app.Probe(host, False, "端口有响应，服务连接超时")
        success = app.Probe(host, True, "TLS 已连接", "proxy")
        direct = app.Probe(host, True, "TLS 已连接", "direct")
        with patch.object(app, "connect_probe", side_effect=[timeout, success]) as probe:
            self.assertEqual(app.probe_with_retry(host, 9674, True), success)
            self.assertEqual(probe.call_count, 2)
        with patch.object(app, "connect_probe", return_value=direct) as probe:
            self.assertEqual(app.probe_with_retry(host, 9674, True), direct)
            probe.assert_called_once()

    def test_running_antigravity_does_not_open_an_unconfigured_instance(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app, "process_running", return_value=True), patch.object(app.subprocess, "Popen") as popen:
            binary = Path(temp) / "Antigravity.exe"
            binary.touch()
            with self.assertRaisesRegex(ValueError, "已在运行"):
                app.launch(app.TARGETS["agent"], binary, 9674)
            popen.assert_not_called()

    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Path(temp) / "settings.json"
            app.save_settings(settings, {"port": 9674, "paths": {"ide": "D:/中文/IDE.exe"}})
            self.assertEqual(app.read_settings(settings)["paths"]["ide"], "D:/中文/IDE.exe")
            settings.write_text("[invalid]", encoding="utf-8")
            self.assertEqual(app.read_settings(settings), {})

    def test_universal_discovery_detects_v2rayn_without_clash_controller(self):
        import proxies
        processes = {100: ("v2rayN.exe", 1), 101: ("xray.exe", 100)}
        listeners = [(101, "127.0.0.1", 10808), (101, "127.0.0.1", 10811)]
        with patch.object(proxies, "windows_active_proxy_pipes", return_value=[]), \
                patch.object(proxies, "windows_existing_pipes", return_value=set()):
            snap = proxies.discover_proxies(
                processes, listeners, None, [], [],
                is_mac=False, detected_apps={"v2rayN": None},
                probe_controller=lambda port, name: (None, False),
            )
        self.assertEqual([c.port for c in snap.http_proxies], [10808, 10811])
        self.assertEqual([c.name for c in snap.http_proxies], ["v2rayN", "v2rayN"])

        with patch.object(app, "discover_live_proxies", return_value=snap), \
                patch.object(app, "probe_http_proxy_port", side_effect=lambda port, **kw: "http_proxy" if port == 10808 else "not_proxy"), \
                patch.object(app, "detect_system_tun", return_value=False), \
                patch.object(app, "connect_probe", return_value=app.Probe("chatgpt.com", True, "网站有响应（HTTP 200）", "http_proxy")) as probe:
            reports, detected = app.check_reports(["chatgpt.com"], False)
        self.assertEqual(len(reports), 1)
        self.assertEqual((reports[0].provider, reports[0].port, reports[0].endpoint), ("v2rayN", 10808, None))
        self.assertIsNone(app.block_reason(list(reports[0].probes.values()), False, reports[0].tun, reports[0].provider))
        self.assertIn("v2rayN", detected)
        probe.assert_called_once_with("chatgpt.com", 10808, endpoint=None, allow_uncontrolled=True)

    def test_universal_discovery_detects_unknown_proxy_via_system_proxy_and_promotes_api(self):
        import proxies
        processes = {500: ("MyAirport.exe", 1), 501: ("engine.exe", 500)}
        listeners = [(501, "127.0.0.1", 17890), (501, "127.0.0.1", 19090)]
        with patch.object(proxies, "windows_active_proxy_pipes", return_value=[]), \
                patch.object(proxies, "windows_existing_pipes", return_value=set()):
            # Case 1: unknown proxy without Clash API
            snap_http = proxies.discover_proxies(
                processes, listeners, 17890, [], [],
                is_mac=False, detected_apps={},
                probe_controller=lambda port, name: (None, False),
            )
            # Case 2: unknown proxy exposing Clash API on sibling port 19090
            snap_ctrl = proxies.discover_proxies(
                processes, listeners, 17890, [], [],
                is_mac=False, detected_apps={},
                probe_controller=lambda port, name: (app.Controller(port=port, name=name), False) if port == 19090 else (None, False),
            )
        self.assertEqual(snap_http.http_proxies[0], proxies.HTTPProxyCandidate(17890, "MyAirport", "system", 501))
        self.assertIn("MyAirport", snap_http.detected_apps)
        self.assertIn(app.Controller(port=19090, name="MyAirport"), snap_ctrl.controllers)
        self.assertEqual(snap_ctrl.controller_sibling_ports[app.Controller(port=19090, name="MyAirport")], [17890])

    def test_singbox_controller_pairs_with_sibling_http_port_when_config_port_is_zero(self):
        import proxies
        ctrl = app.Controller(port=16756, name="Hiddify")
        snap = proxies.DiscoverySnapshot(
            controllers=[ctrl],
            http_proxies=[proxies.HTTPProxyCandidate(12334, "Hiddify", "process", 200)],
            detected_apps={"Hiddify": None},
            hints=[],
            controller_sibling_ports={ctrl: [12334]},
        )
        with patch.object(app, "discover_live_proxies", return_value=snap), \
                patch.object(app, "controller", return_value={"mode": "rule", "port": 0, "mixed-port": 0, "tun": {"enable": True}}), \
                patch.object(app, "probe_http_proxy_port", return_value="http_proxy"), \
                patch.object(app, "probe_with_retry", return_value=app.Probe("chatgpt.com", True, "TLS 已连接", "proxy")):
            reports, _ = app.check_reports(["chatgpt.com"], True)
        self.assertEqual(len(reports), 1)
        self.assertEqual((reports[0].provider, reports[0].port, reports[0].tun, reports[0].endpoint), ("Hiddify", 12334, True, ctrl))

    def test_protected_controller_and_socks5_only_emit_helpful_hints(self):
        import proxies
        processes = {10: ("Shadowsocks.exe", 1), 20: ("mihomo.exe", 1)}
        listeners = [(10, "127.0.0.1", 1080), (20, "127.0.0.1", 9090)]
        with patch.object(proxies, "windows_active_proxy_pipes", return_value=[]), \
                patch.object(proxies, "windows_existing_pipes", return_value=set()):
            snap = proxies.discover_proxies(
                processes, listeners, None, [], [],
                is_mac=False, detected_apps={},
                probe_controller=lambda port, name: (None, port == 9090),
            )
        self.assertTrue(any("9090" in h and "密钥" in h for h in snap.hints))
        self.assertNotIn(9090, [c.port for c in snap.http_proxies])
        hints: list[str] = []
        with patch.object(app, "discover_live_proxies", return_value=snap), \
                patch.object(app, "probe_http_proxy_port", return_value="socks5"):
            reports, _ = app.check_reports(["chatgpt.com"], False, hints=hints)
        self.assertEqual(reports, [])
        self.assertTrue(any("SOCKS5" in h and "1080" in h for h in hints))

    def test_manual_port_mode_probes_any_local_http_port(self):
        empty = app.check(None, ["chatgpt.com"], app.MANUAL)
        self.assertTrue(empty.issue)
        with patch.object(app, "detect_system_tun", return_value=False), \
                patch.object(app, "connect_probe", return_value=app.Probe("chatgpt.com", True, "网站有响应（HTTP 200）", "http_proxy")):
            result = app.check(8118, ["chatgpt.com"], app.MANUAL)
        self.assertEqual((result.port, result.issue, result.endpoint), (8118, "", None))
        self.assertIsNone(app.block_reason(list(result.probes.values()), False, result.tun, result.provider))

    def test_windows_tun_route_parser_handles_clash_singbox_and_inactive_tap(self):
        import proxies
        inactive = """
===========================================================================
Interface List
  9...00 ff 6e 07 20 1e ......TAP-Windows Adapter V9
  2...00 ff 06 1e 43 0a ......Netease UU TAP-Win32 Adapter V9.21
 19...00 e0 4c a8 0a 19 ......Realtek PCIe GbE Family Controller
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0      192.168.1.1      192.168.0.7     35
        127.0.0.0        255.0.0.0         On-link         127.0.0.1    331
"""
        clash_tun = """
Interface List
  6...........................Meta Tunnel
Active Routes:
          0.0.0.0          0.0.0.0      192.168.1.1      192.168.0.7     35
          0.0.0.0          0.0.0.0       198.18.0.2       198.18.0.1      0
       198.18.0.0  255.255.255.252          在链路上        198.18.0.1    256
"""
        singbox_split = """
Interface List
  8...........................sing-tun
Active Routes:
          0.0.0.0        128.0.0.0         On-link        172.19.0.1      5
        128.0.0.0        128.0.0.0         On-link        172.19.0.1      5
          0.0.0.0          0.0.0.0      192.168.1.1      192.168.0.7     35
"""
        self.assertFalse(proxies.parse_windows_tun_routes(inactive))
        self.assertTrue(proxies.parse_windows_tun_routes(clash_tun))
        self.assertTrue(proxies.parse_windows_tun_routes(singbox_split))

    def test_system_tun_validation_supports_non_controller_proxies_for_desktop_apps(self):
        probes = {host: app.Probe(host, True, "TLS 已连接", "http_proxy") for host in app.GOOGLE}
        v2ray = app.Report("http", True, probes, 10808, provider="v2rayN", endpoint=None)
        with patch.object(repair, "check_reports", return_value=([v2ray], ("v2rayN",))), \
                patch.object(repair, "validate_system_tun") as verify:
            reports, _, _, notes = repair.prepare_apps(["ide"], None, app.AUTO, None, True, True, lambda _: None)
        verify.assert_called_once()
        self.assertTrue(reports[0].tun)
        self.assertTrue(any("系统 TUN" in note for note in notes))

        with patch.object(repair, "check_reports", return_value=([v2ray], ("v2rayN",))), \
                patch.object(repair, "validate_system_tun", side_effect=ValueError("系统路由连接失败")):
            reports, _, _, notes = repair.prepare_apps(["ide"], None, app.AUTO, None, True, True, lambda _: None)
        self.assertFalse(reports[0].tun)
        self.assertTrue(any("未通过" in note for note in notes))

    def test_live_connections_shows_generic_http_proxy_and_fake_ip_tun(self):
        processes = {10: ("ChatGPT.exe", 1), 20: ("Claude.exe", 1)}
        sockets = [(10, 5001, "127.0.0.1", 10808), (20, 5002, "198.18.0.44", 443)]
        routes = app.app_connections(processes, sockets, [("v2rayN", 10808, None)], {})
        self.assertEqual(routes["chatgpt_desktop"], ["v2rayN :10808 · 代理连接中"])
        self.assertEqual(routes["claude_desktop"], ["系统 TUN · 虚拟网卡连接中"])

    def test_node_repair_triggers_for_any_controller_backed_provider(self):
        probes = {host: app.Probe(host, False, "超时") for host in app.CHATGPT}
        flclash = app.Report("rule", False, probes, 7890, provider="FlClash", endpoint=app.Controller(port=9090, name="FlClash"))
        with patch.object(repair, "check_reports", return_value=([flclash], ("FlClash",))), \
                patch.object(repair, "repair_proxy_nodes", return_value=["Proxy 已换到通过复测的节点 · JP1"]) as fix:
            _, _, _, notes = repair.prepare_apps(["chatgpt"], None, app.AUTO, None, True, True, lambda _: None)
        fix.assert_called_once()
        self.assertIn("JP1", notes[0])

    def test_singbox_json_and_wildcard_local_controllers_are_parsed_safely(self):
        import proxies
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            good = root / "singbox.json"
            good.write_text(json.dumps({"experimental": {"clash_api": {"external_controller": "0.0.0.0:9090", "secret": "s1"}}}), encoding="utf-8")
            bad = root / "remote.json"
            bad.write_text(json.dumps({"experimental": {"clash_api": {"external_controller": "192.0.2.1:9090"}}}), encoding="utf-8")
            yaml_wild = root / "mihomo.yaml"
            yaml_wild.write_text('external-controller: ":9097"\n', encoding="utf-8")
            self.assertEqual(proxies.parse_json_controller_file(good, "Hiddify"), [app.Controller(port=9090, secret="s1", name="Hiddify")])
            self.assertEqual(proxies.parse_json_controller_file(bad, "Hiddify"), [])
            _, http = proxies.parse_yaml_controller_file(yaml_wild, "Mihomo")
            self.assertEqual(http, [app.Controller(port=9097, name="Mihomo")])

    def test_observed_route_accepts_singbox_zero_inbound_port(self):
        rows = {"connections": [
            {"metadata": {"sourceIP": "127.0.0.1", "sourcePort": "9222", "host": "chatgpt.com", "inboundPort": "0"}, "chains": ["US-01"]},
            {"metadata": {"sourceIP": "127.0.0.1", "sourcePort": "9223", "host": "chatgpt.com", "inboundPort": "9999"}, "chains": ["US-01"]},
        ]}
        self.assertEqual(app.observed_route(rows, "chatgpt.com", 9222, 12334), "proxy")
        self.assertEqual(app.observed_route(rows, "chatgpt.com", 9223, 12334), "unknown")

    def test_pipe_display_name_distinguishes_clash_party_and_jimmm(self):
        import proxies
        self.assertEqual(proxies.pipe_display_name(r"MihomoParty\mihomo", {1: ("Clash Party.exe", 0)}), "Clash Party")
        self.assertEqual(proxies.pipe_display_name(r"MihomoParty\mihomo", {1: ("Jimmm.exe", 0)}), "JImmm")
        self.assertEqual(proxies.pipe_display_name("verge-mihomo", {}), "Clash Verge Rev")
        self.assertEqual(proxies.pipe_display_name("nyanpasu-mihomo", {}), "Clash Nyanpasu")

    def test_region_helpers_and_preference_over_restricted_region(self):
        import proxies
        self.assertEqual(proxies.region_label("US"), "美国")
        self.assertTrue(proxies.is_restricted_region("HK"))
        self.assertFalse(proxies.is_restricted_region("JP"))
        self.assertTrue(proxies.node_looks_restricted("香港 01 | IPLC"))
        self.assertTrue(proxies.node_looks_restricted("HK-Edge-02"))
        self.assertFalse(proxies.node_looks_restricted("日本东京 01"))

        target = app.TARGETS["chatgpt"]
        ok = {host: app.Probe(host, True, "OK", "proxy") for host in target.hosts}
        hk = app.Report("rule", True, ok, 9674, provider="JImmm", region="HK", endpoint=app.JIMMM_CONTROLLER)
        us = app.Report("rule", True, ok, 7897, provider="Clash Verge Rev", region="US", endpoint=app.Controller(pipe="verge-mihomo"))
        self.assertEqual(app.app_report(target, [hk, us], preferred_port=9674).port, 7897)

    def test_node_repair_avoids_restricted_region_nodes_when_ranking(self):
        group = "AI"
        ctrl = app.Controller(pipe="verge-mihomo")
        endpoint = app.OAUTH_TOKEN
        failure = app.Probe(endpoint, False, "超时")
        ok = app.Probe(endpoint, True, "OK", "proxy")
        report = app.Report("rule", False, {endpoint: failure}, 7897, provider="Clash Verge Rev", endpoint=ctrl)
        groups = {
            group: {"type": "Selector", "all": ["Old", "香港 01", "日本 01"], "now": "Old"},
            "Old": {"type": "AnyTLS"},
            "香港 01": {"type": "AnyTLS", "alive": True},
            "日本 01": {"type": "AnyTLS", "alive": True},
        }
        writes = []

        def request(path, control, method, data, timeout):
            if method == "GET":
                # HK is faster (20ms) than JP (90ms), but JP should still be tried first!
                return {"delay": 20 if "%E9%A6%99%E6%B8%AF" in path else 90} if "/delay?" in path else groups[group]
            writes.append(data["name"])
            groups[group]["now"] = data["name"]
            return {}

        with patch.object(repair, "controller_request", side_effect=request), \
                patch.object(repair, "controller", side_effect=[
                    {"proxies": groups}, {"rules": [{"proxy": group, "type": "GeoSite", "payload": "google"}]},
                ]), patch.object(repair, "connect_probe", return_value=ok):
            notes = repair.repair_proxy_nodes(report, [endpoint])
        self.assertEqual(writes, ["日本 01"])
        self.assertTrue(any("日本 01" in n for n in notes))

    def test_selector_group_listing_delay_and_switch_helpers(self):
        ctrl = app.Controller(port=9097, name="Clash Verge Rev")
        proxies_resp = {
            "proxies": {
                "Misc": {"type": "Selector", "now": "US-1", "all": ["US-1", "DIRECT"]},
                "OpenAI": {"type": "Selector", "now": "HK-1", "all": ["HK-1", "JP-1"]},
                "Auto": {"type": "URLTest", "now": "JP-1", "all": ["JP-1"]},
                "HK-1": {"type": "SS", "alive": True, "history": [{"delay": 40}]},
                "JP-1": {"type": "SS", "alive": True, "history": [{"delay": 110}]},
                "US-1": {"type": "SS", "alive": True, "history": []},
            }
        }
        with patch.object(repair, "controller", return_value=proxies_resp):
            groups = repair.list_selector_groups(ctrl)
        self.assertEqual([g["name"] for g in groups], ["OpenAI", "Misc"])
        self.assertTrue(groups[0]["nodes"][0]["restricted"])

        def req(path, endpoint, method, data, timeout):
            if "/delay?" in path:
                return {"delay": 88} if "JP-1" in path else None
            if method == "PUT":
                return {}
            return {"now": "JP-1"}

        with patch.object(repair, "controller_request", side_effect=req):
            delays = repair.test_group_delays(ctrl, ["JP-1", "HK-1"])
            self.assertEqual(delays, {"JP-1": 88, "HK-1": None})
            repair.switch_group_node(ctrl, "OpenAI", "JP-1")

    def test_envcheck_audits_and_repairs_stale_configs_with_backups(self):
        import envcheck
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            codex_env = home / ".codex" / ".env"
            codex_env.parent.mkdir(parents=True)
            codex_env.write_text("KEEP=1\nHTTPS_PROXY=http://127.0.0.1:9674\n", encoding="utf-8")
            gitconfig = home / ".gitconfig"
            gitconfig.write_text("[user]\n\tname = demo\n[http]\n\tproxy = http://127.0.0.1:9674\n", encoding="utf-8")
            npmrc = home / ".npmrc"
            npmrc.write_text("registry=https://registry.npmjs.org/\nhttps-proxy=http://127.0.0.1:9674\n", encoding="utf-8")

            conflicts = envcheck.audit_environment(
                [7897], 7897, home=home, codex_env=codex_env,
                user_env={"HTTPS_PROXY": "http://127.0.0.1:9674", "NO_PROXY": "*"},
                process_env={},
                port_checker=lambda p: p == 7897,
            )
            ids = {c.id for c in conflicts}
            self.assertIn("winenv:HTTPS_PROXY", ids)
            self.assertIn("winenv:NO_PROXY", ids)
            self.assertIn("codex:9674", ids)
            self.assertIn("git:http.proxy", ids)
            self.assertIn("npm:https-proxy", ids)

            for item in conflicts:
                if item.path:
                    envcheck.fix_conflict(item)
            self.assertIn("http://127.0.0.1:7897", codex_env.read_text(encoding="utf-8"))
            self.assertIn("KEEP=1", codex_env.read_text(encoding="utf-8"))
            self.assertTrue((codex_env.parent / ".env.ai-proxy-launcher.bak").is_file())
            self.assertIn("proxy = http://127.0.0.1:7897", gitconfig.read_text(encoding="utf-8"))
            self.assertIn("https-proxy=http://127.0.0.1:7897", npmrc.read_text(encoding="utf-8"))

    def test_custom_targets_presets_and_chromium_browsers_are_supported(self):
        saved = {
            "enabled_presets": ["cursor", "grok"],
            "hidden_targets": ["gemini"],
            "custom_targets": [{"key": "custom_ds", "name": "DeepSeek", "kind": "web", "url": "https://chat.deepseek.com/"}],
        }
        custom, presets, hidden = app.parse_custom_targets(saved)
        active = app.build_active_targets(custom, presets, hidden)
        self.assertIn("cursor", active)
        self.assertIn("grok", active)
        self.assertIn("custom_ds", active)
        self.assertNotIn("gemini", active)
        self.assertEqual(active["custom_ds"].hosts, ("chat.deepseek.com",))
        self.assertEqual(app.serialize_custom_targets(custom)[0]["key"], "custom_ds")

        with tempfile.TemporaryDirectory() as temp, patch.object(app.subprocess, "Popen") as popen:
            root = Path(temp)
            brave = root / "brave.exe"
            brave.touch()
            app.launch(active["custom_ds"], brave, 7897, root)
            self.assertIn("--app=https://chat.deepseek.com/", popen.call_args.args[0])

    def test_running_apps_and_restart_and_launch_flow(self):
        processes = {10: ("Antigravity IDE.exe", 1), 20: ("Cursor.exe", 1), 30: ("notepad.exe", 1)}
        active = app.build_active_targets(enabled_presets=["cursor"])
        running = app.running_apps(processes, {}, active)
        self.assertEqual(running, {"ide", "cursor"})

        with patch.object(app, "close_app_gracefully", return_value=True) as close_fn, \
                patch.object(app, "launch") as launch_fn:
            app.restart_and_launch(app.TARGETS["ide"], Path("C:/IDE.exe"), 7897)
        close_fn.assert_called_once()
        launch_fn.assert_called_once()

        with patch.object(app, "close_app_gracefully", return_value=False):
            with self.assertRaisesRegex(ValueError, "未能自动关闭"):
                app.restart_and_launch(app.TARGETS["ide"], Path("C:/IDE.exe"), 7897)

    def test_local_proxy_relay_domain_routing_hotswap_failover_and_no_leak(self):
        import relay
        import socket
        import threading

        def start_mock_proxy(tag: bytes):
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", 0))
            srv.listen(8)
            stop = threading.Event()

            def loop():
                srv.settimeout(0.2)
                while not stop.is_set():
                    try:
                        conn, _ = srv.accept()
                    except TimeoutError:
                        continue
                    except OSError:
                        break
                    with conn:
                        req = conn.recv(2048)
                        if req.startswith(b"CONNECT "):
                            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                            conn.sendall(tag)
                        else:
                            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(tag)).encode() + b"\r\n\r\n" + tag)

            t = threading.Thread(target=loop, daemon=True)
            t.start()
            return srv, srv.getsockname()[1], stop, t

        srv_a, port_a, stop_a, t_a = start_mock_proxy(b"UPSTREAM_A")
        srv_b, port_b, stop_b, t_b = start_mock_proxy(b"UPSTREAM_B")
        r = relay.LocalProxyRelay(preferred_port=0)
        try:
            rport = r.start()
            self.assertGreater(rport, 0)

            def probe_connect(host: str) -> bytes:
                with socket.create_connection(("127.0.0.1", rport), timeout=2.5) as s:
                    s.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode("ascii"))
                    s.settimeout(2.5)
                    chunks = []
                    while True:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    return b"".join(chunks)

            # 1. No upstream configured -> strict HTTP 502 (never leaks direct)
            resp_none = probe_connect("chatgpt.com")
            self.assertIn(b"502", resp_none.splitlines()[0])

            # 2. Per-domain routing: chatgpt -> A, claude -> B
            r.update_routes(port_a, {"claude.ai": port_b}, [port_b])
            self.assertIn(b"UPSTREAM_A", probe_connect("chatgpt.com"))
            self.assertIn(b"UPSTREAM_B", probe_connect("claude.ai"))

            # 3. Live hot-swap without restarting relay
            r.update_routes(port_b, {}, [port_a])
            self.assertIn(b"UPSTREAM_B", probe_connect("chatgpt.com"))

            # 4. Automatic failover when primary upstream dies
            stop_b.set()
            try:
                srv_b.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            srv_b.close()
            t_b.join(timeout=2.0)
            self.assertIn(b"UPSTREAM_A", probe_connect("chatgpt.com"))
            _, total, _ = r.stats()
            self.assertGreaterEqual(total, 4)
        finally:
            r.stop()
            stop_a.set()
            stop_b.set()
            for srv in (srv_a, srv_b):
                try:
                    srv.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                srv.close()
            t_a.join(timeout=1.0)
            t_b.join(timeout=1.0)

    def test_single_instance_lock_and_wakeup_callback(self):
        import threading
        import win32net

        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            woke = threading.Event()
            name = f"TestAIProxyLauncher_{os.getpid()}_{id(woke)}"
            g1 = win32net.SingleInstance(data_dir, name=name)
            g2 = win32net.SingleInstance(data_dir, name=name)
            try:
                self.assertTrue(g1.try_acquire_or_wake())
                g1.start_listener(woke.set)
                self.assertFalse(g2.try_acquire_or_wake())
                self.assertTrue(woke.wait(timeout=2.0))
            finally:
                g2.release()
                g1.release()

    def test_node_region_inference_and_observed_route_detail(self):
        self.assertEqual(proxies.infer_region_from_node("🇺🇸 美国 02 IPLC"), "US")
        self.assertEqual(proxies.infer_region_from_node("🇯🇵 日本东京"), "JP")
        self.assertEqual(proxies.infer_region_from_node("HK-BGP-01"), "HK")
        self.assertEqual(proxies.infer_region_from_node("新加坡 03"), "SG")
        self.assertEqual(proxies.infer_region_from_node("DIRECT"), "")

        snapshot = {
            "connections": [
                {
                    "chains": ["美国 02", "OpenAI", "GLOBAL"],
                    "metadata": {
                        "sourceIP": "127.0.0.1",
                        "sourcePort": "54321",
                        "inboundPort": "7897",
                        "host": "chatgpt.com",
                    },
                }
            ]
        }
        route, node = app.observed_route_detail(snapshot, "chatgpt.com", 54321, 7897)
        self.assertEqual(route, "proxy")
        self.assertEqual(node, "美国 02")

    def test_dns_ipv6_health_audit_and_sanitized_diagnostic_report(self):
        import envcheck

        # 1. Fake-IP under TUN -> ok
        ok_findings = envcheck.diagnose_network_health(
            tun_active=True,
            resolver=lambda hosts: {h: ["198.18.0.12"] for h in hosts},
            ipv6_detector=lambda: "",
        )
        self.assertTrue(any(f.category == "DNS" and f.status == "ok" and "Fake-IP" in f.title for f in ok_findings))
        self.assertTrue(any(f.category == "IPv6" and f.status == "ok" for f in ok_findings))

        # 2. Identical non-Fake-IP across distinct AI domains + global IPv6 leak -> error + warn
        bad_findings = envcheck.diagnose_network_health(
            tun_active=True,
            resolver=lambda hosts: {h: ["31.13.66.1"] for h in hosts},
            ipv6_detector=lambda: "2409:8a00:1234::1",
        )
        self.assertTrue(any(f.category == "DNS" and f.status == "error" for f in bad_findings))
        self.assertTrue(any(f.category == "IPv6" and f.status == "warn" for f in bad_findings))

        # 3. Sanitized diagnostic report contains key fields and strips secrets
        rep = app.Report(
            "rule", True,
            {"chatgpt.com": app.Probe("chatgpt.com", True, "HTTP 200", "proxy", 120, "美国 02", "US")},
            7897, provider="Clash Verge Rev",
            endpoint=app.Controller(port=9097, secret="TOP_SECRET_TOKEN", name="Clash Verge Rev"),
            region="US", latency_ms=120,
        )
        text = envcheck.build_diagnostic_report([rep], ("Clash Verge Rev",), bad_findings, [], relay_port=17890)
        self.assertIn("脱敏诊断报告", text)
        self.assertIn("Clash Verge Rev", text)
        self.assertIn("美国 02", text)
        self.assertIn("127.0.0.1:17890", text)
        self.assertNotIn("TOP_SECRET_TOKEN", text)

    def test_stable_relay_profile_and_native_pipe_controller_request(self):
        import win32net

        with tempfile.TemporaryDirectory() as temp, patch.object(app.subprocess, "Popen") as popen:
            root = Path(temp)
            edge = root / "msedge.exe"
            edge.touch()
            app.launch(app.TARGETS["chatgpt"], edge, 17890, root, stable_profile=True)
            cmd = popen.call_args.args[0]
            self.assertIn(f"--user-data-dir={root / 'Browser' / 'msedge-relay'}", cmd)

        pipe_endpoint = app.Controller(pipe="verge-mihomo", name="Clash Verge Rev")
        raw_http = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"mode\":\"rule\",\"mixed-port\":7897}"
        with patch.object(win32net, "pipe_http_request", return_value=raw_http) as pipe_fn:
            data = app.controller("/configs", pipe_endpoint)
        pipe_fn.assert_called_once()
        self.assertEqual(data["mixed-port"], 7897)

        # live_status attributes connections targeting relay_port to 统一中继
        fake_snap = proxies.DiscoverySnapshot(detected_apps={}, controllers=(), http_proxies=(), controller_sibling_ports={}, hints=())
        with patch.object(app, "discover_live_proxies", return_value=fake_snap), \
                patch.object(app, "process_snapshot", return_value={100: ("Antigravity IDE.exe", 1)}), \
                patch.object(app, "tcp_connections", return_value=[(100, 55000, "127.0.0.1", 17890)]):
            routes, running = app.live_status({}, None, relay_port=17890)
        self.assertIn("ide", running)
        self.assertEqual(routes["ide"], ["统一中继 :17890 · 代理连接中"])

    def test_user_guide_startup_gate_and_top_right_reopen(self):
        import tkinter as tk
        import dialogs
        import interface

        self.assertGreaterEqual(len(dialogs.USER_GUIDE_SECTIONS), 5)
        self.assertTrue(any("右上角" in bullet for _, _, bullets in dialogs.USER_GUIDE_SECTIONS for bullet in bullets))

        with tempfile.TemporaryDirectory() as temp, \
                patch.object(interface, "DATA", Path(temp)), \
                patch.object(interface, "system_proxy_port", return_value=None), \
                patch.object(interface, "detect_paths", return_value={}), \
                patch.object(interface, "detected_proxy_apps", return_value={}):
            try:
                root = self._get_tk_root()
            except tk.TclError:
                self.skipTest("Tk display unavailable")
            win = interface.LauncherWindow(root, enable_tray=False)
            self.assertFalse(win.skip_guide_on_start)
            self.assertTrue(hasattr(win, "guide_button"))

            # Startup gate: does not acknowledge until user clicks "我已阅读"
            acked = []
            dlg = win.open_guide(startup_gate=True, on_acknowledged=lambda: acked.append(True))
            # Calling open_guide again reuses the existing modal instead of duplicating
            self.assertIs(win.open_guide(), dlg)
            self.assertEqual(acked, [])
            dlg.confirm_button._command()
            root.update()
            self.assertEqual(acked, [True])
            self.assertFalse(win.skip_guide_on_start)
            self.assertIsNone(win._guide_dialog)

            # Reopening from top-right button works repeatedly and closes cleanly
            dlg2 = win.open_guide(startup_gate=False)
            self.assertTrue(dlg2.winfo_exists())
            dlg2.confirm_button._command()
            root.update()
            self.assertIsNone(win._guide_dialog)

            # Official bundled PNG icons exist and load cleanly for all built-in + preset apps
            import widgets
            for key in app.ALL_BUILTIN_TARGETS:
                for sz in (40, 50, 60):
                    path = widgets.icon_asset_path(key, sz)
                    self.assertIsNotNone(path, f"Missing icon for {key}@{sz}")
                photo = widgets.load_app_icon(root, key, 40)
                self.assertIsNotNone(photo, f"Failed loading PhotoImage for {key}")

    def test_whitelabel_skeleton_detection_and_dead_sys_proxy_diagnosis(self):
        import win32net

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "baicai"
            folder.mkdir()
            exe = folder / "baicai.exe"
            exe.touch()
            (folder / "FlClashCore.exe").touch()
            (folder / "proxy_plugin.dll").touch()

            with patch.object(proxies, "_WHITELABEL_CACHE", {}), \
                    patch.object(win32net, "pe_display_name", return_value="白菜机场"):
                info = proxies.detect_whitelabel_proxy(exe, is_mac=False)
                self.assertEqual(info, ("白菜机场", "FlClash", ("flclashcore.exe", "flclashcore")))

                # Case 1: baicai.exe running WITHOUT FlClashCore.exe, Windows sys_port=7890 is dead (not in listeners)
                # Must NOT misattribute dead :7890 to Clash Verge Rev and MUST emit precise diagnostic hint!
                procs = {
                    10: ("baicai.exe", 1),
                    20: ("clash-verge.exe", 1),
                    21: ("verge-mihomo.exe", 20),
                }
                listeners = [(10, "127.0.0.1", 13113), (21, "127.0.0.1", 7897)]
                probed_ports: list[int] = []
                with patch.object(proxies, "process_executable_path", side_effect=lambda pid: exe if pid == 10 else None):
                    snap = proxies.discover_proxies(
                        procs, listeners, sys_port=7890, env_ports=[], static_controllers=[],
                        is_mac=False, detected_apps={"Clash Verge Rev": None},
                        probe_controller=lambda port, label: (probed_ports.append(port) or None, False),
                    )
                self.assertIn("白菜机场", snap.detected_apps)
                # Must NEVER probe or select GUI shell random IPC port 13113 (prevents FlClash 'socket done'!)
                self.assertNotIn(13113, probed_ports)
                self.assertFalse(any(h.port in {7890, 13113} for h in snap.http_proxies))
                self.assertTrue(any("白菜机场" in hint and "7890" in hint and "内核未启动" in hint for hint in snap.hints))

                # Case 2: FlClashCore.exe IS running as child of baicai.exe on :7890 -> labeled as 白菜机场!
                procs_running = {**procs, 11: ("FlClashCore.exe", 10)}
                listeners_running = [*listeners, (11, "127.0.0.1", 7890)]
                with patch.object(proxies, "process_executable_path", side_effect=lambda pid: exe if pid == 10 else None):
                    snap2 = proxies.discover_proxies(
                        procs_running, listeners_running, sys_port=7890, env_ports=[], static_controllers=[],
                        is_mac=False, detected_apps={},
                        probe_controller=lambda port, label: (None, False),
                    )
                self.assertTrue(any(h.port == 7890 and h.name == "白菜机场" for h in snap2.http_proxies))

    def test_protocol_first_common_port_discovery_and_wildcard_pipe(self):
        # Unknown renamed proxy NOT setting system proxy, listening on common proxy port 15732
        procs = {50: ("mystery_accel.exe", 1)}
        listeners = [(50, "127.0.0.1", 15732)]
        probed = []

        def fake_ctrl_probe(port, label):
            probed.append((port, label))
            return (app.Controller(port=port, name=label), False) if port == 15732 else (None, False)

        with patch.object(proxies, "scan_whitelabel_processes", return_value={}):
            snap = proxies.discover_proxies(
                procs, listeners, sys_port=None, env_ports=[], static_controllers=[],
                is_mac=False, detected_apps={}, probe_controller=fake_ctrl_probe,
            )
        self.assertTrue(any(p == 15732 for p, _ in probed))
        self.assertTrue(any(c.port == 15732 for c in snap.controllers))

        # Wildcard unknown named pipe (e.g. baicai-mihomo) returning /version JSON is auto-discovered
        with patch.object(proxies, "sys", type("S", (), {"platform": "win32"})()), \
                patch.object(proxies.os, "listdir", return_value=["baicai-mihomo", "baicai-service"]), \
                patch.object(proxies, "probe_pipe_controller", side_effect=lambda p: p == "baicai-mihomo"):
            pipes = proxies.windows_active_proxy_pipes({})
        self.assertEqual([c.pipe for c in pipes], ["baicai-mihomo"])

    def test_pure_tun_portless_fallback_and_closed_port_timeout_fix(self):
        import socket
        import threading

        # 1. TCP connect timeout must classify as 'closed', NEVER 'http_proxy'
        with patch.object(app.socket, "create_connection", side_effect=TimeoutError("SYN timeout")):
            self.assertEqual(app.probe_http_proxy_port(7890, timeout=1.5), "closed")

        # 1b. Silent non-HTTP binary IPC port (like Flutter/FlClash UI IPC :12937) must classify as 'not_proxy'
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        ipc_port = srv.getsockname()[1]
        stop = threading.Event()

        def silent_ipc():
            srv.settimeout(0.2)
            conns = []
            while not stop.is_set():
                try:
                    c, _ = srv.accept()
                    conns.append(c)
                except TimeoutError:
                    continue
                except OSError:
                    break
            for c in conns:
                c.close()

        t = threading.Thread(target=silent_ipc, daemon=True)
        t.start()
        try:
            self.assertEqual(app.probe_http_proxy_port(ipc_port, timeout=0.35), "not_proxy")
        finally:
            stop.set()
            srv.close()
            t.join(timeout=1.0)

        # 2. Pure-TUN mode (port=0) strips proxy env & avoids --proxy-server=:0
        env = app.proxy_env(0, {"HTTP_PROXY": "http://127.0.0.1:9674", "KEEP": "1"})
        self.assertNotIn("HTTP_PROXY", env)
        self.assertEqual(env["KEEP"], "1")
        self.assertEqual(app.chromium_args(0), ["--disable-quic"])

        with tempfile.TemporaryDirectory() as temp, patch.object(app.subprocess, "Popen") as popen:
            root = Path(temp)
            edge = root / "msedge.exe"
            edge.touch()
            app.launch(app.TARGETS["chatgpt"], edge, 0, root)
            cmd = popen.call_args.args[0]
            self.assertIn(f"--user-data-dir={root / 'Browser' / 'msedge-tun'}", cmd)
            self.assertFalse(any(arg.startswith("--proxy-server=") for arg in cmd))

        # 3. check_reports falls back to check_tun_only when OS TUN is active with non-CN exit
        empty_snap = proxies.DiscoverySnapshot(detected_apps={}, controllers=(), http_proxies=(), controller_sibling_ports={}, hints=())
        good_probe = app.Probe("chatgpt.com", True, "HTTP 200", "tun_route", 110, "", "US")
        with patch.object(app, "discover_live_proxies", return_value=empty_snap), \
                patch.object(app, "detect_system_tun", return_value=True), \
                patch.object(app, "_probe_hosts_and_region", return_value=([good_probe], "US", 110)):
            reports, _ = app.check_reports(["chatgpt.com"], True)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].port, 0)
        self.assertTrue(reports[0].tun)

        # Rejects domestic CN direct traffic even if a TUN adapter exists
        with patch.object(app, "_probe_hosts_and_region", return_value=([good_probe], "CN", 20)):
            self.assertIsNone(app.check_tun_only(["chatgpt.com"]))

    def test_relay_socks5_upstream_connect_and_plain_http_bridging(self):
        import socket
        import struct
        import threading
        import relay as relay_mod

        # 1. Local echo target server
        target_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        target_srv.bind(("127.0.0.1", 0))
        target_srv.listen(8)
        target_port = target_srv.getsockname()[1]

        # 2. Local RFC 1928 SOCKS5 mock server redirecting any CONNECT to target_srv
        socks_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        socks_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        socks_srv.bind(("127.0.0.1", 0))
        socks_srv.listen(8)
        socks_port = socks_srv.getsockname()[1]

        stop = threading.Event()
        seen_domains: list[tuple[str, int]] = []

        def run_target():
            target_srv.settimeout(0.2)
            while not stop.is_set():
                try:
                    c, _ = target_srv.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                with c:
                    c.settimeout(2.0)
                    data = c.recv(4096)
                    if data.startswith(b"GET "):
                        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(data)).encode() + b"\r\nConnection: close\r\n\r\n" + data)
                    else:
                        c.sendall(b"ECHO:" + data)

        def handle_socks(client: socket.socket):
            with client:
                client.settimeout(2.0)
                ver_n = client.recv(2)
                if len(ver_n) < 2 or ver_n[0] != 5:
                    return
                client.recv(ver_n[1])
                client.sendall(b"\x05\x00")
                hdr = client.recv(4)
                if len(hdr) < 4 or hdr[1] != 1 or hdr[3] != 3:
                    return
                alen = client.recv(1)[0]
                dom = client.recv(alen).decode("ascii")
                dport = struct.unpack("!H", client.recv(2))[0]
                seen_domains.append((dom, dport))
                up = socket.create_connection(("127.0.0.1", target_port), timeout=2.0)
                with up:
                    client.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
                    client.settimeout(2.0)
                    up.settimeout(2.0)
                    req = client.recv(4096)
                    if req:
                        up.sendall(req)
                        resp = up.recv(4096)
                        if resp:
                            client.sendall(resp)

        def run_socks():
            socks_srv.settimeout(0.2)
            while not stop.is_set():
                try:
                    c, _ = socks_srv.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                threading.Thread(target=handle_socks, args=(c,), daemon=True).start()

        t_target = threading.Thread(target=run_target, daemon=True)
        t_socks = threading.Thread(target=run_socks, daemon=True)
        t_target.start()
        t_socks.start()
        r = relay_mod.LocalProxyRelay(preferred_port=0)
        try:
            rport = r.start()
            r.update_routes(("socks5", socks_port))

            # A. HTTPS CONNECT through HTTP relay -> SOCKS5 upstream
            with socket.create_connection(("127.0.0.1", rport), timeout=2.0) as s:
                s.settimeout(2.0)
                s.sendall(b"CONNECT example.test:443 HTTP/1.1\r\nHost: example.test:443\r\n\r\n")
                status_buf = b""
                while b"\r\n\r\n" not in status_buf:
                    status_buf += s.recv(1024)
                self.assertIn(b"200 Connection Established", status_buf)
                s.sendall(b"PING_SOCKS5")
                self.assertEqual(s.recv(1024), b"ECHO:PING_SOCKS5")

            # B. Plain HTTP absolute-URI request through HTTP relay -> SOCKS5 upstream
            with socket.create_connection(("127.0.0.1", rport), timeout=2.0) as s:
                s.settimeout(2.0)
                s.sendall(
                    b"GET http://plain.test/api?x=1 HTTP/1.1\r\n"
                    b"Host: plain.test\r\nProxy-Connection: keep-alive\r\n\r\n"
                )
                resp = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                self.assertIn(b"HTTP/1.1 200 OK", resp)
                self.assertIn(b"GET /api?x=1 HTTP/1.1", resp)
                self.assertNotIn(b"Proxy-Connection", resp)

            self.assertIn(("example.test", 443), seen_domains)
            self.assertIn(("plain.test", 80), seen_domains)
        finally:
            r.stop()
            stop.set()
            socks_srv.close()
            target_srv.close()
            t_socks.join(timeout=1.0)
            t_target.join(timeout=1.0)

    def test_universal_gui_framework_ipc_protection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fl_dir = root / "flutter_vpn"
            fl_dir.mkdir()
            fl_exe = fl_dir / "mystery_vpn.exe"
            fl_exe.touch()
            (fl_dir / "flutter_windows.dll").touch()

            el_dir = root / "electron_vpn"
            (el_dir / "resources").mkdir(parents=True)
            el_exe = el_dir / "electron_vpn.exe"
            el_exe.touch()
            (el_dir / "resources" / "app.asar").touch()

            ta_dir = root / "tauri_vpn"
            ta_dir.mkdir()
            ta_exe = ta_dir / "tauri_vpn.exe"
            ta_exe.touch()
            (ta_dir / "WebView2Loader.dll").touch()

            core_dir = root / "plain_core"
            core_dir.mkdir()
            core_exe = core_dir / "custom_core.exe"
            core_exe.touch()

            self.assertTrue(proxies.is_gui_framework_executable(fl_exe, is_mac=False))
            self.assertTrue(proxies.is_gui_framework_executable(el_exe, is_mac=False))
            self.assertTrue(proxies.is_gui_framework_executable(ta_exe, is_mac=False))
            self.assertFalse(proxies.is_gui_framework_executable(core_exe, is_mac=False))

            # Unknown Flutter GUI owns ephemeral IPC port 41234 AND common proxy port 7890
            procs = {77: ("mystery_vpn.exe", 1)}
            listeners = [(77, "127.0.0.1", 41234), (77, "127.0.0.1", 7890)]
            probed_ports: list[int] = []

            def record_probe(port, label):
                probed_ports.append(port)
                return (None, False)

            with patch.object(proxies, "scan_whitelabel_processes", return_value={}), \
                    patch.object(proxies, "process_executable_path", return_value=fl_exe), \
                    patch.object(proxies, "windows_active_proxy_pipes", return_value=[]), \
                    patch.object(proxies, "windows_existing_pipes", return_value=set()), \
                    patch.object(proxies, "discover_flclash_family_configs", return_value=[]):
                snap = proxies.discover_proxies(
                    procs, listeners, sys_port=None, env_ports=[], static_controllers=[],
                    is_mac=False, detected_apps={}, probe_controller=record_probe,
                )
            self.assertNotIn(41234, probed_ports)
            self.assertNotIn(41234, [c.port for c in snap.http_proxies])
            self.assertIn(7890, probed_ports)
            self.assertIn(7890, [c.port for c in snap.http_proxies])

    def test_custom_named_tun_route_and_transparent_router_proxy_fallback(self):
        # Unknown VPN adapter name NOT matching TUN_ADAPTER_PATTERN, but owning a lower-metric CGNAT default route
        custom_vpn_route = """
Interface List
  4...........................MyCustomVPN Adapter
 19...00 e0 4c a8 0a 19 ......Realtek PCIe GbE Family Controller
Active Routes:
          0.0.0.0          0.0.0.0      192.168.1.1      192.168.1.50     35
          0.0.0.0          0.0.0.0       100.64.0.2       100.64.0.1       5
"""
        dual_lan_only = """
Interface List
 19...00 e0 4c a8 0a 19 ......Realtek PCIe GbE Family Controller
 20...00 e0 4c a8 0a 20 ......Intel Wi-Fi 6 AX201
Active Routes:
          0.0.0.0          0.0.0.0      192.168.1.1      192.168.1.50     35
          0.0.0.0          0.0.0.0      192.168.2.1      192.168.2.50     25
"""
        self.assertTrue(proxies.parse_windows_tun_routes(custom_vpn_route))
        self.assertFalse(proxies.parse_windows_tun_routes(dual_lan_only))

        # Transparent router / 旁路由 fallback when detect_system_tun() is False and 0 local proxies exist
        empty_snap = proxies.DiscoverySnapshot(detected_apps={}, controllers=(), http_proxies=(), controller_sibling_ports={}, hints=())
        good_probe = app.Probe("chatgpt.com", True, "HTTP 200", "tun_route", 90, "", "US")
        with patch.object(app, "discover_live_proxies", return_value=empty_snap), \
                patch.object(app, "detect_system_tun", return_value=False), \
                patch.object(proxies, "probe_exit_region", return_value=("US", 80)), \
                patch.object(app, "_probe_hosts_and_region", return_value=([good_probe], "US", 90)):
            reports, _ = app.check_reports(["chatgpt.com"], True)
        self.assertEqual(len(reports), 1)
        self.assertEqual((reports[0].provider, reports[0].port, reports[0].tun, reports[0].scheme), ("透明代理 / 旁路由", 0, True, "tun"))

        # Must NOT false-positive when direct exit is CN or empty
        for direct_reg in ("CN", ""):
            with patch.object(app, "discover_live_proxies", return_value=empty_snap), \
                    patch.object(app, "detect_system_tun", return_value=False), \
                    patch.object(proxies, "probe_exit_region", return_value=(direct_reg, 20)):
                reports, _ = app.check_reports(["chatgpt.com"], False)
            self.assertEqual(reports, [])

    def test_socks5_only_client_auto_verified_and_manual_socks5_fallback(self):
        snap = proxies.DiscoverySnapshot(
            controllers=[],
            http_proxies=[proxies.HTTPProxyCandidate(1080, "Shadowsocks", "process", 10)],
            detected_apps={"Shadowsocks": None},
            hints=[],
        )
        ok_probe = app.Probe("chatgpt.com", True, "HTTP 200", "socks5_proxy", 95, "", "JP")
        hints: list[str] = []
        with patch.object(app, "discover_live_proxies", return_value=snap), \
                patch.object(app, "probe_http_proxy_port", return_value="socks5"), \
                patch.object(app, "_probe_hosts_and_region", return_value=([ok_probe], "JP", 95)):
            reports, _ = app.check_reports(["chatgpt.com"], False, hints=hints)
        self.assertEqual(len(reports), 1)
        self.assertEqual((reports[0].provider, reports[0].port, reports[0].scheme), ("Shadowsocks", 1080, "socks5"))
        self.assertTrue(any("桥接" in h for h in hints))

        # Manual port mode falls back to SOCKS5 when HTTP probe fails and port speaks SOCKS5
        fail_http = app.Report("http", False, {"chatgpt.com": app.Probe("chatgpt.com", False, "中断")}, 1080, provider="手动端口 :1080")
        ok_socks = app.Report("socks5", False, {"chatgpt.com": ok_probe}, 1080, provider="手动端口 :1080", region="JP", scheme="socks5")
        with patch.object(app, "detect_system_tun", return_value=False), \
                patch.object(app, "check_http_proxy", return_value=fail_http), \
                patch.object(proxies, "probe_socks5_port", return_value=True), \
                patch.object(app, "check_socks5_proxy", return_value=ok_socks):
            manual_rep = app.check(1080, ["chatgpt.com"], app.MANUAL)
        self.assertEqual((manual_rep.scheme, manual_rep.port), ("socks5", 1080))

    def test_universal_flclash_family_passive_config_discovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "com.follow"
            brand_dir = root / "任意自定义机场"
            brand_dir.mkdir(parents=True)
            inner = json.dumps({"patchClashConfig": {"mixed-port": 18899, "tun": {"enable": True}}})
            (brand_dir / "shared_preferences.json").write_text(
                json.dumps({"flutter.config": inner}), encoding="utf-8",
            )
            found = proxies.discover_flclash_family_configs(is_mac=False, base_dirs=(root,))
            self.assertEqual(found, [("任意自定义机场", 18899, True)])

            # Only added to discover_proxies when the configured port is genuinely listening
            with patch.object(proxies, "scan_whitelabel_processes", return_value={}), \
                    patch.object(proxies, "windows_active_proxy_pipes", return_value=[]), \
                    patch.object(proxies, "windows_existing_pipes", return_value=set()), \
                    patch.object(proxies, "discover_flclash_family_configs", return_value=found):
                dead_snap = proxies.discover_proxies(
                    {99: ("renamed_core.exe", 1)}, [], sys_port=None, env_ports=[], static_controllers=[],
                    is_mac=False, detected_apps={}, probe_controller=lambda p, n: (None, False),
                )
                live_snap = proxies.discover_proxies(
                    {99: ("renamed_core.exe", 1)}, [(99, "127.0.0.1", 18899)], sys_port=None, env_ports=[], static_controllers=[],
                    is_mac=False, detected_apps={}, probe_controller=lambda p, n: (None, False),
                )
            self.assertFalse(any(c.port == 18899 for c in dead_snap.http_proxies))
            self.assertTrue(any(c.port == 18899 and c.name == "任意自定义机场" for c in live_snap.http_proxies))

    def test_rollback_restores_backups_created_files_winenv_autostart_and_runtime_state(self):
        import envcheck
        envcheck.clear_runtime_changes()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home = root / "home"
                data_dir = root / "data"
                home.mkdir()
                data_dir.mkdir()

                # 1. Existing files modified by envcheck -> restore from .bak
                gitconfig = home / ".gitconfig"
                orig_git = "[http]\n\tproxy = http://127.0.0.1:9674\n"
                gitconfig.write_text(orig_git, encoding="utf-8")
                npmrc = home / ".npmrc"
                orig_npm = "https-proxy=http://127.0.0.1:9674\n"
                npmrc.write_text(orig_npm, encoding="utf-8")

                conflicts = envcheck.audit_environment(
                    [7897], 7897, home=home, codex_env=home / ".codex" / ".env",
                    user_env={}, process_env={}, port_checker=lambda p: p == 7897,
                )
                for item in conflicts:
                    envcheck.fix_conflict(item, data_dir=data_dir)
                self.assertIn("7897", gitconfig.read_text(encoding="utf-8"))
                self.assertIn("7897", npmrc.read_text(encoding="utf-8"))

                # 2. Newly created .codex/.env (did not exist before) -> deleted on rollback
                codex_env = home / ".codex" / ".env"
                with patch.object(app, "codex_dotenv", return_value=codex_env), \
                        patch.object(app, "DATA", data_dir):
                    app.with_codex_proxy(7897, lambda: None)
                self.assertTrue(codex_env.is_file())
                self.assertFalse((codex_env.parent / ".env.ai-proxy-launcher.bak").exists())

                # 3. Recorded Windows user env var (first-write-wins preserves original :9674)
                envcheck.record_winenv_change(
                    "HTTPS_PROXY", existed_before=True,
                    previous_value="http://127.0.0.1:9674", new_value="http://127.0.0.1:7897",
                    data_dir=data_dir,
                )
                envcheck.record_winenv_change(
                    "HTTPS_PROXY", existed_before=True,
                    previous_value="http://127.0.0.1:7897", new_value="http://127.0.0.1:17890",
                    data_dir=data_dir,
                )

                # 4. Session runtime changes (first-write-wins for node & TUN)
                ctrl = app.Controller(port=9090, name="Clash Verge Rev")
                envcheck.record_runtime_node_change(ctrl, "OpenAI", "US-01", "JP-01")
                envcheck.record_runtime_node_change(ctrl, "OpenAI", "JP-01", "SG-01")
                envcheck.record_runtime_tun_change(ctrl, False, True)

                items = envcheck.list_rollback_items(
                    data_dir=data_dir, home=home, codex_env=codex_env,
                    include_runtime=True, autostart_checker=lambda: True,
                )
                cats = {it.category for it in items}
                self.assertEqual(cats, {"file", "winenv", "autostart", "runtime"})
                self.assertEqual(len(items), 7)  # 3 files + 1 winenv + 1 autostart + 2 runtime

                restored_env = {}
                disabled_autostart = []
                switched_nodes = []
                toggled_tun = []

                done, errors = envcheck.rollback_all_external(
                    data_dir=data_dir, home=home, codex_env=codex_env,
                    include_runtime=True,
                    autostart_checker=lambda: True,
                    autostart_disabler=lambda: disabled_autostart.append(True),
                    winenv_writer=lambda k, v: restored_env.__setitem__(k, v),
                    node_switcher=lambda ep, g, n: switched_nodes.append((ep.name, g, n)),
                    tun_setter=lambda ep, en: toggled_tun.append((ep.name, en)),
                )
                self.assertEqual(errors, [])
                self.assertEqual(len(done), 7)
                self.assertEqual(gitconfig.read_text(encoding="utf-8"), orig_git)
                self.assertEqual(npmrc.read_text(encoding="utf-8"), orig_npm)
                self.assertFalse((home / ".gitconfig.ai-proxy-launcher.bak").exists())
                self.assertFalse(codex_env.exists())
                self.assertEqual(restored_env, {"HTTPS_PROXY": "http://127.0.0.1:9674"})
                self.assertEqual(disabled_autostart, [True])
                self.assertEqual(switched_nodes, [("Clash Verge Rev", "OpenAI", "US-01")])
                self.assertEqual(toggled_tun, [("Clash Verge Rev", False)])

                # After rollback, list_rollback_items is clean
                remaining = envcheck.list_rollback_items(
                    data_dir=data_dir, home=home, codex_env=codex_env,
                    include_runtime=True, autostart_checker=lambda: False,
                )
                self.assertEqual(remaining, [])

                # 5. Reset app own data (settings.json + optional Browser/)
                (data_dir / "settings.json").write_text("{}", encoding="utf-8")
                (data_dir / "Browser" / "edge-relay").mkdir(parents=True)
                (data_dir / "Browser" / "edge-relay" / "Cookies").write_text("x", encoding="utf-8")
                reset_msgs = envcheck.reset_app_data(data_dir, clear_browser=True)
                self.assertEqual(len(reset_msgs), 2)
                self.assertFalse((data_dir / "settings.json").exists())
                self.assertFalse((data_dir / "Browser").exists())
        finally:
            envcheck.clear_runtime_changes()

    def test_rollback_dialog_opens_and_reverts_items(self):
        import tkinter as tk
        import envcheck
        import interface

        envcheck.clear_runtime_changes()
        with tempfile.TemporaryDirectory() as temp:
            root_dir = Path(temp)
            data_dir = root_dir / "data"
            data_dir.mkdir()
            sample = root_dir / ".npmrc"
            sample.write_text("https-proxy=http://127.0.0.1:7897\n", encoding="utf-8")
            bak = root_dir / ".npmrc.ai-proxy-launcher.bak"
            bak.write_text("https-proxy=http://127.0.0.1:9674\n", encoding="utf-8")
            envcheck.record_file_change(
                sample, source="npm 配置 (.npmrc)", existed_before=True, backup_path=bak, data_dir=data_dir,
            )

            with patch.object(interface, "DATA", data_dir), \
                    patch.object(interface, "system_proxy_port", return_value=None), \
                    patch.object(interface, "detect_paths", return_value={}), \
                    patch.object(interface, "detected_proxy_apps", return_value={}):
                try:
                    tk_root = self._get_tk_root()
                except tk.TclError:
                    self.skipTest("Tk display unavailable")
                win = interface.LauncherWindow(tk_root, enable_tray=False)
                self.assertTrue(hasattr(win, "rollback_button"))
                dlg = win.open_rollback()
                self.assertTrue(dlg.winfo_exists())
                dlg.undo_all_button._command()
                self.assertEqual(sample.read_text(encoding="utf-8"), "https-proxy=http://127.0.0.1:9674\n")
                self.assertFalse(bak.exists())
                try:
                    dlg.grab_release()
                except tk.TclError:
                    pass
                dlg.destroy()
                tk_root.update_idletasks()
                # Also verify reset_software_state cleanly rebuilds hidden/preset targets without KeyError
                win.hidden_targets.add("claude")
                win.enabled_presets = ["cursor"]
                win.targets = app.build_active_targets({}, win.enabled_presets, win.hidden_targets)
                win.settings.write_text("{}", encoding="utf-8")
                win.reset_software_state(clear_browser=False)
                self.assertIn("claude", win.targets)
                self.assertIn("claude", win.details)
                self.assertIn("claude", win.selected)
                self.assertNotIn("cursor", win.targets)
                self.assertTrue(win._skip_save_on_exit)
                self.assertFalse(win.settings.exists())

    def test_prepare_tun_failure_rollback_leaves_no_phantom_runtime_entry_and_prunes_stale_journal(self):
        import envcheck
        import repair

        envcheck.clear_runtime_changes()
        try:
            endpoint = app.Controller(port=9090, name="Clash Verge Rev")
            state = {"tun": False}

            def fake_controller(path, ep):
                return {"tun": {"enable": state["tun"]}}

            def fake_request(path, ep, method="GET", body=None, timeout=5):
                if method == "PATCH" and isinstance(body, dict) and "tun" in body:
                    state["tun"] = bool(body["tun"]["enable"])
                return {}

            with patch.object(repair, "controller", side_effect=fake_controller), \
                    patch.object(repair, "controller_request", side_effect=fake_request):
                with self.assertRaises(ValueError):
                    repair.prepare_tun(endpoint, None, lambda _ep: (_ for _ in ()).throw(ValueError("probe failed")))

            self.assertFalse(state["tun"])
            runtime_items = [
                it for it in envcheck.list_rollback_items(include_runtime=True, autostart_checker=lambda: False)
                if it.category == "runtime"
            ]
            self.assertEqual(runtime_items, [])

            # Stale journal entry whose file & backup no longer exist is automatically pruned
            with tempfile.TemporaryDirectory() as temp:
                data_dir = Path(temp)
                ghost = data_dir / "ghost.env"
                envcheck.record_file_change(ghost, source="Ghost", existed_before=False, data_dir=data_dir)
                self.assertTrue((data_dir / envcheck.JOURNAL_FILENAME).is_file())
                items = envcheck.list_rollback_items(data_dir=data_dir, home=data_dir, include_runtime=False, autostart_checker=lambda: False)
                self.assertEqual(items, [])
                self.assertFalse((data_dir / envcheck.JOURNAL_FILENAME).exists())
        finally:
            envcheck.clear_runtime_changes()


if __name__ == "__main__":
    unittest.main()

