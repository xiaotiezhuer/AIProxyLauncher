import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import envcheck
import launcher
import linux
import proxies
import repair
import win32net


class LinuxTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(launcher, "IS_MAC", False))
        self.enterContext(patch.object(launcher, "IS_LINUX", True))
        self.enterContext(patch.object(repair, "IS_MAC", False))
        self.enterContext(patch.object(repair, "IS_LINUX", True))

    def test_desktop_entry_parsing_exec_extraction_and_display_name(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            exe = root / "baicai-linux"
            exe.touch()
            desktop_dir = root / "applications"
            desktop_dir.mkdir()
            (desktop_dir / "baicai.desktop").write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=白菜机场\n"
                f'Exec=env GDK_BACKEND=x11 "{exe}" %U\n'
                "[Desktop Action Quit]\n"
                "Name=Quit\n",
                encoding="utf-8",
            )
            (desktop_dir / "hidden.desktop").write_text(
                "[Desktop Entry]\nName=HiddenApp\nHidden=true\nExec=/bin/true\n",
                encoding="utf-8",
            )
            self.assertEqual(linux.find_desktop_app(("白菜机场",), dirs=(desktop_dir,)), exe)
            self.assertIsNone(linux.find_desktop_app(("hiddenapp",), dirs=(desktop_dir,)))
            self.assertEqual(linux.desktop_display_name(exe, dirs=(desktop_dir,)), "白菜机场")

    def test_linux_browser_and_web_launch_injects_upper_and_lower_proxy_env(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            browser = root / "google-chrome-stable"
            browser.touch()
            self.assertTrue(linux.is_supported_browser(browser))
            self.assertEqual(linux.browser_display_name(browser), "Google Chrome")

            with patch.object(linux.subprocess, "Popen") as popen:
                linux.launch(launcher.TARGETS["chatgpt"], browser, 7897, root, False)
            cmd = popen.call_args.args[0]
            env = popen.call_args.kwargs["env"]
            self.assertEqual(cmd[0], str(browser))
            self.assertIn("--proxy-server=http://127.0.0.1:7897", cmd)
            self.assertIn(f"--user-data-dir={root / 'Browser' / 'google-chrome-stable-7897'}", cmd)
            self.assertIn("--app=https://chatgpt.com/", cmd)
            self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7897")
            self.assertEqual(env["https_proxy"], "http://127.0.0.1:7897")
            self.assertEqual(env["http_proxy"], "http://127.0.0.1:7897")
            self.assertEqual(env["no_proxy"], "localhost,127.0.0.1,::1")

            with patch.object(linux.subprocess, "Popen") as popen:
                linux.launch(launcher.TARGETS["chatgpt"], browser, 17890, root, False, stable_profile=True)
            self.assertIn(f"--user-data-dir={root / 'Browser' / 'google-chrome-stable-relay'}", popen.call_args.args[0])

    def test_linux_cli_launch_uses_terminal_wrapper_script(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cli = root / "claude"
            cli.touch()
            with patch.object(linux, "find_terminal", return_value=("/usr/bin/kitty", ("--",))), \
                    patch.object(linux.subprocess, "Popen") as popen:
                linux.launch(launcher.TARGETS["code"], cli, 7897, root, False)
            cmd = popen.call_args.args[0]
            self.assertEqual(cmd[:2], ["/usr/bin/kitty", "--"])
            script = Path(cmd[2])
            self.assertTrue(script.is_file())
            content = script.read_text(encoding="utf-8")
            self.assertIn("HTTPS_PROXY=http://127.0.0.1:7897", content)
            self.assertIn("https_proxy=http://127.0.0.1:7897", content)
            self.assertIn(str(cli), content)

            with patch.object(linux, "find_terminal", return_value=None):
                with self.assertRaisesRegex(ValueError, "终端"):
                    linux.launch(launcher.TARGETS["code"], cli, 7897, root, False)

    def test_linux_proc_stat_and_proc_net_tcp_parsing(self):
        self.assertEqual(linux.parse_proc_stat("4321 (Web Content (2)) S 1000 4321 1000 0 -1"), ("Web Content (2)", 1000))
        self.assertEqual(linux.parse_proc_stat("77 (clash-verge) S 1 77 77 0 -1"), ("clash-verge", 1))

        proc_tcp4 = (
            "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 0100007F:1ED9 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 10001\n"
            "   1: 0100007F:A8C0 0100007F:1ED9 01 00000000:00000000 00:00000000 00000000  1000        0 10002\n"
            "   2: 0A00000A:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 10003\n"
        )
        est4, lis4 = linux.parse_proc_net_tcp(proc_tcp4, ipv6=False, inode_to_pid={"10002": 500}, byteorder="little")
        # Root listener 127.0.0.1:7897 (0x1ED9) has pid=0; non-loopback 10.0.0.10:8080 is excluded
        self.assertEqual(lis4, [(0, "127.0.0.1", 7897)])
        self.assertEqual(est4, [(500, 0xA8C0, "127.0.0.1", 7897)])

        proc_tcp6 = (
            "  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 00000000000000000000000001000000:2389 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 20001\n"
            "   1: 0000000000000000FFFF00000100007F:B001 0000000000000000FFFF0000140012C6:01BB 01 00000000:00000000 00:00000000 00000000  1000        0 20002\n"
        )
        est6, lis6 = linux.parse_proc_net_tcp(proc_tcp6, ipv6=True, inode_to_pid={"20001": 600, "20002": 601}, byteorder="little")
        self.assertEqual(lis6, [(600, "[::1]", 9097)])
        # IPv4-mapped 198.18.0.20 (0x140012C6 little-endian) normalizes to plain IPv4 for Fake-IP route matching
        self.assertEqual(est6, [(601, 0xB001, "198.18.0.20", 443)])

    def test_linux_tun_route_detection_handles_policy_tables_custom_sysfs_and_docker(self):
        lan_and_docker = (
            "default via 192.168.1.1 dev eth0 proto dhcp metric 100\n"
            "172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1\n"
            "10.0.0.0/24 dev br-123456 proto kernel scope link\n"
        )
        self.assertFalse(linux.parse_linux_ip_routes(lan_and_docker, set()))

        mihomo_policy = (
            "default via 192.168.1.1 dev eth0\n"
            "default dev Meta table 2022 scope link\n"
            "198.18.0.0/15 dev Meta scope link\n"
        )
        self.assertTrue(linux.parse_linux_ip_routes(mihomo_policy, set()))

        split_tun = (
            "0.0.0.0/1 dev tun0 scope link\n"
            "128.0.0.0/1 dev tun0 scope link\n"
        )
        self.assertTrue(linux.parse_linux_ip_routes(split_tun, set()))

        custom_tun = "default dev acme0 table 100 scope link\n"
        self.assertFalse(linux.parse_linux_ip_routes(custom_tun, set()))
        self.assertTrue(linux.parse_linux_ip_routes(custom_tun, {"acme0"}))

        proc_route_tun = (
            "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
            "tun0\t00000000\t00000000\t0001\t0\t0\t0\t00000000\t0\t0\t0\n"
        )
        self.assertTrue(linux.parse_proc_net_route(proc_route_tun, set()))

    def test_linux_gnome_and_kde_system_proxy_detection(self):
        kde_space = "[Proxy Settings]\nProxyType=1\nhttpsProxy=http://127.0.0.1 7890\n"
        kde_colon = "[Proxy Settings]\nProxyType=1\nhttpProxy=http://127.0.0.1:10808\n"
        kde_off = "[Proxy Settings]\nProxyType=0\nhttpsProxy=http://127.0.0.1:7890\n"
        self.assertEqual(linux.parse_kde_kioslaverc(kde_space), 7890)
        self.assertEqual(linux.parse_kde_kioslaverc(kde_colon), 10808)
        self.assertIsNone(linux.parse_kde_kioslaverc(kde_off))

        def fake_gsettings(cmd, **_kw):
            key = (cmd[2], cmd[3])
            return {
                ("org.gnome.system.proxy", "mode"): "'manual'\n",
                ("org.gnome.system.proxy.https", "host"): "'127.0.0.1'\n",
                ("org.gnome.system.proxy.https", "port"): "7897\n",
            }[key]

        with patch.object(linux.shutil, "which", return_value="/usr/bin/gsettings"), \
                patch.object(linux.subprocess, "check_output", side_effect=fake_gsettings):
            self.assertEqual(linux._gnome_system_proxy_port(), 7897)

    def test_linux_unix_controller_and_root_systemd_proxy_discovery(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = Path(temp) / "clash-verge.yaml"
            cfg.write_text(
                'external-controller-unix: "/tmp/verge/verge-mihomo.sock"\n'
                'external-controller-pipe: "verge-mihomo"\n'
                'external-controller: "127.0.0.1:9097"\n'
                'secret: "linux-secret"\n',
                encoding="utf-8",
            )
            pipes, http = proxies.parse_yaml_controller_file(cfg, "Clash Verge Rev", is_linux=True)
            self.assertEqual(pipes, [launcher.Controller(unix="/tmp/verge/verge-mihomo.sock", name="Clash Verge Rev")])
            self.assertEqual(http, [launcher.Controller(port=9097, secret="linux-secret", name="Clash Verge Rev")])

        # Root-owned systemd core (pid=0 in /proc/net/tcp) paired with visible process name verge-mihomo
        procs = {100: ("clash-verge", 1), 101: ("verge-mihomo", 100)}
        listeners = [(0, "127.0.0.1", 7897), (0, "127.0.0.1", 9097)]
        with patch.object(proxies, "scan_whitelabel_processes", return_value={}), \
                patch.object(proxies, "discover_flclash_family_configs", return_value=[]):
            snap = proxies.discover_proxies(
                procs, listeners, sys_port=7897, env_ports=[], static_controllers=[],
                is_linux=True, detected_apps={"Clash Verge Rev": None},
                probe_controller=lambda port, label: (launcher.Controller(port=port, name=label), False) if port == 9097 else (None, False),
            )
        self.assertIn(launcher.Controller(port=9097, name="Clash Verge Rev"), snap.controllers)
        self.assertTrue(any(c.port == 7897 and c.name == "Clash Verge Rev" for c in snap.http_proxies))
        self.assertFalse(any("无人监听" in h or "没有任何进程监听" in h for h in snap.hints))

        # Linux-native proxy process identification (v2rayA, dae)
        self.assertEqual(proxies.identify_process_proxy(10, {10: ("v2raya", 1)}), "v2rayA")
        self.assertEqual(proxies.identify_process_proxy(11, {11: ("daed", 1)}), "dae")

    def test_linux_whitelabel_gui_ipc_guard_and_flclash_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app_dir = root / "opt" / "myvpn"
            lib_dir = app_dir / "lib"
            lib_dir.mkdir(parents=True)
            exe = app_dir / "myvpn"
            exe.touch()
            (lib_dir / "libflutter_linux_gtk.so").touch()
            (lib_dir / "libproxy_plugin.so").touch()

            with patch.object(proxies, "_WHITELABEL_CACHE", {}), \
                    patch.object(proxies, "_GUI_SHELL_CACHE", {}), \
                    patch.object(linux, "desktop_display_name", return_value="MyLinuxVPN"):
                self.assertTrue(proxies.is_gui_framework_executable(exe, is_linux=True))
                info = proxies.detect_whitelabel_proxy(exe, is_linux=True)
                self.assertEqual(info, ("MyLinuxVPN", "FlClash", ("flclashcore.exe", "flclashcore")))

            pref_root = root / "com.follow.clash"
            pref_root.mkdir()
            inner = json.dumps({"patchClashConfig": {"mixed-port": 17899, "tun": {"enable": True}}})
            (pref_root / "shared_preferences.json").write_text(json.dumps({"flutter.config": inner}), encoding="utf-8")
            self.assertEqual(
                proxies.discover_flclash_family_configs(is_linux=True, base_dirs=(pref_root,)),
                [("FlClash", 17899, True)],
            )

    def test_linux_tun_failure_uses_linux_service_hint_without_windows_setup(self):
        endpoint = launcher.Controller(name="Clash Verge Rev", unix="/tmp/verge/verge-mihomo.sock")
        probes = {host: launcher.Probe(host, True, "", "proxy") for host in launcher.GOOGLE}
        report = launcher.Report("rule", False, probes, 7897, provider=endpoint.name, endpoint=endpoint)
        with patch.object(repair, "check", return_value=report), \
                patch.object(repair, "system_proxy_port", return_value=7897), \
                patch.object(repair, "proxy_controllers", return_value=[]), \
                patch.object(repair, "prepare_tun", side_effect=ValueError("tun permission denied")), \
                patch.object(repair, "setup_clash_service") as win_setup, \
                patch.object(linux.shutil, "which", return_value="/usr/bin/systemctl"), \
                patch.object(linux.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "active\n", "")):
            reports, _, _, notes = repair.prepare_apps(["ide"], None, "Clash", None, False, True, lambda _: None)
        win_setup.assert_not_called()
        self.assertFalse(reports[0].tun)
        self.assertIn("systemd 服务正在运行", notes[-1])

    def test_linux_xdg_autostart_and_rollback_item_location(self):
        with tempfile.TemporaryDirectory() as temp:
            desktop = Path(temp) / "autostart" / "aiproxylauncher.desktop"
            fake_sys = type("S", (), {"platform": "linux", "executable": "/usr/bin/python3", "frozen": False})()
            with patch.object(win32net, "LINUX_AUTOSTART", desktop), \
                    patch.object(win32net, "sys", fake_sys), \
                    patch.object(envcheck, "sys", fake_sys):
                self.assertFalse(win32net.autostart_enabled())
                win32net.set_autostart(True, Path("/opt/AIProxyLauncher/launcher.py"))
                self.assertTrue(win32net.autostart_enabled())
                body = desktop.read_text(encoding="utf-8")
                self.assertIn("[Desktop Entry]", body)
                self.assertIn("--minimized", body)
                items = envcheck.list_rollback_items(
                    data_dir=Path(temp), home=Path(temp), include_runtime=False,
                    autostart_checker=win32net.autostart_enabled,
                )
                self.assertEqual(len(items), 1)
                self.assertIn(".config/autostart/aiproxylauncher.desktop", items[0].detail)
                win32net.set_autostart(False, Path("/opt/AIProxyLauncher/launcher.py"))
                self.assertFalse(win32net.autostart_enabled())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Live Linux /proc & CA check runs on Linux only")
    def test_linux_live_proc_inspection_and_system_ca_bundle(self):
        procs = linux.process_snapshot()
        self.assertIn(os.getpid(), procs)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            listeners = linux.listening_tcp_ports()
            self.assertTrue(any(pid == os.getpid() and p == port for pid, _ip, p in listeners))
        proxies._SSL_CA_SOURCE = None
        ctx = proxies.create_ssl_context()
        self.assertGreaterEqual(ctx.cert_store_stats().get("x509_ca", 0), 10)


if __name__ == "__main__":
    unittest.main()
