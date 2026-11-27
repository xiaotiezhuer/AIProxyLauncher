"""Main dashboard window orchestrator for AIProxyLauncher."""
from __future__ import annotations

from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

import dialogs
import envcheck
from launcher import (
    AUTO, DATA, IS_MAC, LABELS, MANUAL, PASSING_ROUTES, SUPPORTED_WIN_BROWSERS,
    AlreadyRunningError, Controller, Report, Target,
    app_report, block_reason, build_active_targets, detect_paths, detected_proxy_apps,
    focus_app, launch, live_status, parse_custom_targets, parse_port, proxy_preferences,
    read_settings, requires_tun, save_settings, serialize_custom_targets, system_proxy_port,
)
import macos
import proxies
import relay
from repair import prepare_apps
import tray
from widgets import (
    AMBER, BLUE, DEFAULTS, EMERALD, FILTERS, FONT, MUTED_KEY, PROBE_LABELS, ROSE,
    TEAL, TEAL_DARK, THEMES, AppCard, HealthRing, PillButton, StatusChip, ToggleSwitch,
    enable_dpi_awareness, visual_for_target,
)


class LauncherWindow:
    def __init__(self, root: tk.Tk, *, enable_tray: bool = True, start_minimized: bool = False):
        self.root = root
        self.scale = 1.0 if IS_MAC else max(1.0, min(1.5, root.winfo_fpixels("1i") / 96.0))
        self.settings = DATA / "settings.json"
        saved = read_settings(self.settings)

        self.theme_name = saved.get("theme") if saved.get("theme") in THEMES else "light"
        self.minimize_to_tray = tk.BooleanVar(value=saved.get("minimize_to_tray") is True)
        self.relay_enabled = saved.get("relay_enabled") is True
        self.skip_guide_on_start = saved.get("skip_guide_on_start") is True
        self._guide_dialog: tk.Toplevel | None = None
        self._startup_started = False
        self.relay = relay.LocalProxyRelay(relay.DEFAULT_RELAY_PORT)

        self.custom_targets, self.enabled_presets, self.hidden_targets = parse_custom_targets(saved)
        self.targets: dict[str, Target] = build_active_targets(self.custom_targets, self.enabled_presets, self.hidden_targets)

        self.paths = detect_paths()
        saved_paths = saved.get("paths", {})
        if isinstance(saved_paths, dict):
            for key, value in saved_paths.items():
                if isinstance(key, str) and isinstance(value, str) and value:
                    self.paths[key] = Path(value)

        self.provider, self.ports = proxy_preferences(saved)
        self.port = tk.StringVar(value=self.ports.get(self.provider, ""))
        self.active_provider = ""
        self.known_providers: list[str] = []
        self.active_filter = "all"
        self.log_expanded = False
        saved_controller = saved.get("clash_controller_port")
        self.clash_controller_port = saved_controller if type(saved_controller) is int and 1 <= saved_controller <= 65535 else None
        self.clash_secret = ""

        checked = saved.get("selected", DEFAULTS)
        if not isinstance(checked, (list, tuple)):
            checked = DEFAULTS
        self.selected: dict[str, tk.BooleanVar] = {key: tk.BooleanVar(value=key in checked) for key in self.targets}
        self.auto = tk.BooleanVar(value=saved.get("auto") is True)

        self.details: dict[str, tk.StringVar] = {key: tk.StringVar(value=t.description) for key, t in self.targets.items()}
        self.actual: dict[str, tk.StringVar] = {key: tk.StringVar(value="实时 · 检查中") for key in self.targets}
        self.card_status: dict[str, tuple[str, str]] = {key: ("待检查", MUTED_KEY) for key in self.targets}
        self.latest_routes: dict[str, list[str]] = {}
        self.running_set: set[str] = set()
        self.last_available: list[Report] = []
        self.last_detected: tuple[str, ...] = ()
        self.last_merged_probes: dict = {}
        self.last_conflicts: list[envcheck.EnvConflict] = []
        self.last_health: list[envcheck.HealthFinding] = []
        self.last_log_lines: list[tuple[str, str]] = []
        self.last_ring = (0.0, "#38BDF8", "—", "待检测")
        self._last_wall_tick = time.time()
        self._last_sys_proxy = system_proxy_port()

        self.cards: dict[str, AppCard] = {}
        self.tags: dict[str, AppCard] = self.cards
        self.filter_buttons: dict[str, PillButton] = {}
        self.controls: list = []
        self.results: queue.Queue = queue.Queue()
        self.busy = False
        self._socks_bridge_active = False
        self._skip_save_on_exit = False
        self.container: tk.Frame | None = None
        self.tray_icon: tray.SystemTray | None = None

        if self.relay_enabled and enable_tray:
            try:
                self.relay.start()
            except OSError:
                self.relay_enabled = False

        self._init_window()
        self.build_ui()
        self.refresh_provider_choices(tuple(detected_proxy_apps()))
        self.update_paths()
        self.update_selected_counter()
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_close)

        if enable_tray and tray.SystemTray.supported:
            icon = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "launcher.ico"
            self.tray_icon = tray.SystemTray(
                "AI 代理启动器",
                icon if icon.is_file() else None,
                on_show=lambda: self.root.after(0, self.restore_from_tray),
                on_launch=lambda: self.root.after(0, lambda: self._tray_action(True)),
                on_check=lambda: self.root.after(0, lambda: self._tray_action(False)),
                on_exit=lambda: self.root.after(0, self.force_exit),
            )
            self.tray_icon.start()
            if start_minimized:
                self.root.after(50, self.hide_to_tray)

    @property
    def p(self) -> dict[str, str]:
        return THEMES[self.theme_name]

    def px(self, value: float) -> int:
        return max(1, int(round(value * self.scale)))

    def _init_window(self) -> None:
        root = self.root
        root.title("AI 代理启动器")
        icon = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "launcher.ico"
        if sys.platform == "win32" and icon.is_file():
            root.iconbitmap(default=str(icon))
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        win_w = min(self.px(1050), sw - 48)
        win_h = min(self.px(840), sh - 70)
        root.minsize(min(self.px(840), sw - 60), min(self.px(670), sh - 90))
        root.geometry(f"{win_w}x{win_h}")
        root.option_add("*Font", (FONT, 10))

    def _apply_ttk_styles(self) -> None:
        p = self.p
        self.root.configure(bg=p["canvas"])
        self.root.option_add("*TCombobox*Listbox.background", p["hero_card"])
        self.root.option_add("*TCombobox*Listbox.foreground", p["hero_text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", TEAL)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Dark.TCombobox", padding=(8, 4), fieldbackground=p["hero_card"], background=p["hero_card"],
            foreground=p["hero_text"], arrowcolor="#CBD5E1", bordercolor=p["hero_border"],
            lightcolor=p["hero_border"], darkcolor=p["hero_border"],
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", p["hero_card"]), ("disabled", p["hero_card_dim"])],
            foreground=[("readonly", p["hero_text"]), ("disabled", "#64748B")],
            background=[("active", "#283548"), ("readonly", p["hero_card"])],
        )
        style.configure(
            "Dark.TEntry", padding=(8, 4), fieldbackground=p["hero_card"], foreground=p["hero_text"],
            insertcolor=p["hero_text"], bordercolor=p["hero_border"],
            lightcolor=p["hero_border"], darkcolor=p["hero_border"],
        )
        style.map("Dark.TEntry", fieldbackground=[("disabled", p["hero_card_dim"])], foreground=[("disabled", "#64748B")])
        style.configure(
            "Dialog.TEntry", padding=(8, 5), fieldbackground=p["surface_alt"], foreground=p["ink"],
            insertcolor=p["ink"], bordercolor=p["border"],
        )
        style.configure(
            "Dialog.TCombobox", padding=(8, 4), fieldbackground=p["surface_alt"], background=p["surface_alt"],
            foreground=p["ink"], bordercolor=p["border"],
        )
        style.configure(
            "Subtle.Vertical.TScrollbar", troughcolor=p["canvas"], background=p["border_hover"],
            bordercolor=p["canvas"], arrowcolor=p["muted"],
        )

    def pill(self, parent, text, command, **kwargs) -> PillButton:
        button = PillButton(parent, text, command, **kwargs)
        self.controls.append(button)
        return button

    def build_ui(self) -> None:
        self._apply_ttk_styles()
        p = self.p
        if self.container is not None:
            self.container.destroy()
        self.controls = []
        self.cards = {}
        self.tags = self.cards
        self.filter_buttons = {}

        self.container = tk.Frame(self.root, bg=p["canvas"])
        self.container.pack(fill="both", expand=True)

        # ── Top Control Center Header ───────────────────────────────────
        hero = tk.Frame(self.container, bg=p["hero_bg"])
        hero.pack(fill="x")
        hero_inner = tk.Frame(hero, bg=p["hero_bg"])
        hero_inner.pack(fill="x", padx=self.px(20), pady=(self.px(13), self.px(13)))

        self.health_ring = HealthRing(hero_inner, p, size=self.px(88))
        self.health_ring.pack(side="left", padx=(0, self.px(16)))
        self.health_ring.set_state(*self.last_ring)

        center = tk.Frame(hero_inner, bg=p["hero_bg"])
        center.pack(side="left", fill="both", expand=True)

        title_row = tk.Frame(center, bg=p["hero_bg"])
        title_row.pack(fill="x")
        tk.Label(title_row, text="AI 代理启动器", font=(FONT, 17, "bold"), bg=p["hero_bg"], fg=p["hero_text"]).pack(side="left")
        self.selected_pill_wrap = tk.Frame(title_row, bg=p["hero_bg"])
        self.selected_pill_wrap.pack(side="left", padx=(self.px(10), 0))

        self.relay_button = PillButton(
            title_row, "⇄ 统一中继", self.toggle_relay,
            bg=p["hero_bg"], fill=p["hero_card"], hover_fill="#283548", fg="#CBD5E1",
            outline=p["hero_border"], hover_outline="#64748B", font_spec=(FONT, 8, "bold"),
            height=self.px(22), pad_x=self.px(9),
            tooltip="开启后所有应用固定走 127.0.0.1:17890 中继端口：\n• 浏览器共用固定资料目录，换代理免重新登录\n• 后台热切换上游代理，无需重启已打开的 AI 应用",
        )
        self.relay_button.pack(side="left", padx=(self.px(7), 0))
        self._update_relay_button()

        theme_label = "☀ 浅色" if self.theme_name == "dark" else "☾ 暗黑"
        self.theme_button = PillButton(
            title_row, theme_label, self.toggle_theme,
            bg=p["hero_bg"], fill=p["hero_card"], hover_fill="#283548", fg="#CBD5E1",
            outline=p["hero_border"], hover_outline="#64748B", font_spec=(FONT, 8, "bold"),
            height=self.px(22), pad_x=self.px(9),
        )
        self.theme_button.pack(side="left", padx=(self.px(6), 0))

        if tray.SystemTray.supported:
            self.tray_button = PillButton(
                title_row, "⬇ 托盘", self.hide_to_tray,
                bg=p["hero_bg"], fill=p["hero_card"], hover_fill="#283548", fg="#CBD5E1",
                outline=p["hero_border"], hover_outline="#64748B", font_spec=(FONT, 8, "bold"),
                height=self.px(22), pad_x=self.px(9),
            )
            self.tray_button.pack(side="left", padx=(self.px(6), 0))

        self.proxy_pills_frame = tk.Frame(center, bg=p["hero_bg"])
        self.proxy_pills_frame.pack(fill="x", pady=(self.px(6), self.px(7)))
        if self.last_available:
            self._render_proxy_pills(self.last_available)
        else:
            self._render_proxy_pills([], "正在准备自动检测本机代理…")

        ctrl_row = tk.Frame(center, bg=p["hero_bg"])
        ctrl_row.pack(fill="x")
        tk.Label(ctrl_row, text="代理模式", fg=p["hero_muted"], bg=p["hero_bg"], font=(FONT, 9)).pack(side="left", padx=(0, self.px(6)))
        self.provider_choice = ttk.Combobox(ctrl_row, values=(AUTO, MANUAL), width=15, state="readonly", style="Dark.TCombobox")
        self.provider_choice.set(self.provider)
        self.provider_choice.bind("<<ComboboxSelected>>", self.change_provider)
        self.provider_choice.pack(side="left")

        self.port_label = tk.Label(ctrl_row, text="端口", fg=p["hero_muted"], bg=p["hero_bg"], font=(FONT, 9))
        self.port_label.pack(side="left", padx=(self.px(8), self.px(4)))
        self.port_entry = ttk.Entry(ctrl_row, width=6, textvariable=self.port, style="Dark.TEntry")
        self.controls.append(self.port_entry)
        self.port_entry.pack(side="left")

        hero_btn_kw = dict(
            bg=p["hero_bg"], fill=p["hero_card"], hover_fill="#283548", fg="#E2E8F0",
            outline=p["hero_border"], hover_outline="#64748B",
            disabled_fill=p["hero_card_dim"], disabled_fg="#64748B",
            height=self.px(27), pad_x=self.px(9), font_spec=(FONT, 8, "bold"),
        )
        self.nodes_button = self.pill(ctrl_row, "切换节点", lambda: dialogs.open_node_switcher(self), **hero_btn_kw)
        self.nodes_button.pack(side="left", padx=(self.px(8), self.px(5)))

        self.env_button = self.pill(ctrl_row, "环境体检", lambda: dialogs.open_env_audit(self), **hero_btn_kw)
        self.env_button.pack(side="left", padx=(0, self.px(5)))

        self.controller_button = self.pill(ctrl_row, "控制接口", lambda: dialogs.edit_controller(self), **hero_btn_kw)
        self.controller_button.pack(side="left", padx=(0, self.px(5)))

        self.proxy_button = self.pill(ctrl_row, "打开代理 ↗", self.open_proxy_app, **hero_btn_kw)
        self.proxy_button.pack(side="left")

        right = tk.Frame(hero_inner, bg=p["hero_bg"])
        right.pack(side="right", fill="y", padx=(self.px(12), 0))

        top_right_row = tk.Frame(right, bg=p["hero_bg"])
        top_right_row.pack(anchor="e", pady=(0, self.px(6)))
        self.guide_button = PillButton(
            top_right_row, "? 使用说明", self.open_guide,
            bg=p["hero_bg"], fill=p["hero_card"], hover_fill="#283548", fg="#CBD5E1",
            outline=p["hero_border"], hover_outline=TEAL, font_spec=(FONT, 8, "bold"),
            height=self.px(22), pad_x=self.px(10),
            tooltip="打开使用说明书（随时可反复查阅与设置启动弹出偏好）",
        )
        self.guide_button.pack(side="right")
        self.rollback_button = PillButton(
            top_right_row, "↺ 撤销修改", self.open_rollback,
            bg=p["hero_bg"], fill=p["hero_card"], hover_fill="#283548", fg="#CBD5E1",
            outline=p["hero_border"], hover_outline=AMBER, font_spec=(FONT, 8, "bold"),
            height=self.px(22), pad_x=self.px(10),
            tooltip="查看并一键撤销本软件对外部配置文件、环境变量、开机自启及代理状态的修改",
        )
        self.rollback_button.pack(side="right", padx=(0, self.px(6)))
        self._update_rollback_badge()

        btn_row = tk.Frame(right, bg=p["hero_bg"])
        btn_row.pack(anchor="e")
        self.check_button = self.pill(
            btn_row, "↻ 检查全部", lambda: self.begin(list(self.targets), False),
            bg=p["hero_bg"], fill=p["hero_card"], hover_fill="#283548", fg="#E2E8F0",
            outline=p["hero_border"], hover_outline="#64748B",
            disabled_fill=p["hero_card_dim"], disabled_fg="#64748B",
            height=self.px(35), pad_x=self.px(12),
        )
        self.check_button.pack(side="left", padx=(0, self.px(8)))

        self.launch_button = self.pill(
            btn_row, "▶ 检查修复并打开", lambda: self.begin(self.selected_keys(), True),
            bg=p["hero_bg"], fill=TEAL, hover_fill=TEAL_DARK, fg="#FFFFFF",
            outline="", hover_outline="", disabled_fill="#134E4A", disabled_fg="#5EEAD4",
            font_spec=(FONT, 10, "bold"), height=self.px(35), pad_x=self.px(15),
        )
        self.launch_button.pack(side="left")

        auto_row = tk.Frame(right, bg=p["hero_bg"])
        auto_row.pack(anchor="e", pady=(self.px(7), 0))
        tk.Label(auto_row, text="启动时自动运行勾选项", fg=p["hero_muted"], bg=p["hero_bg"], font=(FONT, 8)).pack(side="left", padx=(0, self.px(8)))
        self.auto_switch = ToggleSwitch(auto_row, self.auto, command=self.save, bg=p["hero_bg"], width=self.px(38), height=self.px(20))
        self.controls.append(self.auto_switch)
        self.auto_switch.pack(side="left")

        self.update_proxy_controls()

        # ── Main Body ───────────────────────────────────────────────────
        body = tk.Frame(self.container, bg=p["canvas"])
        body.pack(fill="both", expand=True, padx=self.px(18), pady=(self.px(10), self.px(12)))

        toolbar = tk.Frame(body, bg=p["canvas"])
        toolbar.pack(fill="x", pady=(0, self.px(6)))

        filter_bar = tk.Frame(toolbar, bg=p["canvas"])
        filter_bar.pack(side="left")
        for fkey, label in FILTERS:
            count = len(self.targets) if fkey == "all" else sum(
                1 for k, t in self.targets.items() if visual_for_target(k, t)[3] == fkey
            )
            active = fkey == self.active_filter
            btn = PillButton(
                filter_bar, f"{label} ({count})", lambda current=fkey: self.set_filter(current),
                bg=p["canvas"],
                fill=TEAL if active else p["surface"],
                hover_fill=TEAL_DARK if active else p["surface_hover"],
                fg="#FFFFFF" if active else p["muted"],
                outline="" if active else p["border"],
                hover_outline="" if active else p["border_hover"],
                font_spec=(FONT, 9, "bold" if active else "normal"),
                height=self.px(28), pad_x=self.px(11),
            )
            btn.pack(side="left", padx=(0, self.px(6)))
            self.filter_buttons[fkey] = btn

        quick_bar = tk.Frame(toolbar, bg=p["canvas"])
        quick_bar.pack(side="right")
        tb_kw = dict(
            bg=p["canvas"], fill=p["surface"], hover_fill=p["surface_hover"],
            outline=p["border"], hover_outline=p["border_hover"],
            disabled_fill=p["disabled_bg"], disabled_fg=p["disabled_fg"],
            height=self.px(28), pad_x=self.px(10),
        )
        self.browser_button = self.pill(quick_bar, "浏览器 · 选择", lambda: self.choose("web"), fg=p["ink"], **tb_kw)
        self.browser_button.pack(side="right")

        self.manage_button = self.pill(quick_bar, "+ 管理应用", lambda: dialogs.open_app_manager(self), fg=TEAL, **tb_kw)
        self.manage_button.pack(side="right", padx=(0, self.px(6)))

        self.pill(quick_bar, "清空", lambda: self.set_selection(()), fg=p["muted"], **tb_kw).pack(side="right", padx=(0, self.px(6)))
        self.pill(quick_bar, "全选", lambda: self.set_selection(tuple(self.targets)), fg=p["muted"], **tb_kw).pack(side="right", padx=(0, self.px(6)))

        # ── Visual App Cards Grid ───────────────────────────────────────
        grid_wrap = tk.Frame(body, bg=p["canvas"])
        grid_wrap.pack(fill="both", expand=True)

        self.grid_canvas = tk.Canvas(grid_wrap, bg=p["canvas"], highlightthickness=0, bd=0, yscrollincrement=28)
        self.grid_scroll = ttk.Scrollbar(grid_wrap, orient="vertical", command=self.grid_canvas.yview, style="Subtle.Vertical.TScrollbar")
        self.grid_canvas.configure(yscrollcommand=self.grid_scroll.set)
        self.grid_canvas.pack(side="left", fill="both", expand=True)
        self.grid_scroll.pack(side="right", fill="y")

        self.cards_frame = tk.Frame(self.grid_canvas, bg=p["canvas"])
        self._grid_win = self.grid_canvas.create_window(0, 0, anchor="nw", window=self.cards_frame)
        self.cards_frame.bind("<Configure>", self._sync_grid_scroll)
        self.grid_canvas.bind("<Configure>", self._on_grid_resize)

        for key, target in self.targets.items():
            brand_color, icon_kind, category, _group = visual_for_target(key, target)
            has_path = target.kind in {"electron", "cli"} or (IS_MAC and target.kind in {"store", "codex"})
            self.selected.setdefault(key, tk.BooleanVar(value=False))
            detail_var = self.details.setdefault(key, tk.StringVar(value=target.description))
            actual_var = self.actual.setdefault(key, tk.StringVar(value="实时 · 检查中"))
            card = AppCard(
                self.cards_frame, key, target.name,
                palette=p, brand_color=brand_color, icon_kind=icon_kind, category=category,
                selected_var=self.selected[key], has_path_button=has_path,
                on_toggle=self._on_card_toggled, on_open=lambda k: self.begin([k], True),
                on_choose_path=self.choose, scale=self.scale,
            )
            st_text, st_color = self.card_status.get(key, ("待检查", MUTED_KEY))
            card.set_status(st_text, st_color)
            card.set_running(key in self.running_set)
            card.set_detail(detail_var.get())
            card.set_actual(actual_var.get())
            card.bind("<MouseWheel>", self._on_mousewheel)
            self.cards[key] = card

        self.cards_frame.bind("<MouseWheel>", self._on_mousewheel)
        self.grid_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._layout_cards()

        # ── Bottom Visual Probe Matrix + Diagnostics Panel ──────────────
        diag_shell = tk.Frame(body, bg=p["surface"], highlightthickness=1, highlightbackground=p["border"])
        diag_shell.pack(fill="x", pady=(self.px(8), 0))

        diag_head = tk.Frame(diag_shell, bg=p["surface"])
        diag_head.pack(fill="x", padx=self.px(12), pady=(self.px(8), self.px(5)))
        self.heading = tk.Label(diag_head, text="节点与服务状态", bg=p["surface"], fg=p["ink"], font=(FONT, 9, "bold"))
        self.heading.pack(side="left")

        self.detected_summary = tk.Label(diag_head, text="", bg=p["surface"], fg=p["muted"], font=(FONT, 8))
        self.detected_summary.pack(side="left", padx=(self.px(10), 0))
        if self.last_detected:
            self.detected_summary.configure(text=f"本机发现 · {'、'.join(self.last_detected)}")

        self.expand_button = PillButton(
            diag_head, "收起日志 ▴" if self.log_expanded else "展开日志 ▾", self.toggle_log_height,
            bg=p["surface"], fill=p["surface_alt"], hover_fill=p["surface_hover"], fg=p["muted"],
            outline=p["border"], hover_outline=p["border_hover"], font_spec=(FONT, 8),
            height=self.px(22), pad_x=self.px(10),
        )
        self.expand_button.pack(side="right")

        self.probe_chips_frame = tk.Frame(diag_shell, bg=p["surface"])
        self.probe_chips_frame.pack(fill="x", padx=self.px(12), pady=(0, self.px(6)))
        self._render_probe_chips(self.last_merged_probes)

        log_area = tk.Frame(diag_shell, bg=p["surface_alt"])
        log_area.pack(fill="x", padx=self.px(10), pady=(0, self.px(8)))
        log_area.grid_columnconfigure(0, weight=1)
        self.log = tk.Text(
            log_area, height=9 if self.log_expanded else 4, borderwidth=0, bg=p["surface_alt"], fg=p["muted"],
            font=(FONT, 8), wrap="word", padx=self.px(8), pady=self.px(5), highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(log_area, orient="vertical", command=self.log.yview, style="Subtle.Vertical.TScrollbar")
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log.tag_config("good", foreground=p["teal_ink"])
        self.log.tag_config("error", foreground=p["rose_ink"])
        self.clear_log(keep_history=True)
        if self.last_log_lines:
            for text, tag in self.last_log_lines:
                self._append_log_widget(text, tag)
        else:
            self.write("点击任意卡片可勾选/取消；悬停服务圆点可看命中节点与延迟，开启「统一中继」可换代理免重启、网页免重登。")

        self.refresh_provider_choices(self.last_detected)
        self.update_selected_counter()
        self._update_browser_button()
        if not self.last_available:
            self.update_paths()
        self._update_env_badge()

    # ── Local Stable Proxy Relay ────────────────────────────────────────

    def _update_relay_button(self) -> None:
        if not hasattr(self, "relay_button"):
            return
        p = self.p
        if (self.relay_enabled or self._socks_bridge_active) and self.relay.running and self.relay.port:
            active, total, upstream = self.relay.stats()
            label = f"⇄ 中继 :{self.relay.port}" if self.relay_enabled else f"⇄ SOCKS5桥 :{self.relay.port}"
            self.relay_button.set_text(label)
            self.relay_button.set_palette(
                fill="#0F3430", hover_fill="#134E4A", fg="#5EEAD4",
                outline=TEAL, hover_outline="#2DD4BF",
            )
            if self.relay_button.tooltip:
                up = f"127.0.0.1:{upstream}" if upstream else "等待可用代理"
                mode_desc = "统一本地中继运行中" if self.relay_enabled else "已自动启用 SOCKS5 → HTTP 本地桥接"
                self.relay_button.tooltip.set_text(
                    f"{mode_desc}（127.0.0.1:{self.relay.port} → {up}）\n"
                    f"已转发 {total} 次请求 · 当前活跃 {active} 连接\n"
                    "关闭窗口会自动驻留托盘以保持中继不断开；再次点击可切换统一中继。"
                )
        else:
            self.relay_button.set_text("⇄ 统一中继")
            self.relay_button.set_palette(
                fill=p["hero_card"], hover_fill="#283548", fg="#CBD5E1",
                outline=p["hero_border"], hover_outline="#64748B",
            )
            if self.relay_button.tooltip:
                self.relay_button.tooltip.set_text(
                    "开启后所有应用固定走 127.0.0.1:17890 中继端口：\n"
                    "• 浏览器共用唯一资料目录，切换代理软件无需重新登录\n"
                    "• 后台按域名自动热切换上游代理（同时支持 HTTP 与 SOCKS5），免重启已打开的应用"
                )

    @staticmethod
    def _report_upstream(report: Report) -> int | tuple[str, int]:
        return ("socks5", report.port) if report.scheme == "socks5" else report.port

    def _sync_relay_routes(self, available: list[Report], preferred: int | None) -> None:
        port_reports = [r for r in available if not r.issue and r.port > 0]
        needs_socks_bridge = any(r.scheme == "socks5" for r in port_reports)
        if needs_socks_bridge and not self.relay.running:
            try:
                self.relay.start()
                self._socks_bridge_active = True
            except OSError:
                self._socks_bridge_active = False
        elif not self.relay_enabled and not needs_socks_bridge and self._socks_bridge_active:
            self.relay.stop()
            self._socks_bridge_active = False
        if not self.relay.running:
            self._update_relay_button()
            return
        if not port_reports:
            self.relay.update_routes(None, {}, [])
            self._update_relay_button()
            return
        host_upstreams: dict[str, int | tuple[str, int]] = {}
        for target in self.targets.values():
            best = app_report(target, port_reports, preferred)
            if best and not best.issue and best.port > 0:
                spec = self._report_upstream(best)
                for host in target.hosts:
                    host_upstreams[host.split("/", 1)[0]] = spec
        ordered = sorted(
            port_reports,
            key=lambda r: (
                not proxies.is_restricted_region(r.region),
                r.port == preferred,
                r.endpoint is not None,
                r.tun is True,
            ),
            reverse=True,
        )
        default_up = self._report_upstream(ordered[0])
        fallbacks = [self._report_upstream(r) for r in ordered[1:]]
        self.relay.update_routes(default_up, host_upstreams, fallbacks)
        self._update_relay_button()

    def toggle_relay(self) -> None:
        if self.busy:
            return
        if self.relay_enabled:
            self.relay_enabled = False
            needs_socks = any(r.scheme == "socks5" and r.port > 0 and not r.issue for r in self.last_available)
            if needs_socks and self.relay.running:
                self._socks_bridge_active = True
                self.write("已关闭统一中继模式；因当前使用 SOCKS5 代理，本地桥接端口将保持运行。")
            else:
                self._socks_bridge_active = False
                self.relay.stop()
                self.write("已关闭统一本地中继，后续打开的应用将直连代理软件实际端口。")
        else:
            try:
                port = self.relay.start()
                self.relay_enabled = True
                self._socks_bridge_active = False
                self._sync_relay_routes(self.last_available, system_proxy_port())
                self.write(f"已启动统一本地中继 127.0.0.1:{port}（网页将共用持久资料免重登，换代理可后台热切换）。", "good")
            except OSError as err:
                self.write(f"无法启动统一本地中继：{err}", "error")
                self.relay_enabled = False
        self._update_relay_button()
        self.save()

    # ── Theme & Tray ────────────────────────────────────────────────────

    def toggle_theme(self) -> None:
        if self.busy:
            return
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.save()
        self.build_ui()

    def hide_to_tray(self) -> None:
        if self.tray_icon and self.tray_icon.start():
            self.root.withdraw()

    def open_guide(self, *, startup_gate: bool = False, on_acknowledged=None) -> tk.Toplevel:
        return dialogs.open_user_guide(self, startup_gate=startup_gate, on_acknowledged=on_acknowledged)

    def open_rollback(self) -> tk.Toplevel:
        return dialogs.open_rollback_dialog(self)

    def _update_rollback_badge(self) -> None:
        if not hasattr(self, "rollback_button"):
            return
        try:
            items = envcheck.list_rollback_items(data_dir=self.settings.parent, include_runtime=True)
            active_count = sum(1 for it in items if it.category != "autostart")
        except Exception:
            active_count = 0
        p = self.p
        if active_count > 0:
            self.rollback_button.set_text(f"↺ 撤销修改 ({active_count})")
            self.rollback_button.set_palette(
                fill="#451A03", hover_fill="#78350F", fg="#FDE68A",
                outline="#D97706", hover_outline="#F59E0B",
            )
        else:
            self.rollback_button.set_text("↺ 撤销修改")
            self.rollback_button.set_palette(
                fill=p["hero_card"], hover_fill="#283548", fg="#CBD5E1",
                outline=p["hero_border"], hover_outline=AMBER,
            )

    def reset_software_state(self, *, clear_browser: bool = False) -> list[str]:
        msgs: list[str] = []
        if self.relay.running or self.relay_enabled:
            self.relay_enabled = False
            self._socks_bridge_active = False
            self.relay.stop()
            self._update_relay_button()
            msgs.append("已停止统一本地中继")
        msgs.extend(envcheck.reset_app_data(self.settings.parent, clear_browser=clear_browser))
        self.theme_name = "light"
        self.provider = AUTO
        self.ports = {"JImmm": "", "Clash": ""}
        self.port.set("")
        self.paths = detect_paths()
        self.clash_controller_port = None
        self.clash_secret = ""
        self.enabled_presets = []
        self.hidden_targets = set()
        self.custom_targets = {}
        self.active_filter = "all"
        self.skip_guide_on_start = False
        self.auto.set(False)
        self.minimize_to_tray.set(False)
        self.targets = build_active_targets(self.custom_targets, self.enabled_presets, self.hidden_targets)
        self.selected = {key: tk.BooleanVar(value=key in DEFAULTS) for key in self.targets}
        self.details = {key: tk.StringVar(value=t.description) for key, t in self.targets.items()}
        self.actual = {key: tk.StringVar(value="实时 · 检查中") for key in self.targets}
        self.card_status = {key: ("待检查", MUTED_KEY) for key in self.targets}
        self._skip_save_on_exit = True
        self.build_ui()
        self.update_paths()
        self.update_selected_counter()
        for msg in msgs:
            self.write(f"重置软件数据 · {msg}", "good")
        return msgs

    def start_background_tasks(self) -> None:
        """Start periodic live-route polling and initial proxy check/auto-launch once ready."""
        if self._startup_started:
            return
        self._startup_started = True
        self.root.after(100, self.refresh_actual)
        if self.auto.get() and self.selected_keys():
            self.root.after(300, lambda: self.begin(self.selected_keys(), True))
        else:
            self.root.after(300, lambda: self.begin(list(self.targets), False))

    def restore_from_tray(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        if self._guide_dialog is not None:
            try:
                if self._guide_dialog.winfo_exists():
                    self._guide_dialog.lift()
                    self._guide_dialog.focus_force()
            except tk.TclError:
                pass

    def _tray_action(self, opening: bool) -> None:
        self.restore_from_tray()
        keys = self.selected_keys() if opening else list(self.targets)
        if keys:
            self.begin(keys, opening)

    def on_window_close(self) -> None:
        # Keep running in tray if user enabled minimize_to_tray OR active local relay/SOCKS5 bridge needs to stay alive
        if (self.minimize_to_tray.get() or ((self.relay_enabled or self._socks_bridge_active) and self.relay.running)) and self.tray_icon:
            self.hide_to_tray()
            return
        self.force_exit()

    def force_exit(self) -> None:
        if self.busy:
            self.restore_from_tray()
            self.write("检查或修复仍在进行，请等待完成。")
            return
        if not self._skip_save_on_exit:
            self.save()
        self.relay.stop()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()

    # ── Visual Helpers ──────────────────────────────────────────────────

    def _on_mousewheel(self, event) -> None:
        if self.grid_canvas.yview() != (0.0, 1.0):
            delta = -1 if event.delta > 0 else 1
            self.grid_canvas.yview_scroll(delta, "units")

    def _sync_grid_scroll(self, _event=None) -> None:
        bbox = self.grid_canvas.bbox("all")
        if bbox:
            self.grid_canvas.configure(scrollregion=bbox)
        need_scroll = bool(bbox and bbox[3] > self.grid_canvas.winfo_height() + 4)
        if need_scroll:
            if not self.grid_scroll.winfo_ismapped():
                self.grid_scroll.pack(side="right", fill="y")
        else:
            if self.grid_scroll.winfo_ismapped():
                self.grid_scroll.pack_forget()

    def _on_grid_resize(self, event) -> None:
        self.grid_canvas.itemconfigure(self._grid_win, width=event.width)
        self._sync_grid_scroll()

    def _layout_cards(self) -> None:
        for card in self.cards.values():
            card.grid_forget()
        visible = [
            key for key, target in self.targets.items()
            if self.active_filter == "all" or visual_for_target(key, target)[3] == self.active_filter
        ]
        cols = 3
        for col in range(cols):
            self.cards_frame.grid_columnconfigure(col, weight=1, uniform="cardcol")
        pad = self.px(5)
        for idx, key in enumerate(visible):
            row, col = divmod(idx, cols)
            self.cards[key].grid(row=row, column=col, sticky="nsew", padx=pad, pady=pad)
        self.cards_frame.update_idletasks()
        self._sync_grid_scroll()

    def set_filter(self, filter_key: str) -> None:
        p = self.p
        self.active_filter = filter_key
        for fkey, btn in self.filter_buttons.items():
            active = fkey == filter_key
            btn.set_palette(
                fill=TEAL if active else p["surface"],
                hover_fill=TEAL_DARK if active else p["surface_hover"],
                fg="#FFFFFF" if active else p["muted"],
                outline="" if active else p["border"],
                hover_outline="" if active else p["border_hover"],
            )
        self._layout_cards()

    def set_selection(self, keys: tuple[str, ...]) -> None:
        wanted = set(keys)
        for key, var in self.selected.items():
            if key in self.targets:
                var.set(key in wanted)
        self._on_card_toggled()

    def _on_card_toggled(self) -> None:
        self.update_selected_counter()
        self.save()

    def update_selected_counter(self) -> None:
        p = self.p
        count = len(self.selected_keys())
        for child in self.selected_pill_wrap.winfo_children():
            child.destroy()
        StatusChip(
            self.selected_pill_wrap, f"已选 {count} 项",
            bg=p["hero_bg"], fill=p["hero_card"], fg="#5EEAD4" if count else p["hero_muted"],
            dot="#2DD4BF" if count else "#475569", outline=p["hero_border"],
            font_size=8, bold=True, height=self.px(22), pad_x=self.px(9),
        ).pack()

    def toggle_log_height(self) -> None:
        self.log_expanded = not self.log_expanded
        self.log.configure(height=9 if self.log_expanded else 4)
        self.expand_button.set_text("收起日志 ▴" if self.log_expanded else "展开日志 ▾")

    def _click_proxy_pill(self, provider_name: str) -> None:
        if self.busy:
            return
        next_provider = AUTO if self.provider == provider_name else provider_name
        self.refresh_provider_choices((provider_name,))
        self.provider_choice.set(next_provider)
        self.change_provider()

    def _render_proxy_pills(self, reports: list[Report], fallback_text: str = "", error: bool = False) -> None:
        p = self.p
        for child in self.proxy_pills_frame.winfo_children():
            child.destroy()
        if not reports:
            StatusChip(
                self.proxy_pills_frame, fallback_text,
                bg=p["hero_bg"],
                fill="#3B1822" if error else p["hero_card"],
                fg="#FDA4AF" if error else "#CBD5E1",
                dot=ROSE if error else "#38BDF8",
                outline="#881337" if error else p["hero_border"],
                font_size=9, height=self.px(26), pad_x=self.px(11),
            ).pack(side="left")
            return
        for report in reports[:4]:
            passing = sum(1 for pr in report.probes.values() if pr.connected and pr.route in PASSING_ROUTES)
            total = max(1, len(report.probes))
            restricted = proxies.is_restricted_region(report.region)
            dot = AMBER if restricted else (EMERALD if passing == total else (AMBER if passing > 0 else ROSE))
            region_txt = proxies.region_label(report.region)
            meta_parts = []
            if region_txt:
                meta_parts.append(f"{region_txt}·受限" if restricted else region_txt)
            if report.latency_ms is not None:
                meta_parts.append(f"{report.latency_ms}ms")
            meta_str = " · ".join(meta_parts)

            if report.scheme == "socks5":
                badge, badge_bg, badge_fg = "SOCKS5", "#FDE68A", "#78350F"
            elif report.tun:
                badge, badge_bg, badge_fg = "TUN", "#6EE7B7", "#064E3B"
            elif report.endpoint is None:
                badge, badge_bg, badge_fg = "HTTP", "#BAE6FD", "#0C4A6E"
            else:
                badge, badge_bg, badge_fg = "API", "#C7D2FE", "#312E81"
            pinned = self.provider == report.provider
            port_text = f":{report.port}" if report.port else ""
            if report.scheme == "socks5" and report.port:
                head_tip = f"{report.provider}  socks5://127.0.0.1:{report.port}（已自动桥接为本地 HTTP 中继）"
            elif report.port:
                head_tip = f"{report.provider}  127.0.0.1:{report.port}"
            else:
                head_tip = f"{report.provider}  系统路由 / 透明代理直通（无需本地端口）"
            tip_lines = [head_tip]
            if region_txt:
                tip_lines.append(f"AI 出口地区：{region_txt}（{report.region}）" + (" — 部分 AI 平台可能限制该地区" if restricted else ""))
            if report.latency_ms is not None:
                tip_lines.append(f"参考延迟：{report.latency_ms} ms")
            tip_lines.append("点击恢复「自动检测」" if pinned else "点击锁定使用此代理并复测")
            StatusChip(
                self.proxy_pills_frame, report.provider,
                bg=p["hero_bg"], fill=p["hero_card"], fg=p["hero_text"], dot=dot,
                outline="#2DD4BF" if pinned else p["hero_border"],
                secondary=port_text, secondary_fg="#7DD3FC",
                meta=meta_str, meta_fg="#FBBF24" if restricted else "#94A3B8",
                badge=badge, badge_bg=badge_bg, badge_fg=badge_fg,
                font_size=9, bold=True, height=self.px(26), pad_x=self.px(10),
                on_click=lambda name=report.provider: self._click_proxy_pill(name),
                tooltip="\n".join(tip_lines),
            ).pack(side="left", padx=(0, self.px(8)))

    def _render_probe_chips(self, probes_map: dict) -> None:
        p = self.p
        for child in self.probe_chips_frame.winfo_children():
            child.destroy()
        if probes_map:
            items = list(probes_map.items())
        else:
            default_hosts = list(dict.fromkeys(h for t in self.targets.values() for h in t.hosts))
            items = [(h, None) for h in default_hosts[:12]]
        row = tk.Frame(self.probe_chips_frame, bg=p["surface"])
        row.pack(fill="x")
        per_row = 6
        for idx, (host, info) in enumerate(items[:18]):
            if idx > 0 and idx % per_row == 0:
                row = tk.Frame(self.probe_chips_frame, bg=p["surface"])
                row.pack(fill="x", pady=(self.px(4), 0))
            label = PROBE_LABELS.get(host, host.split("/")[0])
            probe = info[0] if isinstance(info, tuple) else info
            provider_label = info[1] if isinstance(info, tuple) else ""
            if probe is None:
                dot, fill, fg = p["subtle"], p["surface_alt"], p["muted"]
                tip = f"{host}\n状态：待检测"
            elif probe.connected and probe.route in PASSING_ROUTES:
                dot, fill, fg = EMERALD, p["teal_pale"], p["teal_ink"]
                lat = f" · {probe.latency_ms}ms" if probe.latency_ms is not None else ""
                lines = [host, f"线路：{provider_label} · {LABELS[probe.route]}{lat}"]
                if probe.node or probe.region:
                    reg = proxies.region_label(probe.region)
                    node_info = probe.node + (f"（{reg}）" if reg and reg not in probe.node else "") if probe.node else reg
                    lines.append(f"命中节点：{node_info}")
                lines.append(f"详情：{probe.detail}")
                tip = "\n".join(lines)
            elif probe.route == "direct":
                dot, fill, fg = AMBER, p["amber_soft"], p["amber_ink"]
                tip = f"{host}\n线路：{provider_label} · 直连（需调整代理规则）\n详情：{probe.detail}"
            else:
                dot, fill, fg = ROSE, p["rose_soft"], p["rose_ink"]
                tip = f"{host}\n线路：{provider_label} · {LABELS[probe.route]}\n详情：{probe.detail}"
            StatusChip(
                row, label,
                bg=p["surface"], fill=fill, fg=fg, dot=dot,
                font_size=8, height=self.px(22), pad_x=self.px(9), tooltip=tip,
            ).pack(side="left", padx=(0, self.px(6)))

    def _update_env_badge(self) -> None:
        self._update_rollback_badge()
        issues = len(self.last_conflicts) + sum(1 for h in self.last_health if h.status in {"warn", "error"})
        if issues > 0:
            self.env_button.set_text(f"环境体检 ({issues})")
            self.env_button.set_palette(
                fill="#451A03", hover_fill="#78350F", fg="#FDE68A",
                outline="#D97706", hover_outline="#F59E0B",
            )
        else:
            p = self.p
            self.env_button.set_text("环境体检")
            self.env_button.set_palette(
                fill=p["hero_card"], hover_fill="#283548", fg="#E2E8F0",
                outline=p["hero_border"], hover_outline="#64748B",
            )

    # ── Core Launcher Behavior ──────────────────────────────────────────

    def refresh_provider_choices(self, discovered: tuple[str, ...] = ()):
        items: list[str] = [AUTO]
        for name in (*discovered, *self.known_providers):
            if name and name not in {AUTO, MANUAL} and name not in items:
                items.append(name)
        if self.provider not in {AUTO, MANUAL} and self.provider not in items:
            items.append(self.provider)
        items.append(MANUAL)
        self.known_providers = [name for name in items if name not in {AUTO, MANUAL}]
        self.provider_choice.configure(values=tuple(items))
        self.provider_choice.set(self.provider)

    def _update_browser_button(self) -> None:
        browser = self.paths.get("web")
        if browser and (browser.is_file() or (IS_MAC and browser.is_dir())):
            bname = SUPPORTED_WIN_BROWSERS.get(browser.name.casefold(), browser.stem)
            short = bname.replace("Microsoft ", "").replace("Google ", "").replace(" Browser", "")
            self.browser_button.set_text(f"浏览器 · {short}")
        else:
            self.browser_button.set_text("浏览器 · 选择")

    def update_paths(self):
        self._update_browser_button()
        for key, target in self.targets.items():
            path_key = "web" if target.kind == "web" else key
            path = self.paths.get(path_key)
            if IS_MAC and path and (path.is_dir() or path.is_file()):
                text = f"{path.stem} · {'独立代理窗口' if target.kind == 'web' else target.description}"
            elif target.kind == "web" and path and path.is_file():
                bname = SUPPORTED_WIN_BROWSERS.get(path.name.casefold(), path.stem).replace("Microsoft ", "").replace("Google ", "")
                text = f"{bname} · 独立代理窗口"
            elif (not IS_MAC and target.kind == "store") or (path and path.is_file()):
                text = target.description
            else:
                text = "请选择浏览器" if target.kind == "web" else "未找到程序，点「路径」选择"
            self.details[key].set(text)
            if key in self.cards:
                self.cards[key].set_detail(text)

    def choose(self, key: str):
        current = self.paths.get(key)
        name = "Chromium 内核浏览器（Edge/Chrome/Brave/Arc/Vivaldi）" if key == "web" else self.targets[key].name
        if IS_MAC:
            picked = filedialog.askopenfilename(parent=self.root, title=f"选择 {name}",
                                                initialdir=str(current.parent) if current else "/Applications",
                                                filetypes=[("macOS 应用", "*.app"), ("所有文件", "*")] if key != "code" else [])
        else:
            ftypes = [("Windows 程序", "*.exe;*.cmd;*.bat")] if (key in self.targets and self.targets[key].kind == "cli") else [("Windows 程序", "*.exe")]
            picked = filedialog.askopenfilename(parent=self.root, title=f"选择 {name}",
                                                initialdir=str(current.parent) if current else None,
                                                filetypes=ftypes)
        if not picked:
            return
        path = Path(picked)
        if key == "web":
            browsers = {f"{b.casefold()}.app" for b in macos.BROWSERS} if IS_MAC else set(SUPPORTED_WIN_BROWSERS)
            if path.name.casefold() not in browsers:
                self.write("请选择 Edge、Chrome、Brave、Vivaldi、Arc 或 Chromium 浏览器。", "error")
                return
        self.paths[key] = path
        self.update_paths()
        self.save()

    def selected_keys(self) -> list[str]:
        return [key for key in self.targets if key in self.selected and self.selected[key].get()]

    def save(self):
        self._skip_save_on_exit = False
        if self.provider != AUTO:
            self.ports[self.provider] = self.port.get()
        try:
            save_settings(self.settings, {
                "provider": self.provider,
                "automatic_detection": True,
                "ports": self.ports,
                "port": self.ports.get("JImmm", ""),
                "clash_controller_port": self.clash_controller_port,
                "selected": self.selected_keys(),
                "auto": self.auto.get(),
                "theme": self.theme_name,
                "minimize_to_tray": self.minimize_to_tray.get(),
                "relay_enabled": self.relay_enabled,
                "skip_guide_on_start": self.skip_guide_on_start,
                "enabled_presets": self.enabled_presets,
                "hidden_targets": sorted(self.hidden_targets),
                "custom_targets": serialize_custom_targets(self.custom_targets),
                "paths": {key: str(path) for key, path in self.paths.items() if path},
            })
        except OSError:
            self.write("设置保存失败。仍可使用当前窗口启动应用。", "error")

    def change_provider(self, _event=None):
        if self.provider != AUTO:
            self.ports[self.provider] = self.port.get()
        self.provider = self.provider_choice.get()
        self.active_provider = ""
        self.port.set(self.ports.get(self.provider, ""))
        self.update_proxy_controls()
        for key in self.targets:
            self.tag(key, "待检查")
        self.clear_log()
        self._render_proxy_pills([], f"{self.provider} 状态待检查")
        self.last_ring = (0.0, "#38BDF8", "—", "待检查")
        self.health_ring.set_state(*self.last_ring)
        self.save()
        self.update_paths()
        if self.provider != MANUAL or self.port.get().strip():
            self.begin(list(self.targets), False)
        else:
            self.write("请在上方填写本机 HTTP 代理端口（如 7890、10808、12334），然后点击「检查全部」。")

    def update_proxy_controls(self):
        self.port_entry.configure(state="disabled" if self.provider == AUTO else "normal")
        self.port_label.configure(text="HTTP端口*" if self.provider == MANUAL else "端口")

    def open_proxy_app(self):
        apps = detected_proxy_apps()
        provider = self.active_provider if self.provider in {AUTO, MANUAL} else self.provider
        if provider == "Clash":
            provider = next(
                (name for name in ("Clash Verge Rev", "Clash Party", "FlClash", "Clash Nyanpasu",
                                   "ClashX", "Clash for Windows", "Sparkle") if apps.get(name)),
                "Clash",
            )
        selectable = {name: path for name, path in apps.items() if path and name not in proxies.STANDALONE_CORES}
        if not selectable:
            selectable = {name: path for name, path in apps.items() if path}
        if (not provider or provider not in selectable) and len(selectable) > 1:
            menu = tk.Menu(self.root, tearoff=False)
            for name, path in selectable.items():
                menu.add_command(label=name, command=lambda exe=path: self.start_proxy_app(exe))
            menu.post(self.proxy_button.winfo_rootx(), self.proxy_button.winfo_rooty() + self.proxy_button.winfo_height())
            return
        binary = apps.get(provider) if provider else next(iter(selectable.values()), None)
        if binary is None:
            self.write(f"请从{'「应用程序」' if IS_MAC else '开始菜单'}打开 {provider or '代理软件'}。")
            return
        self.start_proxy_app(binary)

    def start_proxy_app(self, binary: Path):
        try:
            if IS_MAC:
                macos.open_app(binary)
            else:
                subprocess.Popen([str(binary)], cwd=str(binary.parent))
        except (OSError, subprocess.SubprocessError):
            self.write(f"无法打开 {binary.stem}，请手动打开应用。", "error")

    def clear_log(self, *, keep_history: bool = False):
        if not keep_history:
            self.last_log_lines = []
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _append_log_widget(self, text: str, tag: str = "") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.configure(state="disabled")
        self.log.see("end")

    def write(self, text: str, tag: str = ""):
        self.last_log_lines.append((text, tag))
        if len(self.last_log_lines) > 120:
            self.last_log_lines = self.last_log_lines[-120:]
        self._append_log_widget(text, tag)

    def tag(self, key: str, text: str, color: str = MUTED_KEY):
        self.card_status[key] = (text, color)
        if key in self.cards:
            self.cards[key].set_status(text, color)

    def refresh_actual(self):
        # Sleep/wake & system-proxy change watchdog
        now = time.time()
        gap = now - self._last_wall_tick
        self._last_wall_tick = now
        woke_from_sleep = gap > 25.0
        cur_sys = system_proxy_port()
        proxy_changed = (
            self.provider == AUTO
            and cur_sys != self._last_sys_proxy
            and cur_sys != self.relay.port
        )
        old_sys = self._last_sys_proxy
        self._last_sys_proxy = cur_sys

        if not self.busy and woke_from_sleep:
            self.write("检测到系统从休眠唤醒，正在自动复检代理并更新线路…")
            self.root.after(1500, lambda: self.begin(list(self.targets), False))
        elif not self.busy and proxy_changed and cur_sys:
            self.write(f"检测到系统代理端口变更（:{old_sys or '无'} → :{cur_sys}），正在自动刷新…")
            self.root.after(400, lambda: self.begin(list(self.targets), False))

        result = queue.Queue()
        custom = (Controller(port=self.clash_controller_port, secret=self.clash_secret)
                  if self.clash_controller_port else None)
        paths = dict(self.paths)
        targets = dict(self.targets)
        relay_port = self.relay.port if self.relay.running else None

        def read():
            try:
                result.put(live_status(paths, custom, targets=targets, relay_port=relay_port))
            except (OSError, subprocess.SubprocessError, ValueError):
                result.put(None)

        def display():
            if not self.root.winfo_exists():
                return
            try:
                payload = result.get_nowait()
            except queue.Empty:
                self.root.after(200, display)
                return
            routes = payload[0] if payload is not None else None
            running = payload[1] if payload is not None else set()
            if routes is not None:
                self.latest_routes = routes
            self.running_set = running
            for key, label in list(self.actual.items()):
                entries = routes.get(key) if routes is not None else None
                if entries:
                    compact = [item.replace("虚拟网卡连接中", "已接管") for item in entries]
                    detail = " / ".join(compact[:2])
                    if len(compact) > 2:
                        detail += f" +{len(compact) - 2}"
                elif relay_port and key in running:
                    detail = f"中继 :{relay_port} · 运行中"
                else:
                    detail = "暂未识别到活动连接" if routes is not None else "暂时无法读取"
                text = f"实时 · {detail}"
                label.set(text)
                if key in self.cards:
                    self.cards[key].set_actual(text)
                    self.cards[key].set_running(key in running)
            if relay_port:
                self._update_relay_button()
            self.root.after(3500, self.refresh_actual)

        threading.Thread(target=read, daemon=True).start()
        self.root.after(200, display)

    def begin(self, keys: list[str], opening: bool):
        if self.busy:
            return
        keys = [k for k in keys if k in self.targets]
        if not keys:
            self.write("请先点击卡片勾选至少一个应用。", "error")
            return
        try:
            port = parse_port(self.port.get()) if self.port.get().strip() else None
        except ValueError as error:
            self.write(str(error), "error")
            return
        if self.provider == MANUAL and port is None:
            self.write("请在上方填写本机 HTTP 代理端口（例如 7890 或 10808）。", "error")
            return
        self.busy = True
        for widget in self.controls:
            widget.configure(state="disabled")
        for card in self.cards.values():
            card.set_disabled(True)
        self.provider_choice.configure(state="disabled")
        self.clear_log()
        self.save()
        self.heading.configure(text="正在检查代理与服务…")
        self.health_ring.set_state(0.3, "#2DD4BF", "…", "检测中", spinning=True)
        self._render_proxy_pills([], "正在自动检测本机代理…" if self.provider == AUTO else f"正在检查 {self.provider}…")
        for key in keys:
            self.tag(key, "检查中", BLUE)
        custom = Controller(port=self.clash_controller_port, secret=self.clash_secret) if self.clash_controller_port else None
        targets_copy = dict(self.targets)
        threading.Thread(target=self.check_thread, args=(keys, port, self.provider, custom, opening, targets_copy), daemon=True).start()
        self.root.after(100, self.finish, keys, opening)

    def check_thread(self, keys: list[str], port: int | None, provider: str, custom: Controller | None, opening: bool, targets_copy: dict[str, Target]):
        try:
            result = prepare_apps(keys, port, provider, custom, provider == AUTO, opening, self.results.put, targets=targets_copy)
            self.results.put(result)
        except Exception as error:
            self.results.put(error)

    def finish(self, keys: list[str], opening: bool):
        try:
            report = self.results.get_nowait()
        except queue.Empty:
            self.root.after(100, self.finish, keys, opening)
            return
        if isinstance(report, str):
            self.heading.configure(text=report)
            self.write(report)
            self.root.after(100, self.finish, keys, opening)
            return
        try:
            if isinstance(report, tuple):
                reports, detected, preferred, messages = report
                self.show_reports(reports, detected, preferred, keys, opening, messages)
            else:
                self.heading.configure(text="检查失败")
                self.last_ring = (0.0, ROSE, "!", "检查失败")
                self.health_ring.set_state(*self.last_ring)
                self._render_proxy_pills([], f"{self.provider} 状态未知", error=True)
                self.write(f"无法完成代理检查（{type(report).__name__}）。请检查 {self.provider} 后重试。", "error")
                for key in keys:
                    self.tag(key, "检查失败", ROSE)
        finally:
            self.busy = False
            for widget in self.controls:
                widget.configure(state="normal")
            for card in self.cards.values():
                card.set_disabled(False)
            self.provider_choice.configure(state="readonly")
            self.update_proxy_controls()

    def _running_on_target_proxy(self, key: str, report: Report, effective_port: int) -> bool:
        entries = self.latest_routes.get(key, ())
        port_tokens = {f":{report.port}", f":{effective_port}"}
        for entry in entries:
            if "直连" in entry:
                return False
            if any(tok in entry for tok in port_tokens) and ("节点代理" in entry or "代理连接中" in entry or "已转发" in entry):
                return True
            if report.tun and "TUN" in entry and ("节点代理" in entry or "虚拟网卡" in entry):
                return True
        return False

    def _refresh_env_audit_async(self, active_ports: list[int], preferred: int | None, tun_on: bool) -> None:
        q: queue.Queue = queue.Queue()

        def worker():
            try:
                conflicts = envcheck.audit_environment(active_ports, preferred)
                health = envcheck.diagnose_network_health(tun_on)
                q.put((conflicts, health))
            except Exception:
                q.put(([], []))

        def done():
            if not self.root.winfo_exists():
                return
            try:
                conflicts, health = q.get_nowait()
            except queue.Empty:
                self.root.after(150, done)
                return
            self.last_conflicts = conflicts
            self.last_health = health
            self._update_env_badge()

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(150, done)

    def show_reports(self, reports: list[Report], detected: tuple[str, ...], preferred: int | None,
                     keys: list[str], opening: bool, messages: list[str]):
        self.heading.configure(text="节点与服务状态")
        for message in messages:
            self.write(message)
        available = [report for report in reports if not report.issue]
        self.last_available = available
        self.last_detected = detected

        discovered = tuple(dict.fromkeys([
            *detected,
            *(r.provider for r in available if r.provider and not r.provider.startswith(("手动端口", "自定义代理"))),
        ]))
        if discovered:
            self.refresh_provider_choices(discovered)
        self.active_provider = available[0].provider if len(available) == 1 else ""
        if self.provider == AUTO:
            found = "、".join(detected) or "未发现已知代理软件"
            self.detected_summary.configure(text=f"本机发现 · {found}")
            self.write(f"本机检测到 · {found}")
        else:
            self.detected_summary.configure(text=f"当前模式 · {self.provider}")

        tun_on = any(r.tun is True for r in available)
        self._refresh_env_audit_async([r.port for r in available if r.port], preferred, tun_on)
        self._sync_relay_routes(available, preferred)

        if not available:
            self.last_ring = (0.0, ROSE, f"0/{len(keys)}", "待处理")
            self.health_ring.set_state(*self.last_ring)
            self._render_proxy_pills([], f"{self.provider} 暂不可用", error=True)
            self.last_merged_probes = {}
            self._render_probe_chips({})
            self.write(
                reports[0].issue if reports else "未找到可用的本机代理。请打开代理软件并连接节点，或选择「手动指定端口」。",
                "error",
            )
            for key in keys:
                self.tag(key, "待处理", ROSE)
                self.details[key].set("等待可用代理")
                if key in self.cards:
                    self.cards[key].set_detail("等待可用代理")
            return

        self._render_proxy_pills(available)
        if self.tray_icon:
            tip = " / ".join(f"{r.provider} :{r.port}" for r in available[:2])
            if self.relay_enabled and self.relay.running and self.relay.port:
                tip = f"中继:{self.relay.port} → {tip}"
            self.tray_icon.update_tooltip(f"AI 代理启动器 · {tip}")

        merged_probes: dict = {}
        for report in available:
            label = f"{report.provider} :{report.port}" if report.port else f"{report.provider} TUN"
            for host, probe in report.probes.items():
                existing = merged_probes.get(host)
                if existing is None or (probe.connected and probe.route in PASSING_ROUTES and not (existing[0].connected and existing[0].route in PASSING_ROUTES)):
                    merged_probes[host] = (probe, label)
        self.last_merged_probes = merged_probes
        self._render_probe_chips(merged_probes)

        for report in available:
            if proxies.is_restricted_region(report.region):
                rname = proxies.region_label(report.region)
                self.write(f"地区提示：{report.provider} 当前 AI 出口为 {rname}（{report.region}），部分平台可能受限，可点「切换节点」换至美/日/台/新等节点。", "error")

        uncontrolled_http = [report.provider for report in available if report.endpoint is None and report.port > 0 and report.scheme != "socks5"]
        if uncontrolled_http:
            names = "、".join(dict.fromkeys(uncontrolled_http))
            self.write(f"提示：{names} 未开放 Clash 控制接口，已通过本地 HTTP 代理直接验证连通性。")
        socks5_only = [report.provider for report in available if report.scheme == "socks5" and report.port > 0]
        if socks5_only:
            names = "、".join(dict.fromkeys(socks5_only))
            self.write(f"提示：{names} 仅开放 SOCKS5 端口，已通过内置中继自动桥接为 HTTP 代理供全部应用使用。")
        tun_only = [report.provider for report in available if report.endpoint is None and report.port == 0 and report.tun]
        if tun_only:
            names = "、".join(dict.fromkeys(tun_only))
            self.write(f"提示：{names} 无需本地代理端口，已直接通过系统路由验证 AI 出口连通性。")

        use_relay = self.relay_enabled and self.relay.running and self.relay.port is not None
        opened = []
        running_ok = []
        needs_restart = []
        failed_open = 0
        ready_count = 0
        restart_prompt: tuple[str, Target, Path | None, int, str, bool] | None = None

        for key in keys:
            target = self.targets[key]
            report = app_report(target, available, preferred)
            provider = report.provider or self.provider
            can_relay = (use_relay or report.scheme == "socks5") and self.relay.running and self.relay.port is not None and report.port > 0
            effective_port = self.relay.port if (can_relay and self.relay.port) else report.port
            if report.port <= 0 or (requires_tun(target) and target.kind != "electron"):
                route = provider if report.provider == "透明代理 / 旁路由" else f"{provider} TUN"
            elif can_relay:
                suffix = " SOCKS5" if report.scheme == "socks5" else ""
                route = f"中继 :{effective_port} → {provider}{suffix}"
            else:
                route = f"{provider} :{report.port}"
            probes_list = [report.probes[host] for host in target.hosts if host in report.probes]
            reason = block_reason(probes_list, requires_tun(target), report.tun, provider) if probes_list else "缺少服务探针结果"
            if reason:
                self.details[key].set(f"线路 · {route} · 待处理")
                if key in self.cards:
                    self.cards[key].set_detail(f"线路 · {route} · 待处理")
                self.tag(key, "待处理", ROSE)
                self.write(f"{target.name} · {reason}", "error")
                continue
            ready_count += 1
            self.details[key].set(f"线路 · {route}")
            if key in self.cards:
                self.cards[key].set_detail(f"线路 · {route}")
            self.tag(key, "检测通过", TEAL)
            if opening:
                path_key = "web" if target.kind == "web" else key
                app_path = self.paths.get(path_key)
                try:
                    launch(target, app_path, effective_port, stable_profile=can_relay)
                    opened.append(target.name)
                    self.tag(key, "已打开", TEAL)
                except AlreadyRunningError:
                    if self._running_on_target_proxy(key, report, effective_port):
                        running_ok.append(target.name)
                        focused = focus_app(target, app_path)
                        self.tag(key, "已在运行", TEAL)
                        suffix = "，已唤起窗口。" if focused else "，无需重新打开。"
                        self.write(f"{target.name} · 已在运行且正在通过 {route} 联网{suffix}", "good")
                    else:
                        needs_restart.append(target.name)
                        self.tag(key, "需重启生效", AMBER)
                        self.write(f"{target.name} · 已在运行但未确认走 {route}，可在弹窗中选择「重启并切换代理」。")
                        if len(keys) == 1:
                            restart_prompt = (key, target, app_path, effective_port, route, can_relay)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    failed_open += 1
                    self.tag(key, "启动失败", ROSE)
                    self.write(f"{target.name} · {error}", "error")

        effective_ready = max(0, ready_count - failed_open)
        ratio = effective_ready / max(1, len(keys))
        if effective_ready == len(keys) and not needs_restart:
            ring_color = EMERALD
            ring_sub = "已在运行" if (opening and running_ok and not opened) else "全部就绪"
        elif effective_ready > 0 or needs_restart:
            ring_color = AMBER
            ring_sub = "需重启生效" if (opening and needs_restart and not opened and not running_ok) else "部分就绪"
        else:
            ring_color = ROSE
            ring_sub = "启动失败" if failed_open else "待处理"
        self.last_ring = (ratio, ring_color, f"{effective_ready}/{len(keys)}", ring_sub)
        self.health_ring.set_state(*self.last_ring)

        if opened:
            self.write("已请求打开 " + "、".join(opened) + "。", "good")
        for report in available:
            port_label = f" :{report.port}" if report.port else " (系统路由)"
            for probe in report.probes.values():
                tag = "good" if probe.connected and probe.route in PASSING_ROUTES else "error"
                lat = f" · {probe.latency_ms}ms" if probe.latency_ms is not None else ""
                node_txt = f" · {probe.node}" if probe.node else ""
                self.write(f"{report.provider}{port_label} · {probe.host} · {LABELS[probe.route]}{node_txt}{lat} · {probe.detail}", tag)
        self.log.see("1.0")

        if restart_prompt is not None:
            self.root.after(50, lambda args=restart_prompt: dialogs.confirm_restart_app(self, *args[:5], stable_profile=args[5]))


def show(smoke: bool = False, report: Path | None = None, minimized: bool = False, instance_guard=None):
    enable_dpi_awareness()
    root = tk.Tk()
    app = LauncherWindow(root, enable_tray=not smoke, start_minimized=minimized and not smoke)
    if instance_guard is not None and not smoke:
        instance_guard.start_listener(lambda: root.after(0, app.restore_from_tray))
    if smoke:
        root.update_idletasks()
        root.update()
        assert len(app.tags) == len(app.targets) and app.log.winfo_height() > 20, (
            root.winfo_geometry(), app.log.winfo_geometry(), root.winfo_screenheight()
        )
        assert all(tag.winfo_ismapped() for tag in app.tags.values()) and len(app.actual) == len(app.targets)
        assert app.guide_button.winfo_ismapped()
        if report:
            report.write_text(f"UI OK {root.winfo_width()}x{root.winfo_height()} / {len(app.tags)} cards", encoding="utf-8")
        root.destroy()
        return
    if not minimized and not app.skip_guide_on_start:
        def after_guide() -> None:
            app.write("使用说明书已收起至右上角「? 使用说明」，随时点击即可再次查阅。", "good")
            app.start_background_tasks()

        root.after(80, lambda: app.open_guide(startup_gate=True, on_acknowledged=after_guide))
    else:
        app.start_background_tasks()
    root.mainloop()
