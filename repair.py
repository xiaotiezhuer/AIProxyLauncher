from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import quote, urlencode

from launcher import (
    IS_MAC, JIMMM_CONTROLLER, TARGETS,
    WINDOWS, NO_WINDOW, Controller, Report, Target, app_report, block_reason, check, check_reports,
    clash_controllers, connect_probe, controller, controller_request, detected_proxy_apps,
    process_snapshot, proxy_controllers, requires_tun, system_proxy_port,
)
import envcheck
import macos
import proxies

CLASH_SERVICE = "clash_verge_service"
NON_NODE_TYPES = {None, "Direct", "Reject", "RejectDrop", "Pass", "Compatible", "Dns"}
AI_GROUP_KEYWORDS = (
    "ai", "openai", "chatgpt", "claude", "anthropic", "google", "gemini",
    "copilot", "cursor", "节点", "proxy", "代理", "国外", "全球", "选择", "select",
)


def tun_enabled(endpoint: Controller) -> bool | None:
    enabled = (controller("/configs", endpoint).get("tun") or {}).get("enable")
    return enabled if isinstance(enabled, bool) else None


def set_tun(endpoint: Controller, enabled: bool, *, record: bool = True) -> None:
    prev = tun_enabled(endpoint) if record else None
    patch = {"enable": enabled}
    if enabled:
        patch.update({"auto-route": True, "auto-detect-interface": True})
        if IS_MAC and endpoint.name == "Clash Verge Rev":
            device = (controller("/configs", endpoint).get("tun") or {}).get("device", "")
            if not str(device).startswith("utun"):
                patch["device"] = "utun"
    controller_request("/configs", endpoint, "PATCH", {"tun": patch}, timeout=55)
    if tun_enabled(endpoint) is not enabled:
        raise ValueError("系统未能启动虚拟网卡" if enabled else "无法关闭另一款代理的虚拟网卡")
    if record and isinstance(prev, bool) and prev != enabled:
        envcheck.record_runtime_tun_change(endpoint, prev, enabled)


def prepare_tun(endpoint: Controller, other: Controller | None, validate) -> bool:
    was_enabled = tun_enabled(endpoint)
    other_enabled = tun_enabled(other) if other else None
    if was_enabled is None:
        raise ValueError("当前代理的 TUN 状态无法读取")
    switched_other = added_tun = False
    try:
        if other and other != endpoint and other_enabled is True:
            switched_other = True
            set_tun(other, False)
        if not was_enabled:
            added_tun = True
            set_tun(endpoint, True)
        validate(endpoint)
    except (OSError, ValueError) as error:
        failures = []
        for changed, control, enabled in [(added_tun, endpoint, False), (switched_other, other, True)]:
            if changed and control:
                try:
                    set_tun(control, enabled)
                except (OSError, ValueError) as rollback_error:
                    failures.append(str(rollback_error))
        if failures:
            raise ValueError(f"{error}。恢复原虚拟网卡失败 · {' / '.join(failures)}") from error
        raise
    return added_tun or switched_other


def validate_tun(endpoint: Controller, services: list[str]) -> None:
    with ThreadPoolExecutor(max_workers=min(8, len(services))) as pool:
        probes = list(pool.map(lambda service: connect_probe(service, None, endpoint=endpoint), services))
    reason = block_reason(probes, True, tun_enabled(endpoint), endpoint.name or "代理软件")
    if reason:
        raise ValueError(f"TUN 实际线路未通过 · {reason}")


def validate_system_tun(services: list[str], provider: str) -> None:
    with ThreadPoolExecutor(max_workers=min(8, len(services))) as pool:
        probes = list(pool.map(lambda service: connect_probe(service, None, allow_uncontrolled=True), services))
    reason = block_reason(probes, True, True, provider)
    if reason:
        raise ValueError(f"系统 TUN 实际线路未通过 · {reason}")


def list_selector_groups(endpoint: Controller) -> list[dict]:
    """List manual Selector proxy groups from a Clash/Mihomo/Sing-box controller."""
    proxies_data = controller("/proxies", endpoint).get("proxies") or {}
    groups: list[dict] = []
    for name, info in proxies_data.items():
        if not isinstance(info, dict) or info.get("type") != "Selector":
            continue
        all_nodes = [n for n in (info.get("all") or ()) if isinstance(n, str)]
        if not all_nodes:
            continue
        nodes = []
        for node_name in all_nodes:
            meta = proxies_data.get(node_name) or {}
            history = meta.get("history") or []
            last_delay = history[-1].get("delay") if history and isinstance(history[-1], dict) else None
            if not isinstance(last_delay, int) or last_delay <= 0:
                last_delay = None
            nodes.append({
                "name": node_name,
                "type": str(meta.get("type") or ""),
                "alive": meta.get("alive"),
                "delay": last_delay,
                "restricted": proxies.node_looks_restricted(node_name),
            })
        folded = name.casefold()
        priority = 0 if any(tok in folded for tok in AI_GROUP_KEYWORDS) else 1
        groups.append({
            "name": name,
            "now": str(info.get("now") or ""),
            "nodes": nodes,
            "priority": priority,
        })
    groups.sort(key=lambda g: (g["priority"], g["name"].casefold()))
    return groups


def test_group_delays(endpoint: Controller, node_names: list[str],
                      url: str = "https://www.gstatic.com/generate_204", timeout_ms: int = 3500) -> dict[str, int | None]:
    """Concurrently measure latency (ms) for candidate nodes through the controller API."""
    query = urlencode({"url": url, "timeout": timeout_ms})

    def measure(name: str) -> tuple[str, int | None]:
        data = controller_request(f"/proxies/{quote(name, safe='')}/delay?{query}", endpoint, "GET", None, 6)
        delay = data.get("delay") if isinstance(data, dict) else None
        return name, delay if isinstance(delay, int) and delay > 0 else None

    if not node_names:
        return {}
    with ThreadPoolExecutor(max_workers=min(10, len(node_names))) as pool:
        return dict(pool.map(measure, node_names))


def switch_group_node(endpoint: Controller, group_name: str, node_name: str, *, record: bool = True) -> None:
    """Switch a Selector group's active node and verify the controller applied it."""
    path = f"/proxies/{quote(group_name, safe='')}"
    previous = ""
    if record:
        before = controller_request(path, endpoint, "GET", None, 5)
        if isinstance(before, dict) and isinstance(before.get("now"), str):
            previous = before["now"]
    if controller_request(path, endpoint, "PUT", {"name": node_name}, 5) is None:
        raise ValueError(f"无法将 {group_name} 切换到 {node_name}")
    current = controller_request(path, endpoint, "GET", None, 5)
    if current and current.get("now") != node_name:
        raise ValueError(f"控制接口未确认 {group_name} 已切换到 {node_name}")
    if record and previous and previous != node_name:
        envcheck.record_runtime_node_change(endpoint, group_name, previous, node_name)


def powershell(script: str, extra_env: dict[str, str] | None = None, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(WINDOWS / "System32/WindowsPowerShell/v1.0/powershell.exe"), "-NoProfile", "-NonInteractive",
         "-Command", "$ErrorActionPreference='Stop'\n" + script],
        capture_output=True, timeout=timeout, creationflags=NO_WINDOW, env={**os.environ, **(extra_env or {})},
    )


def clash_service_state() -> str:
    result = powershell(
        f"$s=Get-Service -Name '{CLASH_SERVICE}' -ErrorAction SilentlyContinue\n"
        "if ($null -eq $s) { 'Missing' } else { $s.Status.ToString() }"
    )
    return result.stdout.decode("ascii", errors="replace").strip() if result.returncode == 0 else "Unknown"


def clash_uses_service() -> bool:
    processes = process_snapshot()
    return any(name.casefold() in {"verge-mihomo.exe", "verge-mihomo-alpha.exe"}
               and processes.get(parent, ("", 0))[0].casefold() == "clash-verge-service.exe"
               for name, parent in processes.values())


def setup_clash_service() -> tuple[Path, bool]:
    binary = detected_proxy_apps().get("Clash Verge Rev")
    if not binary or not binary.is_file():
        raise ValueError("找不到 Clash Verge Rev 的安装路径，请在 Clash 设置里安装或修复服务")
    status = clash_service_state()
    if status == "Running":
        return binary, False
    if status == "Unknown":
        raise ValueError("无法查询 Clash Windows 服务，请在 Clash 设置里检查服务模式")
    if status == "Missing":
        installer = binary.parent / "resources/clash-verge-service-install.exe"
        if not installer.is_file():
            raise ValueError("此 Clash 安装目录没有附带服务安装程序，请在 Clash 设置里安装服务")
        result = powershell(
            "$p=Start-Process -FilePath $env:AI_LAUNCHER_SERVICE_INSTALLER "
            "-WorkingDirectory (Split-Path -LiteralPath $env:AI_LAUNCHER_SERVICE_INSTALLER) "
            "-Verb RunAs -WindowStyle Hidden -Wait -PassThru\nexit $p.ExitCode",
            {"AI_LAUNCHER_SERVICE_INSTALLER": str(installer)}, timeout=60,
        )
        if result.returncode:
            raise ValueError("Clash 服务安装未完成，请在 Clash 设置里重试并确认 Windows 授权")
    if clash_service_state() != "Running":
        result = powershell(
            f"$argsText='-NoProfile -NonInteractive -Command Start-Service -Name {CLASH_SERVICE}'\n"
            "$p=Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $argsText "
            "-Verb RunAs -WindowStyle Hidden -Wait -PassThru\nexit $p.ExitCode",
            timeout=60,
        )
        if result.returncode or clash_service_state() != "Running":
            raise ValueError("Clash Windows 服务没有启动，请在 Clash 设置里修复服务")
    return binary, True


def restart_clash(binary: Path, endpoint: Controller) -> None:
    script = r'''
$exe = $env:AI_LAUNCHER_CLASH_EXE
$folder = Split-Path -LiteralPath $exe
$cores = @((Join-Path $folder 'verge-mihomo.exe'), (Join-Path $folder 'verge-mihomo-alpha.exe'))
$apps = @(Get-CimInstance Win32_Process -Filter "Name='clash-verge.exe'" | Where-Object ExecutablePath -EQ $exe)
$children = @(Get-CimInstance Win32_Process -Filter "Name='verge-mihomo.exe' OR Name='verge-mihomo-alpha.exe'" |
    Where-Object { $_.ParentProcessId -in $apps.ProcessId -and $_.ExecutablePath -in $cores })
try {
    foreach ($app in $apps) {
        $process = Get-Process -Id $app.ProcessId -ErrorAction SilentlyContinue
        if ($process -and $process.CloseMainWindow()) { $null = $process.WaitForExit(1200) }
        if ($process -and -not $process.HasExited) {
            Stop-Process -InputObject $process
            $null = $process.WaitForExit(2000)
        }
    }
    foreach ($child in $children) {
        $process = Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
        if ($process -and $process.Path -in $cores) { Stop-Process -InputObject $process }
    }
} finally {
    Start-Process -FilePath $exe -WorkingDirectory $folder -WindowStyle Hidden
}
'''
    if powershell(script, {"AI_LAUNCHER_CLASH_EXE": str(binary)}, timeout=25).returncode:
        raise ValueError("Clash 核心未能重新启动，请从系统托盘退出 Clash 后重新打开")
    deadline = time.monotonic() + 52
    while time.monotonic() < deadline:
        if clash_uses_service() and controller("/configs", endpoint):
            return
        time.sleep(1)
    raise ValueError("Clash 已重新打开，核心暂未就绪")


def repair_proxy_nodes(report: Report, hosts: list[str]) -> list[str]:
    endpoint = report.endpoint
    if not endpoint or report.mode != "rule":
        return []
    failures = [host for host in hosts if host in report.probes and not report.probes[host].connected
                and report.probes[host].route != "direct"]
    if not failures:
        return []
    proxies_data = controller("/proxies", endpoint).get("proxies") or {}
    messages = []
    rules = controller("/rules", endpoint).get("rules") or []
    by_group = {}
    for service in hosts:
        host = service.split("/", 1)[0]
        site = ""
        if host in {"chatgpt.com", "ws.chatgpt.com", "api.openai.com"}:
            site = "openai"
        elif host in {"claude.ai", "api.anthropic.com"}:
            site = "anthropic"
        elif host.endswith((".google.com", ".googleapis.com")):
            site = "google"
        for rule in rules:
            kind, payload = rule.get("type"), str(rule.get("payload") or "").casefold()
            if (kind == "Domain" and host == payload
                    or kind == "DomainSuffix" and (host == payload or host.endswith("." + payload))
                    or kind == "DomainKeyword" and payload and payload in host
                    or kind == "GeoSite" and site and payload == site):
                group = rule.get("proxy")
                if proxies_data.get(group, {}).get("type") == "Selector":
                    by_group.setdefault(group, []).append(service)
                break
    for group, services in by_group.items():
        broken = [service for service in services if service in failures]
        if not broken:
            continue
        previous = proxies_data[group].get("now")
        choices = [name for name in proxies_data[group].get("all") or []
                   if name != previous and proxies_data.get(name, {}).get("type") not in NON_NODE_TYPES
                   and not proxies_data.get(name, {}).get("all")]
        # Prefer alive nodes AND avoid AI-restricted regions (HK/CN/RU) when ranking candidates!
        choices.sort(key=lambda name: (proxies.node_looks_restricted(name), proxies_data[name].get("alive") is not True))
        probes = broken[:3]

        def latency(name):
            scores = []
            for service in probes:
                query = urlencode({"url": "https://" + service, "timeout": 3500, "expected": "200-499"})
                data = controller_request(f"/proxies/{quote(name, safe='')}/delay?{query}", endpoint, "GET", None, 6)
                if not data or not isinstance(data.get("delay"), int):
                    return float("inf")
                scores.append(data["delay"])
            return sum(scores)

        with ThreadPoolExecutor(max_workers=8) as pool:
            ranked = sorted(
                ((proxies.node_looks_restricted(name), delay, name)
                 for delay, name in zip(pool.map(latency, choices[:12]), choices[:12]))
            )
        group_path = f"/proxies/{quote(group, safe='')}"
        for _restricted, delay, name in ranked[:2]:
            if delay == float("inf") or not isinstance(previous, str):
                break
            if controller_request(group_path, endpoint, "PUT", {"name": name}, 5) is None:
                break
            verified = False
            changed_elsewhere = False
            try:
                with ThreadPoolExecutor(max_workers=min(8, len(services))) as pool:
                    checks = list(pool.map(lambda host: connect_probe(host, report.port, endpoint=endpoint), services))
                verified = all(probe.connected and probe.route == "proxy" for probe in checks)
                if verified:
                    envcheck.record_runtime_node_change(endpoint, group, previous, name)
                    messages.append(f"{group} 已换到通过复测的节点 · {name}")
                    break
            finally:
                if not verified:
                    current = controller_request(group_path, endpoint, "GET", None, 5)
                    if current and current.get("now") != name:
                        changed_elsewhere = True
                    elif controller_request(group_path, endpoint, "PUT", {"name": previous}, 5) is None:
                        raise ValueError(f"{group} 节点复测失败，原节点未能自动恢复")
            if changed_elsewhere:
                break
    return messages


def _find_other_tun_controller(selected: Report, available: list[Report], custom: Controller | None) -> Controller | None:
    endpoint = selected.endpoint
    other = next(
        (report.endpoint for report in available if report.endpoint and report.endpoint != endpoint and report.tun),
        None,
    )
    if other:
        return other
    if selected.provider == "JImmm":
        controls = ([custom] if custom else []) + clash_controllers()
        return next((control for control in controls if control != endpoint and tun_enabled(control) is True), None)
    controls = proxy_controllers("JImmm") if IS_MAC else [JIMMM_CONTROLLER]
    return next((control for control in controls if control != endpoint and tun_enabled(control) is True), None)


def prepare_apps(keys: list[str], port: int | None, provider: str, custom: Controller | None,
                 automatic: bool, opening: bool, progress,
                 targets: dict[str, Target] | None = None) -> tuple[list[Report], tuple[str, ...], int | None, list[str]]:
    catalog = TARGETS if targets is None else targets
    valid_keys = [key for key in keys if key in catalog]
    hosts = list(dict.fromkeys(host for key in valid_keys for host in catalog[key].hosts))
    native = [catalog[key] for key in valid_keys if requires_tun(catalog[key])]
    preferred = system_proxy_port()
    messages: list[str] = []

    def collect(collect_hints: bool = False):
        if automatic:
            if collect_hints:
                try:
                    return check_reports(hosts, bool(native), custom, hints=messages)
                except TypeError:
                    return check_reports(hosts, bool(native), custom)
            return check_reports(hosts, bool(native), custom)
        return [check(port, hosts, provider, custom)], ()

    reports, detected = collect(collect_hints=True)
    available = [report for report in reports if not report.issue]
    if not opening or not available:
        return reports, detected, preferred, messages

    for report in available:
        blocked = [catalog[key] for key in valid_keys if not any(
            block_reason([current.probes[host] for host in catalog[key].hosts if host in current.probes], False, current.tun, current.provider) is None
            for current in available
        ) and app_report(catalog[key], available, preferred) == report]
        if report.endpoint and blocked:
            progress(f"正在检查 {report.provider} 的节点…")
            try:
                changed = repair_proxy_nodes(report, hosts)
                messages.extend(changed)
                if changed:
                    reports, detected = collect()
                    available = [current for current in reports if not current.issue]
                    break
            except (ValueError, OSError) as error:
                messages.append(str(error))

    if not native or not available:
        return reports, detected, preferred, messages

    candidates = []
    for report in available:
        targets_ok = [target for target in native if block_reason(
            [report.probes[host] for host in target.hosts if host in report.probes], False, report.tun, report.provider) is None]
        if targets_ok and (report.endpoint or report.tun is True):
            candidates.append((report, targets_ok))
    candidates.sort(
        key=lambda item: (
            len(item[1]),
            not proxies.is_restricted_region(item[0].region),
            item[0].port == preferred,
            item[0].endpoint is not None,
            item[0].tun is True,
        ),
        reverse=True,
    )
    for selected, target_group in candidates:
        services = list(dict.fromkeys(host for target in target_group for host in target.hosts))
        endpoint = selected.endpoint
        if endpoint is None:
            progress(f"正在验证 {selected.provider} 的系统虚拟网卡…")
            try:
                validate_system_tun(services, selected.provider)
                messages.append(f"{selected.provider} 系统 TUN 线路已通过验证")
                return [replace(report, tun=False) if report != selected and report.tun else report
                        for report in reports], detected, preferred, messages
            except (OSError, ValueError) as error:
                messages.append(f"{selected.provider} 系统虚拟网卡未通过 · {error}")
            continue
        progress(f"正在验证 {selected.provider} 的虚拟网卡…")
        try:
            if (not IS_MAC and selected.provider == "Clash Verge Rev" and tun_enabled(endpoint) is not True
                    and (clash_service_state() != "Running" or not clash_uses_service())):
                progress("正在准备 Clash Windows 服务，可能需要确认 Windows 授权…")
                binary, _ = setup_clash_service()
                restart_clash(binary, endpoint)
                messages.append("Clash Windows 服务已接管核心")
            other = _find_other_tun_controller(selected, available, custom)
            changed = prepare_tun(endpoint, other, lambda control: validate_tun(control, services))
            if changed:
                messages.append(f"{selected.provider} TUN 已启用，系统线路通过验证")
            reports, detected = collect()
            return [replace(report, tun=False) if report.endpoint != endpoint and report.tun else report
                    for report in reports], detected, preferred, messages
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            messages.append(f"{selected.provider} 虚拟网卡未通过 · {error}")
            if IS_MAC and selected.provider == "Clash Verge Rev":
                messages.append(macos.clash_service_hint())
            elif IS_MAC:
                messages.append(f"请在 {selected.provider} 的界面启用 TUN，并按 macOS 提示完成授权。")
    return [replace(report, tun=False) if report.tun else report for report in reports], detected, preferred, messages
