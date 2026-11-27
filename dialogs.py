"""Modal dialogs for AIProxyLauncher: node switcher, env/DNS/IPv6 audit, app manager, controller, restart."""
from __future__ import annotations

from pathlib import Path
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from urllib.parse import urlparse

import envcheck
from launcher import (
    ALL_BUILTIN_TARGETS, IS_MAC, PRESET_TARGETS, TARGETS, Target,
    build_active_targets, focus_app, parse_port, restart_and_launch,
)
from repair import list_selector_groups, switch_group_node, test_group_delays
import tray
from widgets import AMBER, BLUE, DEFAULTS, FONT, MUTED_KEY, ROSE, TEAL, TEAL_DARK, PillButton
import win32net


def open_node_switcher(win) -> None:
    controllers = [r for r in win.last_available if r.endpoint is not None]
    if not controllers:
        win.write("当前没有支持控制接口的活跃代理（Clash / Mihomo / Sing-box），请先「检查全部」或配置控制接口。", "error")
        return
    p = win.p
    dialog = tk.Toplevel(win.root, bg=p["surface"])
    dialog.title("快捷切换节点与测速")
    dialog.transient(win.root)
    dialog.geometry(f"{win.px(680)}x{win.px(470)}")
    dialog.minsize(win.px(560), win.px(380))

    top = tk.Frame(dialog, bg=p["surface"], padx=win.px(16), pady=win.px(12))
    top.pack(fill="x")
    tk.Label(top, text="代理节点与分流组", font=(FONT, 12, "bold"), bg=p["surface"], fg=p["ink"]).pack(side="left")

    selected_report = tk.StringVar(value=controllers[0].provider)
    if len(controllers) > 1:
        combo = ttk.Combobox(top, textvariable=selected_report, values=[r.provider for r in controllers],
                             state="readonly", width=18, style="Dialog.TCombobox")
        combo.pack(side="left", padx=(win.px(12), 0))
    else:
        tk.Label(top, text=f"· {controllers[0].provider} :{controllers[0].port}", bg=p["surface"],
                 fg=p["muted"], font=(FONT, 9)).pack(side="left", padx=(win.px(8), 0))

    status_var = tk.StringVar(value="正在读取分流组…")
    tk.Label(top, textvariable=status_var, bg=p["surface"], fg=p["teal_ink"], font=(FONT, 8)).pack(side="right")

    split = tk.Frame(dialog, bg=p["surface"], padx=win.px(16))
    split.pack(fill="both", expand=True)
    split.grid_columnconfigure(0, weight=1)
    split.grid_columnconfigure(1, weight=2)
    split.grid_rowconfigure(1, weight=1)

    tk.Label(split, text="分流组", bg=p["surface"], fg=p["muted"], font=(FONT, 9, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
    tk.Label(split, text="节点列表（双击或点下方按钮切换）", bg=p["surface"], fg=p["muted"], font=(FONT, 9, "bold")).grid(row=0, column=1, sticky="w", padx=(10, 0), pady=(0, 4))

    group_list = tk.Listbox(split, bg=p["surface_alt"], fg=p["ink"], selectbackground=TEAL, selectforeground="#FFFFFF",
                            borderwidth=1, relief="solid", highlightthickness=0, font=(FONT, 9), exportselection=False)
    group_list.grid(row=1, column=0, sticky="nsew")

    node_frame = tk.Frame(split, bg=p["surface"])
    node_frame.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
    node_frame.grid_columnconfigure(0, weight=1)
    node_frame.grid_rowconfigure(0, weight=1)
    node_list = tk.Listbox(node_frame, bg=p["surface_alt"], fg=p["ink"], selectbackground=TEAL, selectforeground="#FFFFFF",
                           borderwidth=1, relief="solid", highlightthickness=0, font=(FONT, 9), exportselection=False)
    node_scroll = ttk.Scrollbar(node_frame, orient="vertical", command=node_list.yview)
    node_list.configure(yscrollcommand=node_scroll.set)
    node_list.grid(row=0, column=0, sticky="nsew")
    node_scroll.grid(row=0, column=1, sticky="ns")

    state: dict = {"groups": [], "endpoint": controllers[0].endpoint, "delays": {}}

    def current_group() -> dict | None:
        sel = group_list.curselection()
        if not sel or sel[0] >= len(state["groups"]):
            return None
        return state["groups"][sel[0]]

    def render_nodes(_e=None) -> None:
        node_list.delete(0, "end")
        grp = current_group()
        if not grp:
            return
        for node in grp["nodes"]:
            mark = "✓ " if node["name"] == grp["now"] else "   "
            delay = state["delays"].get(node["name"], node["delay"])
            d_txt = f"  [{delay}ms]" if isinstance(delay, int) else ("  [超时]" if node["name"] in state["delays"] else "")
            warn = "  [地区受限]" if node["restricted"] else ""
            node_list.insert("end", f"{mark}{node['name']}{d_txt}{warn}")

    def load_groups() -> None:
        rep = next((r for r in controllers if r.provider == selected_report.get()), controllers[0])
        state["endpoint"] = rep.endpoint
        status_var.set("正在读取分流组…")
        q: queue.Queue = queue.Queue()

        def worker():
            try:
                q.put(list_selector_groups(rep.endpoint))
            except Exception as err:
                q.put(err)

        def done():
            if not dialog.winfo_exists():
                return
            try:
                res = q.get_nowait()
            except queue.Empty:
                dialog.after(100, done)
                return
            if isinstance(res, Exception):
                status_var.set(f"读取失败：{res}")
                return
            state["groups"] = res
            group_list.delete(0, "end")
            for grp in res:
                group_list.insert("end", f"{grp['name']}  ({grp['now']})")
            if res:
                group_list.selection_set(0)
                render_nodes()
                status_var.set(f"共 {len(res)} 个可切换分流组")
            else:
                status_var.set("未发现手动可切换的 Selector 分流组")

        threading.Thread(target=worker, daemon=True).start()
        dialog.after(100, done)

    def run_delay_test() -> None:
        grp = current_group()
        if not grp or not state["endpoint"]:
            return
        names = [n["name"] for n in grp["nodes"] if n["type"] not in {"Direct", "Reject", "RejectDrop", "Pass", "Compatible"}]
        status_var.set(f"正在并发测速 {len(names)} 个节点…")
        q: queue.Queue = queue.Queue()

        def worker():
            try:
                q.put(test_group_delays(state["endpoint"], names[:40]))
            except Exception as err:
                q.put(err)

        def done():
            if not dialog.winfo_exists():
                return
            try:
                res = q.get_nowait()
            except queue.Empty:
                dialog.after(120, done)
                return
            if isinstance(res, dict):
                state["delays"].update(res)
                grp["nodes"].sort(key=lambda n: (
                    n["name"] != grp["now"],
                    n["restricted"],
                    state["delays"].get(n["name"]) is None,
                    state["delays"].get(n["name"]) or 99999,
                ))
                render_nodes()
                ok_cnt = sum(1 for v in res.values() if v is not None)
                status_var.set(f"测速完成：{ok_cnt}/{len(res)} 个节点可用")
            else:
                status_var.set(f"测速失败：{res}")

        threading.Thread(target=worker, daemon=True).start()
        dialog.after(120, done)

    def apply_switch(_e=None) -> None:
        grp = current_group()
        sel = node_list.curselection()
        if not grp or not sel or sel[0] >= len(grp["nodes"]):
            return
        chosen = grp["nodes"][sel[0]]["name"]
        try:
            switch_group_node(state["endpoint"], grp["name"], chosen)
            grp["now"] = chosen
            idx = group_list.curselection()[0]
            group_list.delete(idx)
            group_list.insert(idx, f"{grp['name']}  ({chosen})")
            group_list.selection_set(idx)
            render_nodes()
            status_var.set(f"已切换 {grp['name']} → {chosen}")
            win.write(f"已手动切换分流组 {grp['name']} → {chosen}", "good")
            if hasattr(win, "_update_rollback_badge"):
                win._update_rollback_badge()
        except (ValueError, OSError) as err:
            status_var.set(str(err))

    group_list.bind("<<ListboxSelect>>", render_nodes)
    node_list.bind("<Double-Button-1>", apply_switch)
    if len(controllers) > 1:
        selected_report.trace_add("write", lambda *_: load_groups())

    footer = tk.Frame(dialog, bg=p["surface"], padx=win.px(16), pady=win.px(12))
    footer.pack(fill="x")
    PillButton(footer, "⚡ 测速当前组", run_delay_test, bg=p["surface"], fill=p["surface_alt"],
               hover_fill=p["surface_hover"], fg=p["ink"], outline=p["border"], hover_outline=p["border_hover"],
               height=win.px(30), pad_x=win.px(13)).pack(side="left")
    PillButton(footer, "关闭并复测", lambda: (dialog.destroy(), win.begin(list(win.targets), False)),
               bg=p["surface"], fill=p["surface_alt"], hover_fill=p["surface_hover"], fg=p["muted"],
               outline=p["border"], hover_outline=p["border_hover"], height=win.px(30), pad_x=win.px(13)).pack(side="right")
    PillButton(footer, "切换到选中节点", apply_switch, bg=p["surface"], fill=TEAL, hover_fill=TEAL_DARK,
               fg="#FFFFFF", outline="", hover_outline="", height=win.px(30), pad_x=win.px(14)).pack(side="right", padx=(0, 8))

    load_groups()
    dialog.bind("<Escape>", lambda _e: dialog.destroy())
    dialog.grab_set()
    dialog.focus_set()


def open_env_audit(win) -> None:
    p = win.p
    active_ports = [r.port for r in win.last_available if r.port]
    preferred = active_ports[0] if active_ports else None
    tun_on = any(r.tun is True for r in win.last_available)
    win.last_conflicts = envcheck.audit_environment(active_ports, preferred)
    win.last_health = envcheck.diagnose_network_health(tun_on)
    win._update_env_badge()

    dialog = tk.Toplevel(win.root, bg=p["surface"])
    dialog.title("深度体检：DNS / IPv6 与环境配置")
    dialog.transient(win.root)
    dialog.geometry(f"{win.px(660)}x{win.px(520)}")

    head = tk.Frame(dialog, bg=p["surface"], padx=win.px(16), pady=win.px(12))
    head.pack(fill="x")
    tk.Label(head, text="深度网络与环境体检", font=(FONT, 12, "bold"), bg=p["surface"], fg=p["ink"]).pack(anchor="w")
    tk.Label(head, text="排查 DNS 污染 / Fake-IP、公网 IPv6 泄漏风险，以及残留失效的环境变量 / Codex / Git / npm 代理。",
             bg=p["surface"], fg=p["muted"], font=(FONT, 8)).pack(anchor="w", pady=(3, 0))

    # Section 1: DNS & IPv6 health findings
    net_box = tk.Frame(dialog, bg=p["surface"], padx=win.px(16))
    net_box.pack(fill="x", pady=(0, win.px(8)))
    tk.Label(net_box, text="底层网络健康（DNS / IPv6）", bg=p["surface"], fg=p["muted"], font=(FONT, 9, "bold")).pack(anchor="w", pady=(0, 4))
    for item in win.last_health:
        bg_c = p["teal_pale"] if item.status == "ok" else (p["amber_soft"] if item.status == "warn" else p["rose_soft"])
        fg_c = p["teal_ink"] if item.status == "ok" else (p["amber_ink"] if item.status == "warn" else p["rose_ink"])
        mark = "✓" if item.status == "ok" else ("!" if item.status == "warn" else "✕")
        row = tk.Frame(net_box, bg=bg_c, padx=10, pady=6)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=f"{mark}  [{item.category}] {item.title}", bg=bg_c, fg=fg_c, font=(FONT, 9, "bold"), anchor="w").pack(fill="x")
        tk.Label(row, text=item.detail, bg=bg_c, fg=p["ink"], font=(FONT, 8), anchor="w", wraplength=win.px(590), justify="left").pack(fill="x")

    # Section 2: Stale config conflicts
    body = tk.Frame(dialog, bg=p["surface"], padx=win.px(16))
    body.pack(fill="both", expand=True)
    tk.Label(body, text="本机残留代理配置", bg=p["surface"], fg=p["muted"], font=(FONT, 9, "bold")).pack(anchor="w", pady=(4, 4))
    list_wrap = tk.Frame(body, bg=p["surface"])
    list_wrap.pack(fill="both", expand=True)

    status_label = tk.Label(dialog, text="", bg=p["surface"], fg=p["teal_ink"], font=(FONT, 8))
    status_label.pack(anchor="w", padx=win.px(16), pady=(4, 0))

    def render() -> None:
        for child in list_wrap.winfo_children():
            child.destroy()
        if not win.last_conflicts:
            tk.Label(list_wrap, text="✓ 未发现残留或冲突的代理环境变量与配置文件。",
                     bg=p["teal_pale"], fg=p["teal_ink"], font=(FONT, 9, "bold"), padx=12, pady=12).pack(fill="x", pady=6)
            return
        for item in win.last_conflicts:
            card = tk.Frame(list_wrap, bg=p["surface_alt"], highlightthickness=1, highlightbackground=p["border"], padx=10, pady=7)
            card.pack(fill="x", pady=3)
            left = tk.Frame(card, bg=p["surface_alt"])
            left.pack(side="left", fill="x", expand=True)
            tk.Label(left, text=f"{item.source} · {item.key} = {item.current_value}",
                     bg=p["surface_alt"], fg=p["ink"], font=(FONT, 9, "bold"), anchor="w").pack(fill="x")
            tk.Label(left, text=item.reason, bg=p["surface_alt"], fg=p["amber_ink"], font=(FONT, 8), anchor="w").pack(fill="x")
            act_text = f"同步 :{item.target_port}" if (item.action == "sync" and item.target_port) else "清理失效项"
            PillButton(
                card, act_text, lambda c=item: repair_one(c),
                bg=p["surface_alt"], fill=TEAL, hover_fill=TEAL_DARK, fg="#FFFFFF",
                outline="", hover_outline="", font_spec=(FONT, 8, "bold"), height=win.px(25), pad_x=win.px(10),
            ).pack(side="right", padx=(8, 0))

    def repair_one(item: envcheck.EnvConflict) -> None:
        try:
            msg = envcheck.fix_conflict(item, data_dir=win.settings.parent)
            status_label.configure(text=msg, fg=p["teal_ink"])
            win.write(f"环境修复 · {msg}", "good")
        except (OSError, ValueError) as err:
            status_label.configure(text=str(err), fg=p["rose_ink"])
        win.last_conflicts = envcheck.audit_environment(active_ports, preferred)
        win._update_env_badge()
        if hasattr(win, "_update_rollback_badge"):
            win._update_rollback_badge()
        render()

    def repair_all() -> None:
        fixed = 0
        for item in list(win.last_conflicts):
            try:
                win.write(f"环境修复 · {envcheck.fix_conflict(item, data_dir=win.settings.parent)}", "good")
                fixed += 1
            except (OSError, ValueError) as err:
                win.write(f"环境修复失败 · {err}", "error")
        win.last_conflicts = envcheck.audit_environment(active_ports, preferred)
        win._update_env_badge()
        if hasattr(win, "_update_rollback_badge"):
            win._update_rollback_badge()
        status_label.configure(text=f"已完成 {fixed} 项自动修复（已保留 .bak 备份，可在「撤销修改」一键还原）", fg=p["teal_ink"])
        render()

    def copy_report() -> None:
        text = envcheck.build_diagnostic_report(
            win.last_available,
            win.last_detected,
            win.last_health,
            win.last_conflicts,
            relay_port=win.relay.port if win.relay.running else None,
            provider_mode=win.provider,
        )
        win.root.clipboard_clear()
        win.root.clipboard_append(text)
        status_label.configure(text="已复制脱敏诊断报告到剪贴板（不含密钥与个人路径）", fg=p["teal_ink"])

    footer = tk.Frame(dialog, bg=p["surface"], padx=win.px(16), pady=win.px(12))
    footer.pack(fill="x")
    if win.last_conflicts:
        PillButton(footer, "一键修复全部", repair_all, bg=p["surface"], fill=TEAL, hover_fill=TEAL_DARK,
                   fg="#FFFFFF", outline="", hover_outline="", height=win.px(30), pad_x=win.px(14)).pack(side="left", padx=(0, 8))
    PillButton(footer, "复制脱敏诊断报告", copy_report, bg=p["surface"], fill=p["surface_alt"], hover_fill=p["surface_hover"],
               fg=p["ink"], outline=p["border"], hover_outline=p["border_hover"], height=win.px(30), pad_x=win.px(13)).pack(side="left", padx=(0, 8))
    PillButton(footer, "↺ 撤销软件修改", lambda: (dialog.destroy(), open_rollback_dialog(win)),
               bg=p["surface"], fill=p["amber_soft"], hover_fill=AMBER, fg=p["amber_ink"],
               outline=p["border"], hover_outline=AMBER, height=win.px(30), pad_x=win.px(13)).pack(side="left")
    PillButton(footer, "关闭", dialog.destroy, bg=p["surface"], fill=p["surface_alt"], hover_fill=p["surface_hover"],
               fg=p["muted"], outline=p["border"], hover_outline=p["border_hover"], height=win.px(30), pad_x=win.px(14)).pack(side="right")

    render()
    dialog.bind("<Escape>", lambda _e: dialog.destroy())
    dialog.grab_set()
    dialog.focus_set()


def open_rollback_dialog(win) -> tk.Toplevel:
    """Display all reversible modifications made by AIProxyLauncher and allow one-click rollback."""
    p = win.p
    dialog = tk.Toplevel(win.root, bg=p["surface"])
    dialog.title("撤销软件修改 · 故障排除")
    dialog.transient(win.root)
    dialog.geometry(f"{win.px(690)}x{win.px(540)}")
    dialog.minsize(win.px(560), win.px(420))

    head = tk.Frame(dialog, bg=p["surface"], padx=win.px(16), pady=win.px(12))
    head.pack(fill="x")
    tk.Label(head, text="撤销软件修改（故障排除）", font=(FONT, 12, "bold"), bg=p["surface"], fg=p["ink"]).pack(anchor="w")
    tk.Label(
        head,
        text="若怀疑网络或应用异常由本软件修改引起，可在此逐项或一键恢复外部配置文件（.codex/.env、.gitconfig、.npmrc）、"
             "环境变量、开机自启以及本次会话内的代理节点/TUN 变更。",
        bg=p["surface"], fg=p["muted"], font=(FONT, 8), wraplength=win.px(640), justify="left",
    ).pack(anchor="w", pady=(3, 0))

    body_wrap = tk.Frame(dialog, bg=p["surface"], padx=win.px(16))
    body_wrap.pack(fill="both", expand=True)
    tk.Label(body_wrap, text="可撤销的外部与代理状态修改", bg=p["surface"], fg=p["muted"], font=(FONT, 9, "bold")).pack(anchor="w", pady=(2, 4))

    scroll_host = tk.Frame(body_wrap, bg=p["surface"])
    scroll_host.pack(fill="both", expand=True)
    canvas = tk.Canvas(scroll_host, bg=p["surface"], highlightthickness=0, bd=0)
    scrollbar = ttk.Scrollbar(scroll_host, orient="vertical", command=canvas.yview, style="Subtle.Vertical.TScrollbar")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    list_wrap = tk.Frame(canvas, bg=p["surface"])
    list_win = canvas.create_window(0, 0, anchor="nw", window=list_wrap)

    def _sync_scroll(_e=None) -> None:
        bbox = canvas.bbox("all")
        if bbox:
            canvas.configure(scrollregion=bbox)

    list_wrap.bind("<Configure>", _sync_scroll)
    canvas.bind("<Configure>", lambda e: (canvas.itemconfigure(list_win, width=e.width), _sync_scroll()))

    # Optional: software own data reset section
    own_box = tk.Frame(dialog, bg=p["surface_alt"], highlightthickness=1, highlightbackground=p["border"], padx=12, pady=9)
    own_box.pack(fill="x", padx=win.px(16), pady=(win.px(8), 0))
    tk.Label(own_box, text="本软件自身数据（不影响外部系统，按需重置）", bg=p["surface_alt"], fg=p["ink"], font=(FONT, 9, "bold")).pack(anchor="w")
    tk.Label(
        own_box,
        text=f"数据目录：{win.settings.parent}（含 settings.json 与独立网页 Browser/ 资料）",
        bg=p["surface_alt"], fg=p["muted"], font=(FONT, 8), anchor="w",
    ).pack(fill="x", pady=(2, 6))
    own_btns = tk.Frame(own_box, bg=p["surface_alt"])
    own_btns.pack(fill="x")

    status_label = tk.Label(dialog, text="", bg=p["surface"], fg=p["teal_ink"], font=(FONT, 8), anchor="w", justify="left")
    status_label.pack(fill="x", padx=win.px(16), pady=(6, 0))

    state: dict = {"items": [], "busy": False}
    cat_labels = {
        "file": ("配置文件", p["teal_pale"], p["teal_ink"]),
        "winenv": ("环境变量", p["amber_soft"], p["amber_ink"]),
        "autostart": ("开机自启", p["surface_hover"], p["ink"]),
        "runtime": ("代理状态", p["rose_soft"], p["rose_ink"]),
    }

    def refresh() -> None:
        if not dialog.winfo_exists():
            return
        state["items"] = envcheck.list_rollback_items(data_dir=win.settings.parent, include_runtime=True)
        if hasattr(win, "_update_rollback_badge"):
            win._update_rollback_badge()
        for ch in list_wrap.winfo_children():
            ch.destroy()
        if not state["items"]:
            tk.Label(
                list_wrap,
                text="✓ 当前未检测到任何尚未撤销的外部文件、环境变量、自启或会话代理修改。\n"
                     "说明本软件当前没有改动您的外部系统配置；若仍无法联网，请排查代理软件节点或系统网络。",
                bg=p["teal_pale"], fg=p["teal_ink"], font=(FONT, 9, "bold"),
                padx=12, pady=12, justify="left", anchor="w", wraplength=win.px(600),
            ).pack(fill="x", pady=6)
            undo_all_btn.set_state("disabled")
            return
        undo_all_btn.set_state("disabled" if state["busy"] else "normal")
        for item in state["items"]:
            card = tk.Frame(list_wrap, bg=p["surface_alt"], highlightthickness=1, highlightbackground=p["border"], padx=10, pady=8)
            card.pack(fill="x", pady=3)
            left = tk.Frame(card, bg=p["surface_alt"])
            left.pack(side="left", fill="x", expand=True)
            top_line = tk.Frame(left, bg=p["surface_alt"])
            top_line.pack(fill="x")
            cat_txt, c_bg, c_fg = cat_labels.get(item.category, ("修改项", p["surface_hover"], p["ink"]))
            tk.Label(top_line, text=cat_txt, bg=c_bg, fg=c_fg, font=(FONT, 7, "bold"), padx=6, pady=1).pack(side="left", padx=(0, 6))
            tk.Label(top_line, text=item.title, bg=p["surface_alt"], fg=p["ink"], font=(FONT, 9, "bold"), anchor="w").pack(side="left")
            tk.Label(left, text=item.detail, bg=p["surface_alt"], fg=p["muted"], font=(FONT, 8), anchor="w",
                     wraplength=win.px(500), justify="left").pack(fill="x", pady=(3, 0))
            btn = PillButton(
                card, "撤销此项", lambda target=item: undo_one(target),
                bg=p["surface_alt"], fill=p["rose_soft"], hover_fill=ROSE, fg=p["rose_ink"],
                outline="", hover_outline="", font_spec=(FONT, 8, "bold"), height=win.px(25), pad_x=win.px(10),
            )
            btn.set_state("disabled" if state["busy"] else "normal")
            btn.pack(side="right", padx=(8, 0))

    def _run_async_action(worker_fn, finish_fn, busy_text: str) -> None:
        state["busy"] = True
        status_label.configure(text=busy_text, fg=p["muted"])
        undo_all_btn.set_state("disabled")
        q: queue.Queue = queue.Queue()

        def worker():
            try:
                q.put(("ok", worker_fn()))
            except Exception as exc:
                q.put(("err", exc))

        def poll():
            if not dialog.winfo_exists():
                return
            try:
                kind, payload = q.get_nowait()
            except queue.Empty:
                dialog.after(80, poll)
                return
            state["busy"] = False
            finish_fn(kind, payload)
            refresh()

        threading.Thread(target=worker, daemon=True).start()
        dialog.after(80, poll)

    def undo_one(item: envcheck.RollbackItem) -> None:
        if state["busy"]:
            return
        if item.category == "runtime":
            def finish(kind, payload):
                if kind == "ok":
                    status_label.configure(text=f"✓ {payload}", fg=p["teal_ink"])
                    win.write(f"撤销修改 · {payload}", "good")
                else:
                    status_label.configure(text=f"撤销失败：{payload}", fg=p["rose_ink"])
                    win.write(f"撤销修改失败 · {payload}", "error")

            _run_async_action(
                lambda: envcheck.rollback_item(item, data_dir=win.settings.parent),
                finish,
                f"正在撤销 {item.title}…",
            )
            return
        try:
            msg = envcheck.rollback_item(item, data_dir=win.settings.parent)
            status_label.configure(text=f"✓ {msg}", fg=p["teal_ink"])
            win.write(f"撤销修改 · {msg}", "good")
        except (OSError, ValueError) as err:
            status_label.configure(text=f"撤销失败：{err}", fg=p["rose_ink"])
            win.write(f"撤销修改失败 · {err}", "error")
        refresh()

    def _finish_undo_all(done: list[str], errors: list[str]) -> None:
        for msg in done:
            win.write(f"撤销修改 · {msg}", "good")
        for err in errors:
            win.write(f"撤销修改失败 · {err}", "error")
        if errors:
            status_label.configure(text=f"已撤销 {len(done)} 项，{len(errors)} 项失败：{errors[0]}", fg=p["rose_ink"])
        elif done:
            status_label.configure(text=f"✓ 已一键撤销全部 {len(done)} 项外部与代理状态修改。", fg=p["teal_ink"])
        else:
            status_label.configure(text="当前没有需要撤销的外部修改。", fg=p["muted"])

    def undo_all() -> None:
        if state["busy"]:
            return
        has_runtime = any(it.category == "runtime" for it in state["items"])
        if has_runtime:
            def finish(kind, payload):
                if kind == "ok":
                    _finish_undo_all(*payload)
                else:
                    status_label.configure(text=f"撤销失败：{payload}", fg=p["rose_ink"])

            _run_async_action(
                lambda: envcheck.rollback_all_external(data_dir=win.settings.parent, include_runtime=True),
                finish,
                "正在撤销全部外部与代理状态修改…",
            )
            return
        done, errors = envcheck.rollback_all_external(data_dir=win.settings.parent, include_runtime=True)
        _finish_undo_all(done, errors)
        refresh()

    def do_reset_own(clear_browser: bool) -> None:
        try:
            msgs = win.reset_software_state(clear_browser=clear_browser)
            text = "；".join(msgs) if msgs else "本软件当前已是默认配置。"
            status_label.configure(text=f"✓ {text}", fg=p["teal_ink"])
        except OSError as err:
            status_label.configure(text=f"清理失败：{err}", fg=p["rose_ink"])

    PillButton(
        own_btns, "关闭中继并重置软件配置", lambda: do_reset_own(False),
        bg=p["surface_alt"], fill=p["surface"], hover_fill=p["surface_hover"],
        fg=p["ink"], outline=p["border"], hover_outline=p["border_hover"],
        font_spec=(FONT, 8), height=win.px(25), pad_x=win.px(10),
    ).pack(side="left", padx=(0, 8))
    PillButton(
        own_btns, "同时清理独立浏览器缓存", lambda: do_reset_own(True),
        bg=p["surface_alt"], fill=p["rose_soft"], hover_fill=ROSE,
        fg=p["rose_ink"], outline="", hover_outline="",
        font_spec=(FONT, 8, "bold"), height=win.px(25), pad_x=win.px(10),
    ).pack(side="left")

    footer = tk.Frame(dialog, bg=p["surface"], padx=win.px(16), pady=win.px(12))
    footer.pack(fill="x")
    undo_all_btn = PillButton(
        footer, "↺ 一键撤销全部外部修改", undo_all,
        bg=p["surface"], fill=ROSE, hover_fill="#BE123C", fg="#FFFFFF",
        outline="", hover_outline="", font_spec=(FONT, 9, "bold"), height=win.px(30), pad_x=win.px(14),
    )
    undo_all_btn.pack(side="left", padx=(0, 8))
    dialog.undo_all_button = undo_all_btn
    PillButton(
        footer, "刷新列表", refresh,
        bg=p["surface"], fill=p["surface_alt"], hover_fill=p["surface_hover"],
        fg=p["ink"], outline=p["border"], hover_outline=p["border_hover"], height=win.px(30), pad_x=win.px(12),
    ).pack(side="left")
    def close_dialog(_e=None) -> None:
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        dialog.destroy()

    PillButton(
        footer, "关闭", close_dialog,
        bg=p["surface"], fill=p["surface_alt"], hover_fill=p["surface_hover"],
        fg=p["muted"], outline=p["border"], hover_outline=p["border_hover"], height=win.px(30), pad_x=win.px(14),
    ).pack(side="right")

    refresh()
    dialog.protocol("WM_DELETE_WINDOW", close_dialog)
    dialog.bind("<Escape>", close_dialog)
    dialog.grab_set()
    dialog.focus_set()
    return dialog


def open_app_manager(win) -> None:
    p = win.p
    dialog = tk.Toplevel(win.root, bg=p["surface"])
    dialog.title("管理与自定义应用")
    dialog.transient(win.root)
    dialog.geometry(f"{win.px(680)}x{win.px(570)}")

    head = tk.Frame(dialog, bg=p["surface"], padx=win.px(16), pady=win.px(12))
    head.pack(fill="x")
    tk.Label(head, text="管理应用卡片与常驻设置", font=(FONT, 12, "bold"), bg=p["surface"], fg=p["ink"]).pack(anchor="w")
    tk.Label(head, text="可隐藏不常用卡片、开启热门 AI 工具预设、添加自定义应用，并设置系统托盘与开机静默自启。",
             bg=p["surface"], fg=p["muted"], font=(FONT, 8)).pack(anchor="w", pady=(2, 0))

    sec1 = tk.Frame(dialog, bg=p["surface_alt"], highlightthickness=1, highlightbackground=p["border"], padx=12, pady=10)
    sec1.pack(fill="x", padx=win.px(16), pady=(0, win.px(10)))
    tk.Label(sec1, text="内置与应用预设（勾选显示在主界面）", bg=p["surface_alt"], fg=p["ink"], font=(FONT, 9, "bold")).pack(anchor="w", pady=(0, 6))

    grid = tk.Frame(sec1, bg=p["surface_alt"])
    grid.pack(fill="x")
    vis_vars: dict[str, tk.BooleanVar] = {}
    for idx, (key, target) in enumerate(ALL_BUILTIN_TARGETS.items()):
        r_idx, c_idx = divmod(idx, 4)
        grid.grid_columnconfigure(c_idx, weight=1)
        shown = (key in TARGETS and key not in win.hidden_targets) or (key in win.enabled_presets and key not in win.hidden_targets)
        var = tk.BooleanVar(value=shown)
        vis_vars[key] = var
        tk.Checkbutton(
            grid, text=target.name, variable=var, bg=p["surface_alt"], activebackground=p["surface_alt"],
            fg=p["ink"], selectcolor=p["surface"], font=(FONT, 8), anchor="w",
        ).grid(row=r_idx, column=c_idx, sticky="w", padx=4, pady=2)

    sec2 = tk.Frame(dialog, bg=p["surface_alt"], highlightthickness=1, highlightbackground=p["border"], padx=12, pady=10)
    sec2.pack(fill="x", padx=win.px(16), pady=(0, win.px(8)))
    tk.Label(sec2, text="添加自定义应用或网页", bg=p["surface_alt"], fg=p["ink"], font=(FONT, 9, "bold")).grid(row=0, columnspan=3, sticky="w", pady=(0, 6))

    name_var = tk.StringVar()
    kind_var = tk.StringVar(value="独立网页窗口")
    target_var = tk.StringVar()
    host_var = tk.StringVar()
    kind_map = {"独立网页窗口": "web", "桌面应用 (代理参数)": "electron", "终端 CLI": "cli"}

    tk.Label(sec2, text="显示名称", bg=p["surface_alt"], fg=p["muted"], font=(FONT, 8)).grid(row=1, column=0, sticky="w", pady=3)
    ttk.Entry(sec2, textvariable=name_var, width=22, style="Dialog.TEntry").grid(row=1, column=1, sticky="ew", padx=6, pady=3)
    ttk.Combobox(sec2, textvariable=kind_var, values=list(kind_map), state="readonly", width=18, style="Dialog.TCombobox").grid(row=1, column=2, sticky="e", pady=3)

    target_lbl = tk.Label(sec2, text="网址 (URL)", bg=p["surface_alt"], fg=p["muted"], font=(FONT, 8))
    target_lbl.grid(row=2, column=0, sticky="w", pady=3)
    ttk.Entry(sec2, textvariable=target_var, width=32, style="Dialog.TEntry").grid(row=2, column=1, sticky="ew", padx=6, pady=3)

    def browse_custom() -> None:
        if IS_MAC:
            picked = filedialog.askopenfilename(parent=dialog, title="选择程序", initialdir="/Applications")
        else:
            picked = filedialog.askopenfilename(parent=dialog, title="选择程序", filetypes=[("可执行文件", "*.exe;*.cmd;*.bat"), ("所有文件", "*.*")])
        if picked:
            target_var.set(picked)
            if not name_var.get().strip():
                name_var.set(Path(picked).stem)

    browse_btn = PillButton(sec2, "浏览…", browse_custom, bg=p["surface_alt"], fill=p["surface"], hover_fill=p["surface_hover"],
                            fg=p["ink"], outline=p["border"], hover_outline=p["border_hover"], font_spec=(FONT, 8), height=win.px(25), pad_x=10)
    browse_btn.grid(row=2, column=2, sticky="e")

    tk.Label(sec2, text="检测域名", bg=p["surface_alt"], fg=p["muted"], font=(FONT, 8)).grid(row=3, column=0, sticky="w", pady=3)
    ttk.Entry(sec2, textvariable=host_var, width=32, style="Dialog.TEntry").grid(row=3, column=1, sticky="ew", padx=6, pady=3)
    sec2.grid_columnconfigure(1, weight=1)

    msg_var = tk.StringVar(value="")
    tk.Label(sec2, textvariable=msg_var, bg=p["surface_alt"], fg=p["rose_ink"], font=(FONT, 8)).grid(row=4, columnspan=3, sticky="w")

    def update_kind_ui(*_a):
        k = kind_map.get(kind_var.get(), "web")
        target_lbl.configure(text="网址 (URL)" if k == "web" else "程序路径")
        browse_btn.set_state("disabled" if k == "web" else "normal")

    kind_var.trace_add("write", update_kind_ui)
    update_kind_ui()

    custom_wrap = tk.Frame(dialog, bg=p["surface"], padx=win.px(16))
    custom_wrap.pack(fill="both", expand=True)

    def render_custom_list() -> None:
        for ch in custom_wrap.winfo_children():
            ch.destroy()
        if not win.custom_targets:
            tk.Label(custom_wrap, text="暂未添加自定义应用。", bg=p["surface"], fg=p["subtle"], font=(FONT, 8)).pack(anchor="w")
            return
        for ckey, ctarget in list(win.custom_targets.items()):
            row = tk.Frame(custom_wrap, bg=p["surface"])
            row.pack(fill="x", pady=2)
            tk.Label(row, text=f"• {ctarget.name}  ({', '.join(ctarget.hosts)})", bg=p["surface"], fg=p["ink"], font=(FONT, 8)).pack(side="left")
            PillButton(
                row, "删除", lambda k=ckey: delete_custom(k),
                bg=p["surface"], fill=p["rose_soft"], hover_fill=ROSE, fg=p["rose_ink"],
                outline="", hover_outline="", font_spec=(FONT, 7, "bold"), height=win.px(20), pad_x=8,
            ).pack(side="right")

    def delete_custom(k: str) -> None:
        win.custom_targets.pop(k, None)
        win.hidden_targets.discard(k)
        render_custom_list()

    def add_custom() -> None:
        name = name_var.get().strip()
        kind = kind_map.get(kind_var.get(), "web")
        raw_target = target_var.get().strip()
        raw_host = host_var.get().strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
        if not name:
            msg_var.set("请填写显示名称。")
            return
        url = ""
        if kind == "web":
            if not raw_target:
                msg_var.set("请填写网址，例如 https://chat.deepseek.com/")
                return
            url = raw_target if "://" in raw_target else f"https://{raw_target}"
            if not raw_host:
                raw_host = (urlparse(url).hostname or "").lower()
        else:
            if not raw_target or not Path(raw_target).exists():
                msg_var.set("请选择有效的本地程序路径。")
                return
        if not raw_host:
            msg_var.set("请填写用于代理检测的目标域名，例如 api.openai.com。")
            return
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "app"
        key = f"custom_{slug}"
        n = 2
        while key in ALL_BUILTIN_TARGETS or key in win.custom_targets:
            key = f"custom_{slug}_{n}"
            n += 1
        desc = "独立浏览器窗口" if kind == "web" else ("在终端里用代理变量启动" if kind == "cli" else "用代理参数启动应用")
        win.custom_targets[key] = Target(name, kind, (raw_host,), desc, url=url, custom=True)
        if kind != "web":
            win.paths[key] = Path(raw_target)
        win.selected[key] = tk.BooleanVar(value=True)
        name_var.set("")
        target_var.set("")
        host_var.set("")
        msg_var.set("")
        render_custom_list()

    PillButton(sec2, "添加", add_custom, bg=p["surface_alt"], fill=TEAL, hover_fill=TEAL_DARK, fg="#FFFFFF",
               outline="", hover_outline="", font_spec=(FONT, 8, "bold"), height=win.px(25), pad_x=12).grid(row=3, column=2, sticky="e")

    render_custom_list()

    # System integration options
    opts = tk.Frame(dialog, bg=p["surface"], padx=win.px(16), pady=win.px(4))
    opts.pack(fill="x")
    autostart_var = tk.BooleanVar(value=win32net.autostart_enabled())
    if tray.SystemTray.supported:
        tk.Checkbutton(
            opts, text="点击关闭按钮时最小化到系统托盘", variable=win.minimize_to_tray,
            bg=p["surface"], activebackground=p["surface"], fg=p["muted"], selectcolor=p["surface_alt"], font=(FONT, 8),
        ).pack(side="left", padx=(0, 14))
    tk.Checkbutton(
        opts, text="开机静默启动到托盘", variable=autostart_var,
        bg=p["surface"], activebackground=p["surface"], fg=p["muted"], selectcolor=p["surface_alt"], font=(FONT, 8),
    ).pack(side="left")

    footer = tk.Frame(dialog, bg=p["surface"], padx=win.px(16), pady=win.px(10))
    footer.pack(fill="x")

    def save_and_close() -> None:
        new_presets: list[str] = []
        new_hidden: set[str] = set()
        for key, var in vis_vars.items():
            if key in PRESET_TARGETS:
                if var.get():
                    new_presets.append(key)
            elif key in TARGETS:
                if not var.get():
                    new_hidden.add(key)
        if not any(v.get() for v in vis_vars.values()) and not win.custom_targets:
            msg_var.set("请至少保留一个显示的应用。")
            return
        try:
            script_path = Path(__file__).with_name("launcher.py").resolve()
            if autostart_var.get() != win32net.autostart_enabled():
                win32net.set_autostart(autostart_var.get(), script_path)
                if hasattr(win, "_update_rollback_badge"):
                    win._update_rollback_badge()
        except OSError as err:
            win.write(f"开机自启设置失败：{err}", "error")
        win.enabled_presets = new_presets
        win.hidden_targets = new_hidden
        win.targets = build_active_targets(win.custom_targets, win.enabled_presets, win.hidden_targets)
        for key, target in win.targets.items():
            win.selected.setdefault(key, tk.BooleanVar(value=key in DEFAULTS))
            win.details.setdefault(key, tk.StringVar(value=target.description))
            win.actual.setdefault(key, tk.StringVar(value="实时 · 检查中"))
            win.card_status.setdefault(key, ("待检查", MUTED_KEY))
        win.save()
        dialog.destroy()
        win.build_ui()

    PillButton(footer, "保存并更新主界面", save_and_close, bg=p["surface"], fill=TEAL, hover_fill=TEAL_DARK,
               fg="#FFFFFF", outline="", hover_outline="", height=win.px(30), pad_x=win.px(14)).pack(side="right")
    PillButton(footer, "取消", dialog.destroy, bg=p["surface"], fill=p["surface_alt"], hover_fill=p["surface_hover"],
               fg=p["muted"], outline=p["border"], hover_outline=p["border_hover"], height=win.px(30), pad_x=win.px(12)).pack(side="right", padx=(0, 8))

    dialog.bind("<Escape>", lambda _e: dialog.destroy())
    dialog.grab_set()
    dialog.focus_set()


def edit_controller(win) -> None:
    p = win.p
    dialog = tk.Toplevel(win.root, bg=p["surface"])
    dialog.title("代理控制接口")
    dialog.transient(win.root)
    dialog.resizable(False, False)
    area = tk.Frame(dialog, bg=p["surface"], padx=win.px(18), pady=win.px(16))
    area.pack()
    tk.Label(area, text="代理控制接口（可选）", font=(FONT, 12, "bold"), bg=p["surface"], fg=p["ink"]).grid(row=0, columnspan=2, sticky="w", pady=(0, win.px(4)))
    tk.Label(
        area,
        text="Clash / Mihomo / Sing-box 等兼容接口通常会自动读取。\n"
             "若控制接口设置了密钥或非常规端口，可填写 127.0.0.1 上的控制端口和密钥。",
        justify="left", bg=p["surface"], fg=p["muted"], font=(FONT, 9),
    ).grid(row=1, columnspan=2, sticky="w", pady=(0, win.px(12)))
    port = tk.StringVar(value=str(win.clash_controller_port or ""))
    secret = tk.StringVar(value=win.clash_secret)
    for row, (label, variable, mask) in enumerate((("控制端口", port, ""), ("密钥（本次有效）", secret, "*")), 2):
        tk.Label(area, text=label, bg=p["surface"], fg=p["ink"], font=(FONT, 9)).grid(row=row, column=0, sticky="w", pady=win.px(5))
        ttk.Entry(area, textvariable=variable, show=mask, width=24, style="Dialog.TEntry").grid(row=row, column=1, padx=(win.px(10), 0), sticky="ew")
    problem = tk.Label(area, bg=p["surface"], fg=p["rose_ink"], font=(FONT, 8))
    problem.grid(row=4, columnspan=2, sticky="w", pady=(win.px(4), 0))

    def apply(_event=None):
        try:
            selected_port = parse_port(port.get()) if port.get().strip() else None
        except ValueError:
            problem.configure(text="请输入有效的本机控制端口。")
            return
        win.clash_controller_port = selected_port
        win.clash_secret = secret.get() if selected_port else ""
        for key in win.targets:
            win.tag(key, "待检查")
        win._render_proxy_pills([], f"{win.provider} 状态待检查")
        win.clear_log()
        win.write("控制接口已更新。点击「检查全部」验证。")
        win.save()
        dialog.destroy()

    btn_bar = tk.Frame(area, bg=p["surface"])
    btn_bar.grid(row=5, columnspan=2, sticky="e", pady=(win.px(10), 0))
    PillButton(btn_bar, "取消", dialog.destroy, bg=p["surface"], fill=p["surface_alt"], hover_fill=p["surface_hover"],
               fg=p["muted"], outline=p["border"], hover_outline=p["border_hover"], height=win.px(30), pad_x=win.px(14)).pack(side="left", padx=(0, win.px(8)))
    PillButton(btn_bar, "应用", apply, bg=p["surface"], fill=TEAL, hover_fill=TEAL_DARK, fg="#FFFFFF",
               outline="", hover_outline="", height=win.px(30), pad_x=win.px(16)).pack(side="left")

    dialog.bind("<Return>", apply)
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.grab_set()
    dialog.focus_set()


def confirm_restart_app(win, key: str, target: Target, path: Path | None, port: int, route: str,
                        *, stable_profile: bool = False) -> None:
    p = win.p
    dialog = tk.Toplevel(win.root, bg=p["surface"])
    dialog.title(f"{target.name} 已在运行")
    dialog.transient(win.root)
    dialog.resizable(False, False)
    area = tk.Frame(dialog, bg=p["surface"], padx=win.px(18), pady=win.px(16))
    area.pack()
    tk.Label(area, text=f"{target.name} 已在运行", font=(FONT, 12, "bold"), bg=p["surface"], fg=p["ink"]).pack(anchor="w")
    tk.Label(
        area,
        text=f"该应用仅在启动时读取代理参数，当前未确认已走 {route}。\n"
             "选择「重启并切换代理」将先礼貌关闭窗口（保留未保存提示），关闭后自动使用新代理重启。",
        justify="left", bg=p["surface"], fg=p["muted"], font=(FONT, 9),
    ).pack(anchor="w", pady=(win.px(6), win.px(14)))

    bar = tk.Frame(area, bg=p["surface"])
    bar.pack(fill="x")

    def do_focus() -> None:
        dialog.destroy()
        if focus_app(target, path):
            win.write(f"{target.name} · 已置顶原窗口。")

    def do_restart() -> None:
        dialog.destroy()
        win.tag(key, "重启中", BLUE)
        win.write(f"正在关闭并重新通过 {route} 启动 {target.name}…")
        q: queue.Queue = queue.Queue()

        def worker():
            try:
                restart_and_launch(target, path, port, stable_profile=stable_profile)
                q.put(None)
            except Exception as err:
                q.put(err)

        def done():
            try:
                res = q.get_nowait()
            except queue.Empty:
                win.root.after(150, done)
                return
            if res is None:
                win.tag(key, "已重启打开", TEAL)
                win.write(f"{target.name} · 已使用 {route} 重新启动。", "good")
            else:
                win.tag(key, "需重启生效", AMBER)
                win.write(f"{target.name} · {res}", "error")

        threading.Thread(target=worker, daemon=True).start()
        win.root.after(150, done)

    PillButton(bar, "仅置顶原窗口", do_focus, bg=p["surface"], fill=p["surface_alt"], hover_fill=p["surface_hover"],
               fg=p["ink"], outline=p["border"], hover_outline=p["border_hover"], height=win.px(30), pad_x=12).pack(side="left")
    PillButton(bar, "重启并切换代理", do_restart, bg=p["surface"], fill=TEAL, hover_fill=TEAL_DARK,
               fg="#FFFFFF", outline="", hover_outline="", height=win.px(30), pad_x=14).pack(side="right")
    PillButton(bar, "取消", dialog.destroy, bg=p["surface"], fill=p["surface_alt"], hover_fill=p["surface_hover"],
               fg=p["muted"], outline=p["border"], hover_outline=p["border_hover"], height=win.px(30), pad_x=12).pack(side="right", padx=(0, 8))
    dialog.bind("<Escape>", lambda _e: dialog.destroy())
    dialog.grab_set()
    dialog.focus_set()


USER_GUIDE_SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "01",
        "快速开始（3 步一键启动）",
        (
            "先打开本机任意代理软件并连上节点：默认「自动检测」已适配 Clash Verge Rev、Clash Party、JImmm、FlClash、v2rayN、Hiddify、NekoBox等20+款客户端；其他代理也可切换「手动指定端口」。",
            "在主界面点击卡片勾选需要使用的AI应用（支持桌面客户端、独立网页窗口与终端CLI）。",
            "点击右上角「▶ 检查修复并打开」，软件会自动核验12+项AI服务连通性、TUN状态与出口节点后再安全启动。",
        ),
    ),
    (
        "02",
        "统一本地中继（换代理免重启 · 网页免重登）",
        (
            "点击顶部「⇄ 统一中继」可在127.0.0.1:17890启动本地稳定中继层（绝不直连泄漏）。",
            "开启后独立网页窗口共用固定浏览器资料目录，切换代理软件不再掉登录；后台按域名自动热切换上游代理并支持快速故障转移。",
        ),
    ),
    (
        "03",
        "出口地区识别与快捷切节点",
        (
            "顶部代理胶囊实时展示AI出口国家/地区与延迟；鼠标悬停底部服务圆点，可查看每项服务实际命中的出站节点与响应耗时。",
            "遇到香港等可能受部分AI平台限制的出口地区会高亮提醒，点击「切换节点」即可直接并发测速并换至美/日/台/新等节点。",
        ),
    ),
    (
        "04",
        "环境体检、撤销修改与运行中应用管理",
        (
            "点击「环境体检」可一键排查并带.bak备份修复环境变量、Codex、Git、npm中残留的失效旧代理端口，同时检测DNS污染与IPv6泄漏，并支持复制脱敏诊断报告。",
            "点击右上角「↺ 撤销修改」可随时逐项或一键还原全部配置文件备份、环境变量、开机自启及本次会话内的节点/TUN变更，快速排除是否由本软件改动引发问题。",
            "已在运行的桌面应用会显示「● 运行中」与「唤起 ↗」，可直接置顶已走对代理的窗口，或引导安全重启以应用新端口。",
        ),
    ),
    (
        "05",
        "常驻托盘、扩展应用与随时复阅",
        (
            "点击「+ 管理应用」可开启Cursor、Windsurf、VS Code、Grok、Perplexity等预设、添加自定义应用/网页，并设置开机静默启动到托盘。",
            "点击下方「我已阅读」进入主界面后，本说明书会收起在窗口右上角「? 使用说明」，随时点击即可再次查阅。",
        ),
    ),
)


def open_user_guide(win, *, startup_gate: bool = False, on_acknowledged=None) -> tk.Toplevel:
    """Display the interactive user manual modal.

    When startup_gate=True, blocks the main window and defers further actions until the user
    explicitly clicks '我已阅读，进入启动器'. When reopened from the top-right button
    (startup_gate=False), allows reviewing and toggling whether to auto-show on next launch.
    """
    existing = getattr(win, "_guide_dialog", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return existing
        except tk.TclError:
            pass

    p = win.p
    dialog = tk.Toplevel(win.root, bg=p["surface"])
    win._guide_dialog = dialog
    dialog.title("AI 代理启动器 · 使用说明书")
    dialog.transient(win.root)
    sw, sh = dialog.winfo_screenwidth(), dialog.winfo_screenheight()
    width = min(win.px(680), sw - 48)
    height = min(win.px(610), sh - 80)
    dialog.minsize(min(win.px(560), sw - 64), min(win.px(460), sh - 96))

    win.root.update_idletasks()
    rx, ry = win.root.winfo_rootx(), win.root.winfo_rooty()
    rw, rh = win.root.winfo_width(), win.root.winfo_height()
    if rw > 100 and rh > 100:
        pos_x = max(24, min(sw - width - 24, rx + (rw - width) // 2))
        pos_y = max(24, min(sh - height - 48, ry + (rh - height) // 2))
    else:
        pos_x = max(24, (sw - width) // 2)
        pos_y = max(24, (sh - height) // 2)
    dialog.geometry(f"{width}x{height}+{pos_x}+{pos_y}")

    # ── Dark Hero Banner ────────────────────────────────────────────────
    banner = tk.Frame(dialog, bg=p["hero_bg"], padx=win.px(20), pady=win.px(15))
    banner.pack(fill="x")

    title_bar = tk.Frame(banner, bg=p["hero_bg"])
    title_bar.pack(fill="x")
    badge = tk.Label(
        title_bar, text="GUIDE", font=(FONT, 8, "bold"),
        bg=TEAL, fg="#FFFFFF", padx=8, pady=2,
    )
    badge.pack(side="left", padx=(0, win.px(10)))
    tk.Label(
        title_bar, text="AI 代理启动器 · 使用说明书",
        font=(FONT, 14, "bold"), bg=p["hero_bg"], fg=p["hero_text"],
    ).pack(side="left")

    subtitle_text = (
        "请先浏览以下核心使用说明，点击底部「我已阅读」后即可进入主界面并开始检测"
        if startup_gate else
        "随时可在主界面右上角「? 使用说明」重新打开查阅"
    )
    tk.Label(
        banner, text=subtitle_text, font=(FONT, 9),
        bg=p["hero_bg"], fg=p["hero_muted"], anchor="w",
    ).pack(fill="x", pady=(win.px(6), 0))

    # ── Scrollable Structured Guide Cards ───────────────────────────────
    body_wrap = tk.Frame(dialog, bg=p["canvas"])
    body_wrap.pack(fill="both", expand=True)

    canvas = tk.Canvas(body_wrap, bg=p["canvas"], highlightthickness=0, bd=0, yscrollincrement=24)
    scrollbar = ttk.Scrollbar(body_wrap, orient="vertical", command=canvas.yview, style="Subtle.Vertical.TScrollbar")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    content = tk.Frame(canvas, bg=p["canvas"], padx=win.px(16), pady=win.px(12))
    content_win = canvas.create_window(0, 0, anchor="nw", window=content)

    wrap_labels: list[tk.Label] = []
    for num, heading, bullets in USER_GUIDE_SECTIONS:
        card = tk.Frame(
            content, bg=p["surface"],
            highlightthickness=1, highlightbackground=p["border"],
            padx=win.px(14), pady=win.px(10),
        )
        card.pack(fill="x", pady=(0, win.px(9)))

        head_row = tk.Frame(card, bg=p["surface"])
        head_row.pack(fill="x", pady=(0, win.px(5)))
        tk.Label(
            head_row, text=num, font=(FONT, 8, "bold"),
            bg=p["teal_soft"], fg=p["teal_ink"], padx=7, pady=1,
        ).pack(side="left", padx=(0, win.px(8)))
        tk.Label(
            head_row, text=heading, font=(FONT, 10, "bold"),
            bg=p["surface"], fg=p["ink"],
        ).pack(side="left")

        for item in bullets:
            row = tk.Frame(card, bg=p["surface"])
            row.pack(fill="x", pady=win.px(2))
            tk.Label(row, text="•", font=(FONT, 9, "bold"), bg=p["surface"], fg=TEAL).pack(side="left", anchor="n", padx=(2, 6))
            lbl = tk.Label(
                row, text=item, font=(FONT, 9), bg=p["surface"], fg=p["muted"],
                justify="left", anchor="w", wraplength=win.px(560),
            )
            lbl.pack(side="left", fill="x", expand=True)
            wrap_labels.append(lbl)

    def sync_scroll(_event=None) -> None:
        bbox = canvas.bbox("all")
        if bbox:
            canvas.configure(scrollregion=bbox)

    def on_canvas_resize(event) -> None:
        canvas.itemconfigure(content_win, width=event.width)
        target_wrap = max(win.px(320), event.width - win.px(86))
        for lbl in wrap_labels:
            lbl.configure(wraplength=target_wrap)
        sync_scroll()

    def on_wheel(event) -> None:
        if canvas.yview() != (0.0, 1.0):
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    content.bind("<Configure>", sync_scroll)
    canvas.bind("<Configure>", on_canvas_resize)
    dialog.bind("<MouseWheel>", on_wheel)

    # ── Bottom Action Bar ───────────────────────────────────────────────
    divider = tk.Frame(dialog, bg=p["border"], height=1)
    divider.pack(fill="x")
    footer = tk.Frame(dialog, bg=p["surface"], padx=win.px(18), pady=win.px(12))
    footer.pack(fill="x")

    if startup_gate:
        skip_var = tk.BooleanVar(value=bool(win.skip_guide_on_start))
        tk.Checkbutton(
            footer, text="下次打开不再自动弹出（右上角随时可重看）",
            variable=skip_var, bg=p["surface"], activebackground=p["surface"],
            fg=p["muted"], selectcolor=p["surface_alt"], font=(FONT, 8),
        ).pack(side="left")

        def acknowledge(_event=None) -> None:
            win.skip_guide_on_start = bool(skip_var.get())
            win.save()
            win._guide_dialog = None
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()
            if on_acknowledged:
                on_acknowledged()

        def decline_exit() -> None:
            win._guide_dialog = None
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()
            win.force_exit()

        dialog.confirm_button = PillButton(
            footer, "✓ 我已阅读，进入启动器", acknowledge,
            bg=p["surface"], fill=TEAL, hover_fill=TEAL_DARK, fg="#FFFFFF",
            outline="", hover_outline="", font_spec=(FONT, 9, "bold"),
            height=win.px(33), pad_x=win.px(16),
        )
        dialog.confirm_button.pack(side="right")
        dialog.exit_button = PillButton(
            footer, "退出", decline_exit,
            bg=p["surface"], fill=p["surface_alt"], hover_fill=p["surface_hover"],
            fg=p["muted"], outline=p["border"], hover_outline=p["border_hover"],
            height=win.px(33), pad_x=win.px(12),
        )
        dialog.exit_button.pack(side="right", padx=(0, win.px(8)))

        dialog.protocol("WM_DELETE_WINDOW", decline_exit)
        dialog.bind("<Return>", acknowledge)
    else:
        auto_show_var = tk.BooleanVar(value=not win.skip_guide_on_start)

        def on_toggle_auto() -> None:
            win.skip_guide_on_start = not auto_show_var.get()
            win.save()

        tk.Checkbutton(
            footer, text="每次打开程序时自动弹出使用说明书",
            variable=auto_show_var, command=on_toggle_auto,
            bg=p["surface"], activebackground=p["surface"],
            fg=p["muted"], selectcolor=p["surface_alt"], font=(FONT, 8),
        ).pack(side="left")

        def close_review(_event=None) -> None:
            win._guide_dialog = None
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()

        dialog.confirm_button = PillButton(
            footer, "我知道了", close_review,
            bg=p["surface"], fill=TEAL, hover_fill=TEAL_DARK, fg="#FFFFFF",
            outline="", hover_outline="", font_spec=(FONT, 9, "bold"),
            height=win.px(32), pad_x=win.px(18),
        )
        dialog.confirm_button.pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", close_review)
        dialog.bind("<Escape>", close_review)
        dialog.bind("<Return>", close_review)

    dialog.grab_set()
    dialog.lift()
    dialog.focus_force()
    return dialog
