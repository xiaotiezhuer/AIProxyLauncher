"""Reusable pure-Tkinter vector UI components, themes, and app card visuals."""
from __future__ import annotations

import ctypes
import math
from pathlib import Path
import sys
import tkinter as tk
from tkinter import font as tkfont

from launcher import IS_LINUX, IS_MAC, Target


THEMES: dict[str, dict[str, str]] = {
    "light": {
        "canvas": "#EDF2F7",
        "surface": "#FFFFFF",
        "surface_alt": "#F8FAFC",
        "surface_sel": "#F0FDFA",
        "surface_hover": "#F1F5F9",
        "surface_sel_hover": "#E6FAF5",
        "border": "#DCE4EE",
        "border_hover": "#94A3B8",
        "shadow": "#DCE3EE",
        "hero_bg": "#0F172A",
        "hero_card": "#1E293B",
        "hero_card_dim": "#162033",
        "hero_border": "#334155",
        "hero_text": "#F8FAFC",
        "hero_muted": "#94A3B8",
        "ink": "#0F172A",
        "muted": "#64748B",
        "subtle": "#94A3B8",
        "badge_bg": "#E2E8F0",
        "status_idle_bg": "#EDF2F7",
        "ring_inner": "#162136",
        "ring_track": "#293852",
        "teal_pale": "#E6FAF5",
        "teal_soft": "#CCFBF1",
        "teal_ink": "#0F766E",
        "amber_soft": "#FEF3C7",
        "amber_ink": "#B45309",
        "rose_soft": "#FFE4E6",
        "rose_ink": "#E11D48",
        "blue_soft": "#DBEAFE",
        "blue_ink": "#2563EB",
        "disabled_bg": "#E2E8F0",
        "disabled_fg": "#94A3B8",
    },
    "dark": {
        "canvas": "#0B1120",
        "surface": "#151F32",
        "surface_alt": "#101829",
        "surface_sel": "#0D2B2A",
        "surface_hover": "#1D2A42",
        "surface_sel_hover": "#113736",
        "border": "#243248",
        "border_hover": "#475569",
        "shadow": "#070C17",
        "hero_bg": "#090F1D",
        "hero_card": "#162238",
        "hero_card_dim": "#111A2C",
        "hero_border": "#2A3B56",
        "hero_text": "#F8FAFC",
        "hero_muted": "#94A3B8",
        "ink": "#F1F5F9",
        "muted": "#94A3B8",
        "subtle": "#64748B",
        "badge_bg": "#1E2D45",
        "status_idle_bg": "#1E293B",
        "ring_inner": "#111B2E",
        "ring_track": "#24344F",
        "teal_pale": "#0F3430",
        "teal_soft": "#134E4A",
        "teal_ink": "#5EEAD4",
        "amber_soft": "#3D2B0D",
        "amber_ink": "#FBBF24",
        "rose_soft": "#3B1522",
        "rose_ink": "#FDA4AF",
        "blue_soft": "#172E54",
        "blue_ink": "#60A5FA",
        "disabled_bg": "#1E293B",
        "disabled_fg": "#475569",
    },
}

TEAL = "#0D9488"
TEAL_DARK = "#0F766E"
EMERALD = "#10B981"
AMBER = "#F59E0B"
ROSE = "#E11D48"
BLUE = "#2563EB"
MUTED_KEY = "muted"

FONT = "PingFang SC" if IS_MAC else ("Noto Sans CJK SC" if IS_LINUX else "Microsoft YaHei UI")
DEFAULTS = ("ide", "agent", "claude", "chatgpt")

APP_VISUALS: dict[str, tuple[str, str, str, str]] = {
    "ide": ("#4F46E5", "ag", "桌面 · TUN", "desktop"),
    "agent": ("#2563EB", "orbit", "桌面 · TUN", "desktop"),
    "antigravity_web": ("#0284C7", "globe", "网页窗口", "web"),
    "gemini": ("#7C3AED", "sparkle", "网页窗口", "web"),
    "claude": ("#D97757", "sunburst", "网页窗口", "web"),
    "chatgpt": ("#10A37F", "bloom", "网页窗口", "web"),
    "code": ("#2B3A55", "cli", "终端 CLI", "cli"),
    "claude_desktop": ("#C2592E", "sunburst", "桌面 · TUN", "desktop"),
    "chatgpt_desktop": ("#047857", "codex", "桌面 · Codex", "desktop"),
    "cursor": ("#334155", "cursor", "桌面 · TUN", "desktop"),
    "windsurf": ("#0EA5E9", "orbit", "桌面 · TUN", "desktop"),
    "vscode": ("#007ACC", "codex", "桌面 · TUN", "desktop"),
    "gemini_cli": ("#7C3AED", "cli", "终端 CLI", "cli"),
    "codex_cli": ("#10A37F", "cli", "终端 CLI", "cli"),
    "grok": ("#334155", "sparkle", "网页窗口", "web"),
    "perplexity": ("#0891B2", "globe", "网页窗口", "web"),
}

PROBE_LABELS: dict[str, str] = {
    "accounts.google.com": "Google 账号",
    "oauth2.googleapis.com/token": "Google OAuth",
    "daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist": "Code Assist",
    "cloudcode-pa.googleapis.com": "CloudCode",
    "businessaicode.googleapis.com": "AI Code",
    "antigravity.google.com": "Antigravity",
    "gemini.google.com": "Gemini",
    "claude.ai": "Claude",
    "api.anthropic.com": "Anthropic API",
    "chatgpt.com": "ChatGPT",
    "ws.chatgpt.com": "ChatGPT WS",
    "chatgpt.com/backend-api/codex/responses": "Codex WS",
    "api2.cursor.sh": "Cursor API",
    "codeium.com": "Windsurf",
    "api.githubcopilot.com": "Copilot API",
    "generativelanguage.googleapis.com": "Gemini API",
    "api.openai.com": "OpenAI API",
    "grok.com": "Grok",
    "www.perplexity.ai": "Perplexity",
}

FILTERS = (
    ("all", "全部应用"),
    ("desktop", "桌面客户端"),
    ("web", "网页窗口"),
    ("cli", "终端"),
)


def visual_for_target(key: str, target: Target) -> tuple[str, str, str, str]:
    if key in APP_VISUALS:
        return APP_VISUALS[key]
    if target.kind == "web":
        return "#0284C7", "globe", "自定义网页", "web"
    if target.kind == "cli":
        return "#2B3A55", "cli", "自定义终端", "cli"
    return "#4F46E5", "orbit", "自定义桌面", "desktop"


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def draw_rounded_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    *,
    fill: str = "",
    outline: str = "",
    width: float = 1,
    tags: str | tuple[str, ...] = (),
) -> list[int]:
    r = max(0.0, min(radius, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    items: list[int] = []
    if fill:
        if r > 0:
            d = 2 * r
            items.append(canvas.create_arc(x1, y1, x1 + d, y1 + d, start=90, extent=90, style="pieslice", fill=fill, outline=fill, tags=tags))
            items.append(canvas.create_arc(x2 - d, y1, x2, y1 + d, start=0, extent=90, style="pieslice", fill=fill, outline=fill, tags=tags))
            items.append(canvas.create_arc(x1, y2 - d, x1 + d, y2, start=180, extent=90, style="pieslice", fill=fill, outline=fill, tags=tags))
            items.append(canvas.create_arc(x2 - d, y2 - d, x2, y2, start=270, extent=90, style="pieslice", fill=fill, outline=fill, tags=tags))
            items.append(canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="", tags=tags))
            items.append(canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline="", tags=tags))
        else:
            items.append(canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline="", tags=tags))
    if outline and width > 0:
        if r > 0:
            d = 2 * r
            items.append(canvas.create_arc(x1, y1, x1 + d, y1 + d, start=90, extent=90, style="arc", outline=outline, width=width, tags=tags))
            items.append(canvas.create_arc(x2 - d, y1, x2, y1 + d, start=0, extent=90, style="arc", outline=outline, width=width, tags=tags))
            items.append(canvas.create_arc(x1, y2 - d, x1 + d, y2, start=180, extent=90, style="arc", outline=outline, width=width, tags=tags))
            items.append(canvas.create_arc(x2 - d, y2 - d, x2, y2, start=270, extent=90, style="arc", outline=outline, width=width, tags=tags))
            items.append(canvas.create_line(x1 + r, y1, x2 - r, y1, fill=outline, width=width, tags=tags))
            items.append(canvas.create_line(x1 + r, y2, x2 - r, y2, fill=outline, width=width, tags=tags))
            items.append(canvas.create_line(x1, y1 + r, x1, y2 - r, fill=outline, width=width, tags=tags))
            items.append(canvas.create_line(x2, y1 + r, x2, y2 - r, fill=outline, width=width, tags=tags))
        else:
            items.append(canvas.create_rectangle(x1, y1, x2, y2, fill="", outline=outline, width=width, tags=tags))
    return items


_ICON_SIZES = (40, 50, 60)
_ICON_CACHE: dict[tuple[int, str, int], tk.PhotoImage | None] = {}


def icon_asset_path(key: str, size: int = 40) -> Path | None:
    """Resolve the bundled official RGBA PNG icon closest to the requested pixel size."""
    bucket = min(_ICON_SIZES, key=lambda s: abs(s - size))
    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    roots.append(Path(__file__).parent)
    for root in roots:
        candidate = root / "icons" / str(bucket) / f"{key}.png"
        if candidate.is_file():
            return candidate
    return None


def load_app_icon(widget: tk.Misc, key: str, size: int = 40) -> tk.PhotoImage | None:
    """Load and cache an official PNG icon as a Tk PhotoImage (pure stdlib, zero external deps)."""
    bucket = min(_ICON_SIZES, key=lambda s: abs(s - size))
    try:
        tk_root = widget._root()
    except Exception:
        return None
    cache_key = (id(tk_root), key, bucket)
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]
    path = icon_asset_path(key, bucket)
    if path is None:
        _ICON_CACHE[cache_key] = None
        return None
    try:
        photo = tk.PhotoImage(master=tk_root, file=str(path))
    except (tk.TclError, OSError):
        photo = None
    _ICON_CACHE[cache_key] = photo
    return photo


def draw_brand_emblem(canvas: tk.Canvas, cx: float, cy: float, radius: float, bg: str, kind: str, font_obj: tkfont.Font) -> None:
    canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, fill=bg, outline="")
    r = radius * 0.48
    if kind == "ag":
        canvas.create_text(cx, cy, text="AG", fill="#FFFFFF", font=font_obj)
    elif kind == "cursor":
        pts = [cx - r * 0.55, cy - r * 0.85, cx + r * 0.85, cy + r * 0.1, cx + r * 0.05, cy + r * 0.25, cx - r * 0.35, cy + r * 0.9]
        canvas.create_polygon(pts, fill="#FFFFFF", outline="")
    elif kind == "orbit":
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#FFFFFF", width=2)
        dot_r = radius * 0.16
        canvas.create_oval(cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r, fill="#FFFFFF", outline="")
        sat_r = radius * 0.11
        sx, sy = cx + r * 0.72, cy - r * 0.72
        canvas.create_oval(sx - sat_r, sy - sat_r, sx + sat_r, sy + sat_r, fill="#93C5FD", outline="")
    elif kind == "globe":
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#FFFFFF", width=1.8)
        canvas.create_oval(cx - r * 0.45, cy - r, cx + r * 0.45, cy + r, outline="#FFFFFF", width=1.3)
        canvas.create_line(cx - r, cy, cx + r, cy, fill="#FFFFFF", width=1.3)
    elif kind == "sparkle":
        inner = r * 0.34
        pts = [
            cx, cy - r * 1.15, cx + inner, cy - inner, cx + r * 1.15, cy, cx + inner, cy + inner,
            cx, cy + r * 1.15, cx - inner, cy + inner, cx - r * 1.15, cy, cx - inner, cy - inner,
        ]
        canvas.create_polygon(pts, fill="#FFFFFF", outline="")
    elif kind == "sunburst":
        for i in range(8):
            ang = math.radians(i * 45)
            canvas.create_line(
                cx + math.cos(ang) * r * 0.25, cy + math.sin(ang) * r * 0.25,
                cx + math.cos(ang) * r * 1.05, cy + math.sin(ang) * r * 1.05,
                fill="#FFFFFF", width=2, capstyle="round",
            )
        hub = r * 0.28
        canvas.create_oval(cx - hub, cy - hub, cx + hub, cy + hub, fill="#FFFFFF", outline="")
    elif kind == "bloom":
        pet = r * 0.42
        ring = r * 0.56
        for i in range(6):
            ang = math.radians(i * 60 - 30)
            px, py = cx + math.cos(ang) * ring, cy + math.sin(ang) * ring
            canvas.create_oval(px - pet, py - pet, px + pet, py + pet, outline="#FFFFFF", width=1.6)
        core = r * 0.26
        canvas.create_oval(cx - core, cy - core, cx + core, cy + core, fill=bg, outline="")
    elif kind == "cli":
        canvas.create_text(cx, cy, text=">_", fill="#FBBF24", font=font_obj)
    elif kind == "codex":
        canvas.create_text(cx, cy, text="{}", fill="#FFFFFF", font=font_obj)
    else:
        canvas.create_text(cx, cy, text="AI", fill="#FFFFFF", font=font_obj)


def fit_text(text: str, max_px: int, font_obj: tkfont.Font) -> str:
    if max_px <= 16 or font_obj.measure(text) <= max_px:
        return text
    ellipsis = "…"
    budget = max_px - font_obj.measure(ellipsis)
    if budget <= 0:
        return ellipsis
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font_obj.measure(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ellipsis


class HoverTooltip:
    """Lightweight floating tooltip for probe chips and proxy capsules."""

    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 240):
        self._widget = widget
        self._text = text
        self._delay = delay_ms
        self._after_id: str | None = None
        self._win: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._cancel, add="+")
        widget.bind("<ButtonPress>", self._cancel, add="+")
        widget.bind("<Destroy>", self._cancel, add="+")

    def set_text(self, text: str) -> None:
        self._text = text

    def _schedule(self, _event=None) -> None:
        self._cancel()
        if self._text:
            self._after_id = self._widget.after(self._delay, self._show)

    def _cancel(self, _event=None) -> None:
        if self._after_id:
            try:
                self._widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        if self._win:
            try:
                self._win.destroy()
            except tk.TclError:
                pass
            self._win = None

    def _show(self) -> None:
        self._after_id = None
        if not self._widget.winfo_exists():
            return
        try:
            x = self._widget.winfo_rootx() + 8
            y = self._widget.winfo_rooty() + self._widget.winfo_height() + 6
            win = tk.Toplevel(self._widget)
            win.wm_overrideredirect(True)
            win.configure(bg="#0F172A")
            frame = tk.Frame(win, bg="#0F172A", highlightthickness=1, highlightbackground="#334155", padx=10, pady=6)
            frame.pack()
            tk.Label(frame, text=self._text, bg="#0F172A", fg="#F8FAFC", justify="left", font=(FONT, 8)).pack()
            win.update_idletasks()
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            tw, th = win.winfo_width(), win.winfo_height()
            if x + tw > sw - 12:
                x = max(8, sw - tw - 12)
            if y + th > sh - 12:
                y = max(8, self._widget.winfo_rooty() - th - 6)
            win.wm_geometry(f"+{x}+{y}")
            self._win = win
        except tk.TclError:
            pass


class PillButton(tk.Canvas):
    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        command,
        *,
        bg: str,
        fill: str = "#FFFFFF",
        hover_fill: str = "#F1F5F9",
        fg: str = "#0F172A",
        outline: str = "#DCE4EE",
        hover_outline: str = "#94A3B8",
        disabled_fill: str = "#CBD5E1",
        disabled_fg: str = "#64748B",
        font_spec: tuple = (FONT, 9, "bold"),
        pad_x: int = 14,
        height: int = 32,
        radius: int | None = None,
        tooltip: str = "",
    ):
        self._text = text
        self._command = command
        self._fill = fill
        self._hover_fill = hover_fill
        self._fg = fg
        self._outline = outline
        self._hover_outline = hover_outline
        self._disabled_fill = disabled_fill
        self._disabled_fg = disabled_fg
        self._font = tkfont.Font(family=font_spec[0], size=font_spec[1], weight=font_spec[2] if len(font_spec) > 2 else "normal")
        self._pad_x = pad_x
        self._height = height
        self._radius = radius if radius is not None else height // 2
        self._hovered = False
        self._disabled = False
        width = max(height, self._font.measure(text) + pad_x * 2)
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonRelease-1>", self._on_click)
        self.tooltip = HoverTooltip(self, tooltip) if tooltip else None
        self.redraw()

    def set_text(self, text: str) -> None:
        self._text = text
        width = max(self._height, self._font.measure(text) + self._pad_x * 2)
        super().configure(width=width)
        self.redraw()

    def set_palette(self, *, fill: str, hover_fill: str, fg: str, outline: str, hover_outline: str) -> None:
        self._fill = fill
        self._hover_fill = hover_fill
        self._fg = fg
        self._outline = outline
        self._hover_outline = hover_outline
        self.redraw()

    def set_state(self, state: str) -> None:
        self._disabled = state == "disabled"
        super().configure(cursor="" if self._disabled else "hand2")
        if self._disabled:
            self._hovered = False
        self.redraw()

    def configure(self, cnf=None, **kw):
        if "state" in kw:
            self.set_state(kw.pop("state"))
        if "text" in kw:
            self.set_text(kw.pop("text"))
        if cnf or kw:
            return super().configure(cnf, **kw)
        return None

    config = configure

    def _on_enter(self, _event=None):
        if not self._disabled:
            self._hovered = True
            self.redraw()

    def _on_leave(self, _event=None):
        self._hovered = False
        self.redraw()

    def _on_click(self, event):
        if self._disabled:
            return
        w, h = int(self["width"]), int(self["height"])
        if 0 <= event.x <= w and 0 <= event.y <= h and self._command:
            self._command()

    def redraw(self) -> None:
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        fill = self._disabled_fill if self._disabled else (self._hover_fill if self._hovered else self._fill)
        fg = self._disabled_fg if self._disabled else self._fg
        outline = "" if self._disabled else (self._hover_outline if self._hovered else self._outline)
        draw_rounded_rect(self, 1, 1, w - 2, h - 2, self._radius, fill=fill, outline=outline, width=1)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)


class StatusChip(tk.Canvas):
    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        *,
        bg: str,
        fill: str,
        fg: str,
        dot: str,
        outline: str = "",
        hover_outline: str = "#5EEAD4",
        secondary: str = "",
        secondary_fg: str = "#7DD3FC",
        meta: str = "",
        meta_fg: str = "#94A3B8",
        badge: str = "",
        badge_bg: str = "",
        badge_fg: str = "",
        font_size: int = 8,
        bold: bool = False,
        height: int = 24,
        pad_x: int = 10,
        on_click=None,
        tooltip: str = "",
    ):
        self._text = text
        self._fill = fill
        self._fg = fg
        self._dot = dot
        self._outline = outline
        self._hover_outline = hover_outline
        self._secondary = secondary
        self._secondary_fg = secondary_fg
        self._meta = meta
        self._meta_fg = meta_fg
        self._badge = badge
        self._badge_bg = badge_bg
        self._badge_fg = badge_fg
        self._height = height
        self._pad_x = pad_x
        self._on_click = on_click
        self._hovered = False

        self._main_font = tkfont.Font(family=FONT, size=font_size, weight="bold" if bold else "normal")
        self._sec_font = tkfont.Font(family=FONT, size=font_size)
        self._badge_font = tkfont.Font(family=FONT, size=max(7, font_size - 1), weight="bold")
        dot_d = max(6, height // 4)
        w = pad_x + dot_d + 6 + self._main_font.measure(text) + pad_x
        if secondary:
            w += self._sec_font.measure(secondary) + 4
        if meta:
            w += self._sec_font.measure(meta) + 8
        if badge:
            w += self._badge_font.measure(badge) + 12 + 7
        super().__init__(parent, width=w, height=height, bg=bg, highlightthickness=0, bd=0,
                         cursor="hand2" if on_click else "")
        if on_click:
            self.bind("<Enter>", self._enter)
            self.bind("<Leave>", self._leave)
            self.bind("<ButtonRelease-1>", self._click)
        if tooltip:
            self._tooltip = HoverTooltip(self, tooltip)
        self.redraw()

    def _enter(self, _e=None):
        self._hovered = True
        self.redraw()

    def _leave(self, _e=None):
        self._hovered = False
        self.redraw()

    def _click(self, event):
        w, h = int(self["width"]), int(self["height"])
        if 0 <= event.x <= w and 0 <= event.y <= h and self._on_click:
            self._on_click()

    def redraw(self) -> None:
        self.delete("all")
        w, h = int(self["width"]), self._height
        outline = self._hover_outline if (self._hovered and self._on_click) else self._outline
        draw_rounded_rect(self, 1, 1, w - 2, h - 2, (h - 2) / 2, fill=self._fill, outline=outline, width=1 if outline else 0)
        cy = h / 2
        dot_d = max(6, h // 4)
        x = self._pad_x
        self.create_oval(x, cy - dot_d / 2, x + dot_d, cy + dot_d / 2, fill=self._dot, outline="")
        x += dot_d + 6
        self.create_text(x, cy, text=self._text, fill=self._fg, font=self._main_font, anchor="w")
        x += self._main_font.measure(self._text)
        if self._secondary:
            x += 4
            self.create_text(x, cy, text=self._secondary, fill=self._secondary_fg, font=self._sec_font, anchor="w")
            x += self._sec_font.measure(self._secondary)
        if self._meta:
            x += 8
            self.create_text(x, cy, text=self._meta, fill=self._meta_fg, font=self._sec_font, anchor="w")
            x += self._sec_font.measure(self._meta)
        if self._badge:
            x += 7
            bw = self._badge_font.measure(self._badge) + 12
            bh = h - 8
            draw_rounded_rect(self, x, cy - bh / 2, x + bw, cy + bh / 2, bh / 2, fill=self._badge_bg)
            self.create_text(x + bw / 2, cy, text=self._badge, fill=self._badge_fg, font=self._badge_font)


class ToggleSwitch(tk.Canvas):
    def __init__(self, parent: tk.Widget, variable: tk.BooleanVar, command=None, *, bg: str, width: int = 40, height: int = 22):
        self._var = variable
        self._command = command
        self._disabled = False
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Button-1>", self._toggle)
        self._trace_id = self._var.trace_add("write", lambda *_: self._safe_redraw())
        self.bind("<Destroy>", self._cleanup)
        self.redraw()

    def _cleanup(self, _e=None):
        try:
            self._var.trace_remove("write", self._trace_id)
        except Exception:
            pass

    def _safe_redraw(self):
        if self.winfo_exists():
            self.redraw()

    def configure(self, cnf=None, **kw):
        if "state" in kw:
            self._disabled = kw.pop("state") == "disabled"
            super().configure(cursor="" if self._disabled else "hand2")
            self.redraw()
        if cnf or kw:
            return super().configure(cnf, **kw)
        return None

    config = configure

    def _toggle(self, _event=None):
        if self._disabled:
            return
        self._var.set(not self._var.get())
        if self._command:
            self._command()

    def redraw(self) -> None:
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        on = self._var.get()
        track = "#334155" if self._disabled else (TEAL if on else "#475569")
        knob = "#94A3B8" if self._disabled else "#FFFFFF"
        draw_rounded_rect(self, 1, 1, w - 2, h - 2, (h - 2) / 2, fill=track)
        pad = 3
        d = h - pad * 2
        x = (w - pad - d) if on else pad
        self.create_oval(x, pad, x + d, pad + d, fill=knob, outline="")


class HealthRing(tk.Canvas):
    def __init__(self, parent: tk.Widget, palette: dict[str, str], size: int = 88):
        self._p = palette
        super().__init__(parent, width=size, height=size, bg=palette["hero_bg"], highlightthickness=0, bd=0)
        self._size = size
        self._ratio = 0.0
        self._color = "#38BDF8"
        self._headline = "—"
        self._sub = "待检测"
        self._spinning = False
        self._angle = 90
        self._after_id: str | None = None
        self._big_font = tkfont.Font(family=FONT, size=14, weight="bold")
        self._sub_font = tkfont.Font(family=FONT, size=8)
        self.redraw()

    def set_state(self, ratio: float, color: str, headline: str, sub: str, *, spinning: bool = False) -> None:
        self._ratio = max(0.0, min(1.0, ratio))
        self._color = color
        self._headline = headline
        self._sub = sub
        was_spinning = self._spinning
        self._spinning = spinning
        if spinning and not was_spinning:
            self._tick()
        elif not spinning:
            if self._after_id:
                try:
                    self.after_cancel(self._after_id)
                except tk.TclError:
                    pass
                self._after_id = None
            self.redraw()

    def _tick(self) -> None:
        if not self._spinning or not self.winfo_exists():
            return
        self._angle = (self._angle - 16) % 360
        self.redraw()
        self._after_id = self.after(45, self._tick)

    def redraw(self) -> None:
        self.delete("all")
        s = self._size
        pad = max(7, s // 11)
        stroke = max(6, s // 13)
        self.create_oval(pad - 2, pad - 2, s - pad + 2, s - pad + 2, fill=self._p["ring_inner"], outline="")
        self.create_oval(pad, pad, s - pad, s - pad, outline=self._p["ring_track"], width=stroke)
        if self._spinning:
            self.create_arc(pad, pad, s - pad, s - pad, start=self._angle, extent=115, style="arc", outline="#2DD4BF", width=stroke)
        elif self._ratio >= 0.999:
            self.create_oval(pad, pad, s - pad, s - pad, outline=self._color, width=stroke)
        elif self._ratio > 0.01:
            self.create_arc(pad, pad, s - pad, s - pad, start=90, extent=-360 * self._ratio, style="arc", outline=self._color, width=stroke)
        self.create_text(s / 2, s * 0.42, text=self._headline, fill=self._p["hero_text"], font=self._big_font)
        self.create_text(s / 2, s * 0.67, text=self._sub, fill=self._p["hero_muted"], font=self._sub_font)


class AppCard(tk.Canvas):
    def __init__(
        self,
        parent: tk.Widget,
        key: str,
        title: str,
        *,
        palette: dict[str, str],
        brand_color: str,
        icon_kind: str,
        category: str,
        selected_var: tk.BooleanVar,
        has_path_button: bool,
        on_toggle,
        on_open,
        on_choose_path,
        scale: float = 1.0,
    ):
        self.key = key
        self._title = title
        self._p = palette
        self._brand_color = brand_color
        self._icon_kind = icon_kind
        self._category = category
        self._selected_var = selected_var
        self._has_path = has_path_button
        self._on_toggle = on_toggle
        self._on_open = on_open
        self._on_choose_path = on_choose_path
        self._s = scale

        self._status_text = "待检查"
        self._status_color_key = MUTED_KEY
        self._detail_text = ""
        self._actual_text = "实时 · 检查中"
        self._running = False
        self._hovered = False
        self._hover_part = ""
        self._disabled = False
        self._icon_photo: tk.PhotoImage | None = None
        self._open_box = (0.0, 0.0, 0.0, 0.0)
        self._path_box = (0.0, 0.0, 0.0, 0.0)

        self._title_font = tkfont.Font(family=FONT, size=10, weight="bold")
        self._glyph_font = tkfont.Font(family=FONT, size=10, weight="bold")
        self._badge_font = tkfont.Font(family=FONT, size=8)
        self._detail_font = tkfont.Font(family=FONT, size=8)
        self._btn_font = tkfont.Font(family=FONT, size=8, weight="bold")

        height = self._px(134)
        super().__init__(parent, width=self._px(286), height=height, bg=palette["canvas"], highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonRelease-1>", self._on_click)
        self._trace_id = self._selected_var.trace_add("write", lambda *_: self._safe_redraw())
        self.bind("<Destroy>", self._cleanup)
        self.redraw()

    def _cleanup(self, _e=None):
        try:
            self._selected_var.trace_remove("write", self._trace_id)
        except Exception:
            pass

    def _safe_redraw(self):
        if self.winfo_exists():
            self.redraw()

    def _px(self, value: float) -> int:
        return max(1, int(round(value * self._s)))

    def set_status(self, text: str, color: str) -> None:
        self._status_text = text
        self._status_color_key = color
        self.redraw()

    def set_running(self, is_running: bool) -> None:
        if self._running != is_running:
            self._running = is_running
            self.redraw()

    def set_detail(self, text: str) -> None:
        self._detail_text = text
        self.redraw()

    def set_actual(self, text: str) -> None:
        self._actual_text = text
        self.redraw()

    def set_disabled(self, disabled: bool) -> None:
        self._disabled = disabled
        super().configure(cursor="" if disabled else "hand2")
        if disabled:
            self._hovered = False
            self._hover_part = ""
        self.redraw()

    def _status_colors(self) -> tuple[str, str, str]:
        p = self._p
        c = self._status_color_key
        if c == TEAL:
            return p["teal_ink"], p["teal_pale"], EMERALD
        if c == AMBER:
            return p["amber_ink"], p["amber_soft"], AMBER
        if c == ROSE:
            return p["rose_ink"], p["rose_soft"], ROSE
        if c == BLUE:
            return p["blue_ink"], p["blue_soft"], "#38BDF8"
        return p["muted"], p["status_idle_bg"], p["subtle"]

    def _in_box(self, x: int, y: int, box: tuple[float, float, float, float]) -> bool:
        return box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def _on_motion(self, event) -> None:
        if self._disabled:
            return
        part = "card"
        if self._in_box(event.x, event.y, self._open_box):
            part = "open"
        elif self._has_path and self._in_box(event.x, event.y, self._path_box):
            part = "path"
        if not self._hovered or part != self._hover_part:
            self._hovered = True
            self._hover_part = part
            self.redraw()

    def _on_leave(self, _event=None) -> None:
        if self._hovered or self._hover_part:
            self._hovered = False
            self._hover_part = ""
            self.redraw()

    def _on_click(self, event) -> None:
        if self._disabled:
            return
        w, h = self.winfo_width(), self.winfo_height()
        if not (0 <= event.x <= w and 0 <= event.y <= h):
            return
        if self._in_box(event.x, event.y, self._open_box):
            self._on_open(self.key)
        elif self._has_path and self._in_box(event.x, event.y, self._path_box):
            self._on_choose_path(self.key)
        else:
            self._selected_var.set(not self._selected_var.get())
            self._on_toggle()

    def redraw(self) -> None:
        self.delete("all")
        p = self._p
        w = max(self._px(220), self.winfo_width() if self.winfo_width() > 20 else int(self["width"]))
        h = max(self._px(126), self.winfo_height() if self.winfo_height() > 20 else int(self["height"]))
        selected = self._selected_var.get()
        r = self._px(14)

        draw_rounded_rect(self, 3, 5, w - 2, h - 1, r, fill=p["shadow"])
        if selected:
            fill = p["surface_sel_hover"] if self._hovered else p["surface_sel"]
            outline = TEAL_DARK if self._hovered else TEAL
            bw = 2
        else:
            fill = p["surface_hover"] if self._hovered else p["surface"]
            outline = p["border_hover"] if self._hovered else p["border"]
            bw = 1
        draw_rounded_rect(self, 2, 2, w - 3, h - 4, r, fill=fill, outline=outline, width=bw)

        st_fg, st_bg, dot_color = self._status_colors()

        ex, ey, ed = self._px(15), self._px(14), self._px(40)
        photo = load_app_icon(self, self.key, ed)
        self._icon_photo = photo
        if photo is not None:
            self.create_image(ex + ed / 2, ey + ed / 2, image=photo)
        else:
            draw_brand_emblem(self, ex + ed / 2, ey + ed / 2, ed / 2, self._brand_color, self._icon_kind, self._glyph_font)
        dot_d = self._px(10)
        dx, dy = ex + ed - dot_d + self._px(2), ey + ed - dot_d + self._px(2)
        self.create_oval(dx - 2, dy - 2, dx + dot_d + 2, dy + dot_d + 2, fill=fill, outline="")
        self.create_oval(dx, dy, dx + dot_d, dy + dot_d, fill=dot_color, outline="")

        cd = self._px(21)
        cx2 = w - self._px(14)
        cx1 = cx2 - cd
        cy1 = self._px(14)
        if selected:
            self.create_oval(cx1, cy1, cx2, cy1 + cd, fill=TEAL, outline="")
            self.create_text((cx1 + cx2) / 2, cy1 + cd / 2, text="✓", fill="#FFFFFF", font=self._btn_font)
        else:
            self.create_oval(cx1, cy1, cx2, cy1 + cd, fill=p["surface"], outline=p["border_hover"], width=1.5)

        tx = ex + ed + self._px(11)
        title_budget = int(cx1 - tx - self._px(8))
        self.create_text(tx, self._px(24), text=fit_text(self._title, title_budget, self._title_font),
                         fill=p["ink"], font=self._title_font, anchor="w")

        cat_w = self._badge_font.measure(self._category) + self._px(12)
        cat_h = self._px(18)
        cat_y1 = self._px(38)
        draw_rounded_rect(self, tx, cat_y1, tx + cat_w, cat_y1 + cat_h, cat_h / 2,
                          fill=p["teal_soft"] if selected else p["badge_bg"])
        self.create_text(tx + cat_w / 2, cat_y1 + cat_h / 2, text=self._category,
                         fill=p["teal_ink"] if selected else p["muted"], font=self._badge_font)

        if self._running:
            rx1 = tx + cat_w + self._px(6)
            run_label = "● 运行中"
            rw = self._badge_font.measure(run_label) + self._px(12)
            if rx1 + rw < w - self._px(14):
                draw_rounded_rect(self, rx1, cat_y1, rx1 + rw, cat_y1 + cat_h, cat_h / 2, fill=p["teal_pale"])
                self.create_text(rx1 + rw / 2, cat_y1 + cat_h / 2, text=run_label, fill=EMERALD, font=self._badge_font)

        info_budget = int(w - self._px(30))
        self.create_text(self._px(15), self._px(71), text=fit_text(self._detail_text, info_budget, self._detail_font),
                         fill=p["muted"], font=self._detail_font, anchor="w")

        actual_color = p["teal_ink"] if any(tok in self._actual_text for tok in ("节点代理", "代理连接中", "TUN", "已转发")) else p["subtle"]
        if "直连" in self._actual_text:
            actual_color = p["rose_ink"]
        self.create_text(self._px(15), self._px(89), text=fit_text(self._actual_text, info_budget, self._detail_font),
                         fill=actual_color, font=self._detail_font, anchor="w")

        fy1 = h - self._px(34)
        fy2 = h - self._px(11)
        fh = fy2 - fy1

        st_w = max(self._px(60), self._btn_font.measure(self._status_text) + self._px(16))
        draw_rounded_rect(self, self._px(14), fy1, self._px(14) + st_w, fy2, fh / 2, fill=st_bg)
        self.create_text(self._px(14) + st_w / 2, (fy1 + fy2) / 2, text=self._status_text, fill=st_fg, font=self._btn_font)

        open_label = "唤起 ↗" if self._running else "打开 ↗"
        open_w = max(self._px(60), self._btn_font.measure(open_label) + self._px(18))
        ox2 = w - self._px(14)
        ox1 = ox2 - open_w
        self._open_box = (ox1, fy1, ox2, fy2)
        if self._disabled:
            ofill, ofg = p["disabled_bg"], p["disabled_fg"]
        elif self._hover_part == "open":
            ofill, ofg = TEAL_DARK, "#FFFFFF"
        else:
            ofill, ofg = TEAL, "#FFFFFF"
        draw_rounded_rect(self, ox1, fy1, ox2, fy2, fh / 2, fill=ofill)
        self.create_text((ox1 + ox2) / 2, (fy1 + fy2) / 2, text=open_label, fill=ofg, font=self._btn_font)

        if self._has_path:
            path_label = "路径"
            pw = max(self._px(44), self._btn_font.measure(path_label) + self._px(14))
            px2 = ox1 - self._px(6)
            px1 = px2 - pw
            self._path_box = (px1, fy1, px2, fy2)
            pfill = p["disabled_bg"] if self._disabled else (p["surface_hover"] if self._hover_part == "path" else p["surface"])
            pfg = p["disabled_fg"] if self._disabled else p["muted"]
            pout = "" if self._disabled else (p["border_hover"] if self._hover_part == "path" else p["border"])
            draw_rounded_rect(self, px1, fy1, px2, fy2, fh / 2, fill=pfill, outline=pout)
            self.create_text((px1 + px2) / 2, (fy1 + fy2) / 2, text=path_label, fill=pfg, font=self._btn_font)
        else:
            self._path_box = (0.0, 0.0, 0.0, 0.0)

