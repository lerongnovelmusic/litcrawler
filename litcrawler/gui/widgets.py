# -*- coding: utf-8 -*-
"""GUI 通用小部件与工具。"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable

CHECK_ON = "☑"
CHECK_OFF = "☐"

PALETTE = {
    "bg": "#f4f6f9",
    "panel": "#ffffff",
    "accent": "#2563eb",
    "accent_dark": "#1d4ed8",
    "text": "#111827",
    "muted": "#6b7280",
    "ok": "#15803d",
    "warn": "#b45309",
    "err": "#b91c1c",
    "line": "#d7dce5",
}


def pick_font(root: tk.Misc, size: int = 10, bold: bool = False) -> tkfont.Font:
    """选择一款支持中文的字体。"""
    families = set(tkfont.families(root))
    for name in ("Microsoft YaHei UI", "微软雅黑", "Microsoft YaHei", "PingFang SC",
                 "Noto Sans CJK SC", "Source Han Sans SC", "SimHei"):
        if name in families:
            return tkfont.Font(root=root, family=name, size=size,
                               weight="bold" if bold else "normal")
    return tkfont.Font(root=root, size=size, weight="bold" if bold else "normal")


def style_theme(root: tk.Misc, theme: str = "clam") -> ttk.Style:
    """应用统一的浅色主题。"""
    style = ttk.Style(root)
    try:
        style.theme_use(theme)
    except tk.TclError:
        pass
    base = pick_font(root, 10)
    small = pick_font(root, 9)
    root.option_add("*Font", base)
    style.configure(".", background=PALETTE["bg"], foreground=PALETTE["text"])
    style.configure("TFrame", background=PALETTE["bg"])
    style.configure("Card.TFrame", background=PALETTE["panel"], relief="flat")
    style.configure("TLabel", background=PALETTE["bg"], foreground=PALETTE["text"])
    style.configure("Card.TLabel", background=PALETTE["panel"], foreground=PALETTE["text"])
    style.configure("Title.TLabel", font=pick_font(root, 15, True), background=PALETTE["bg"],
                    foreground=PALETTE["accent_dark"])
    style.configure("Sub.TLabel", font=small, background=PALETTE["bg"], foreground=PALETTE["muted"])
    style.configure("Section.TLabel", font=pick_font(root, 10, True), background=PALETTE["bg"])
    style.configure("Status.TLabel", font=small, background=PALETTE["bg"], foreground=PALETTE["muted"])
    style.configure("TCheckbutton", background=PALETTE["bg"])
    style.configure("Card.TCheckbutton", background=PALETTE["panel"])
    style.configure("TButton", padding=(10, 5))
    style.configure("Accent.TButton", padding=(10, 5))
    style.map(
        "Accent.TButton",
        background=[("!disabled", PALETTE["accent"]), ("active", PALETTE["accent_dark"])],
        foreground=[("!disabled", "#ffffff")],
    )
    style.configure("Treeview", rowheight=24, fieldbackground=PALETTE["panel"],
                    background=PALETTE["panel"], font=base)
    style.configure("Treeview.Heading", font=pick_font(root, 10, True))
    style.map("Treeview", background=[("selected", "#dbeafe")],
              foreground=[("selected", PALETTE["text"])])
    style.configure("TNotebook", background=PALETTE["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", padding=(14, 6))
    style.configure("TLabelframe", background=PALETTE["bg"])
    style.configure("TLabelframe.Label", background=PALETTE["bg"], font=pick_font(root, 10, True))
    style.configure("TEntry", padding=3)
    style.configure("TCombobox", padding=3)
    return style


def open_path(path) -> None:
    """用系统默认程序打开文件或目录。"""
    path = str(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


class Tooltip:
    """简易悬浮提示。"""

    def __init__(self, widget: tk.Widget, text: str, delay: int = 500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 18
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        except Exception:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tk.Label(tip, text=self.text, justify="left", background="#fffbeb",
                 foreground=PALETTE["text"], relief="solid", borderwidth=1,
                 padx=8, pady=4, wraplength=420).pack()
        self._tip = tip

    def _hide(self, _event=None):
        self._cancel()
        if self._tip:
            self._tip.destroy()
            self._tip = None


class ScrolledTreeview(ttk.Frame):
    """带滚动的 Treeview。"""

    def __init__(self, master, columns: list[tuple[str, str, int, str]], **kw):
        super().__init__(master, **kw)
        self.tree = ttk.Treeview(self, columns=[c[0] for c in columns], show="headings",
                                 selectmode="extended")
        for key, title, width, anchor in columns:
            self.tree.heading(key, text=title, command=lambda k=key: None)
            self.tree.column(key, width=width, anchor=anchor,
                             stretch=(key in {"title", "journal", "source"}))
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.tree.tag_configure("odd", background="#f8fafc")
        self.tree.tag_configure("even", background=PALETTE["panel"])

    def __getattr__(self, item):
        return getattr(self.tree, item)


class TextDialog(tk.Toplevel):
    """可滚动的长文本窗口。"""

    def __init__(self, master, title: str, text: str, *, width: int = 104, height: int = 30):
        super().__init__(master)
        self.title(title)
        self.geometry(f"{width*9}x{height*18}")
        self.transient(master)
        frame = ttk.Frame(self, padding=8)
        frame.pack(fill="both", expand=True)
        box = tk.Text(frame, wrap="word", font=pick_font(self, 10), relief="flat",
                      background=PALETTE["panel"], padx=10, pady=8)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=vsb.set)
        box.insert("1.0", text)
        box.configure(state="disabled")
        box.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        btns = ttk.Frame(self, padding=(8, 0, 8, 8))
        btns.pack(fill="x")
        ttk.Button(btns, text="关闭", command=self.destroy).pack(side="right")
        self.bind("<Escape>", lambda _e: self.destroy())


class BusyIndicator(ttk.Frame):
    """不确定进度条 + 文案。"""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.label = ttk.Label(self, text="", style="Status.TLabel", width=46, anchor="w")
        self.bar = ttk.Progressbar(self, mode="indeterminate", length=160)
        self.label.pack(side="left", padx=(0, 8))
        self.bar.pack(side="left")

    def start(self, text: str = "处理中…") -> None:
        self.label.configure(text=text)
        self.bar.start(12)

    def stop(self, text: str = "") -> None:
        self.bar.stop()
        self.label.configure(text=text)


class Worker:
    """把耗时操作放到后台线程，并通过队列把消息投递回主线程。

    任务**排队执行**（同一时刻只有一个后台任务），避免“加载影响因子库时点检索
    结果被丢弃”这类问题。``on_message`` / ``on_done`` 随任务一起入队，
    因此每个任务可以有自己的处理函数。
    """

    def __init__(self, root: tk.Misc, interval_ms: int = 80):
        self.root = root
        self.queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._poll_job: str | None = None
        self._closed = False
        self.interval_ms = interval_ms
        self._jobs: list[tuple[Callable[["Worker"], None], Callable[[str, object], None],
                               Callable[[], None] | None]] = []
        self._handlers: tuple[Callable[[str, object], None], Callable[[], None] | None] | None = None
        self._ensure_poll()

    @property
    def running(self) -> bool:
        """是否有任务正在执行或排队（常驻轮询本身不算“忙碌”）。"""
        return bool(self._jobs) or self.busy

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def post(self, kind: str, payload: object = None) -> None:
        self.queue.put((kind, payload))

    def run(
        self,
        target: Callable[["Worker"], None],
        *,
        on_message: Callable[[str, object], None],
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """提交任务；若已有任务在执行则排队等待。"""
        self._jobs.append((target, on_message, on_done))
        self._pump()

    def clear_pending(self) -> None:
        """丢弃尚未开始的任务（例如用户重新检索）。"""
        self._jobs.clear()

    # ------------------------------------------------------------------ #
    def _pump(self) -> None:
        if self.busy or not self._jobs:
            return
        target, on_message, on_done = self._jobs.pop(0)
        self._handlers = (on_message, on_done)

        def wrapper() -> None:
            try:
                target(self)
            except Exception as exc:  # 后台异常必须回传，避免静默失败
                self.post("error", f"{type(exc).__name__}: {exc}")
            finally:
                self.post("__done__", None)

        self._thread = threading.Thread(target=wrapper, daemon=True)
        self._thread.start()
        self._ensure_poll()

    def _dispatch_done(self) -> None:
        handlers = self._handlers
        self._handlers = None
        self._thread = None
        if handlers and handlers[1]:
            try:
                handlers[1]()
            except Exception:
                pass
        self._pump()

    def _ensure_poll(self) -> None:
        """保持一个常驻轮询循环（简单可靠，避免 after 被覆盖导致消息丢失）。"""
        if self._poll_job is None and not self._closed:
            self._poll_job = self.root.after(self.interval_ms, self._poll)

    def _poll(self) -> None:
        self._poll_job = None
        if self._closed:
            return
        try:
            drained = 0
            while drained < 600:
                try:
                    kind, payload = self.queue.get_nowait()
                except queue.Empty:
                    break
                drained += 1
                if kind == "__done__":
                    self._dispatch_done()
                    continue
                handlers = self._handlers
                if handlers:
                    try:
                        handlers[0](kind, payload)
                    except Exception:
                        pass
        except tk.TclError:
            self._closed = True
            return
        self._ensure_poll()

    def stop_polling(self) -> None:
        self._closed = True
        if self._poll_job:
            try:
                self.root.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
