# -*- coding: utf-8 -*-
"""主窗口。"""

from __future__ import annotations

import csv
import json
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .. import __app_name_cn__, __version__
from ..core import impactfactor
from ..core.config import Config, HistoryEntry, logs_dir
from ..core.downloader import Downloader, DownloadSummary
from ..core.logging_setup import get_logger, install_excepthook, setup as setup_logging
from ..core.models import Paper, SourceReport
from ..core.search import SearchEngine, SearchQuery
from ..sources import build_registry
from ..sources.sites import BUILTIN_PROFILES, SiteProfile
from .dialogs import JournalPickerDialog
from .widgets import (CHECK_OFF, CHECK_ON, PALETTE, BusyIndicator, ScrolledTreeview,
                      TextDialog, Tooltip, Worker, open_path, pick_font, style_theme)

RESULT_COLUMNS = [
    ("sel", "选", 40, "center"),
    ("idx", "#", 36, "center"),
    ("title", "标题", 330, "w"),
    ("journal", "期刊", 180, "w"),
    ("impact", "影响因子", 84, "center"),
    ("year", "年份", 56, "center"),
    ("cited", "被引", 56, "center"),
    ("oa", "开放获取", 70, "center"),
    ("pdf", "PDF", 46, "center"),
    ("source", "来源", 100, "w"),
]


class ScrollableFrame(ttk.Frame):
    """可纵向滚动的容器。"""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, background=PALETTE["bg"], height=120)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel, add="+")

    def fit_height(self, max_height: int = 0) -> int:
        """把容器高度调整为内容的自然高度（可设上限，超出则出现滚动条）。"""
        self.update_idletasks()
        need = self.inner.winfo_reqheight()
        if max_height:
            need = min(need, max_height)
        need = max(need, 120)
        self.canvas.configure(height=need)
        self._on_inner()
        return need

    def _on_inner(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)

    def _on_wheel(self, event):
        try:
            if self.canvas.winfo_containing(event.x_root, event.y_root) is None:
                return
            widget = self.winfo_containing(event.x_root, event.y_root)
            w = widget
            while w is not None:
                if w is self.canvas or w is self.inner:
                    self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                    return
                w = getattr(w, "master", None)
        except Exception:
            pass


class App(tk.Tk):
    """LitCrawler 主窗口。"""

    def __init__(self, config: Config | None = None):
        super().__init__()
        self.config_obj = config or Config()
        self.logger = setup_logging(logs_dir())
        install_excepthook(self.logger)
        self.settings = self.config_obj.settings
        self.registry = build_registry()
        self.impact_db = impactfactor.ImpactFactorDB(
            impactfactor.bundle_path(),
            self.config_obj.if_cache_path,
            self.config_obj.if_import_dir,
        )
        self.engine = SearchEngine(self.impact_db, self.registry)

        self.papers: list[Paper] = []
        self.visible: list[Paper] = []
        self.selected: set[str] = set()
        self.item_ids: list[str] = []
        self.source_vars: dict[str, tk.BooleanVar] = {}
        self.last_query: SearchQuery | None = None
        self.worker = Worker(self)
        self.downloader: Downloader | None = None
        self._searching = False
        self._downloading = False
        self._stop_requested = False
        #: 自动化测试/无头运行时设为 True：不弹确认框、不自动打开目录
        self.auto_confirm = False

        self.title(f"{__app_name_cn__} LitCrawler {__version__} — 学术文献检索与批量下载")
        self.geometry("1360x860")
        self.minsize(1100, 680)
        style_theme(self, self.settings.theme)
        self._build_menu()
        self._build_header()
        self._build_body()
        self._build_status()
        self._load_state()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-f>", lambda _e: self.keyword_entry.focus_set())
        self.bind("<Control-Return>", lambda _e: self.start_search())
        self.bind("<F5>", lambda _e: self.start_search())
        self.after(200, self._preload_impact_db)

    # ================================================================== #
    # 界面构建
    # ================================================================== #
    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="导出结果为 CSV…", command=lambda: self.export_results("csv"))
        file_menu.add_command(label="导出结果为 Excel…", command=lambda: self.export_results("xlsx"))
        file_menu.add_separator()
        file_menu.add_command(label="打开数据目录", command=lambda: open_path(self.config_obj.base))
        file_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        tools = tk.Menu(menubar, tearoff=0)
        tools.add_command(label="导入影响因子数据（CSV/Excel/JSON）…", command=self.import_impact_factor)
        tools.add_command(label="下载完整 JCR 数据集（约 40MB）…", command=self.download_full_if)
        tools.add_command(label="影响因子库状态", command=self.show_if_status)
        tools.add_separator()
        tools.add_command(label="数据源连通性自检…", command=self.check_sources)
        menubar.add_cascade(label="工具", menu=tools)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="使用说明", command=self.show_help)
        help_menu.add_command(label="打开日志目录", command=lambda: open_path(logs_dir()))
        help_menu.add_command(label="关于", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.configure(menu=menubar)

    def _build_header(self) -> None:
        head = ttk.Frame(self, padding=(14, 10, 14, 6))
        head.pack(fill="x")
        ttk.Label(head, text=f"📚 {__app_name_cn__} · 学术文献检索与批量下载",
                  style="Title.TLabel").pack(side="left")
        ttk.Label(head, text=f"v{__version__}", style="Sub.TLabel").pack(side="left", padx=(8, 0), pady=(6, 0))
        right = ttk.Frame(head)
        right.pack(side="right")
        ttk.Label(right, text="数据目录：", style="Sub.TLabel").pack(side="left")
        ttk.Button(right, text="打开", width=6,
                   command=lambda: open_path(self.config_obj.base)).pack(side="left")
        ttk.Button(right, text="⚙ 设置", width=8,
                   command=self.open_settings).pack(side="left", padx=(6, 0))

    def _build_body(self) -> None:
        outer = ttk.Frame(self, padding=(10, 0, 10, 0))
        outer.pack(fill="both", expand=True)
        self.paned = ttk.Panedwindow(outer, orient="horizontal")
        self.paned.pack(fill="both", expand=True)

        left = ttk.Frame(self.paned, padding=(0, 0, 8, 0))
        right = ttk.Frame(self.paned)
        self.paned.add(left, weight=0)
        self.paned.add(right, weight=1)
        self._build_sidebar(left)

        # 检索表单固定为内容高度（窗口太矮时自动出现滚动条），结果区占满剩余空间
        self.form_wrap = ScrollableFrame(right)
        self.form_wrap.pack(fill="x", expand=False)
        results = ttk.Frame(right)
        results.pack(fill="both", expand=True)
        self._build_form(self.form_wrap.inner)
        self._build_results(results)
        right.bind("<Configure>", self._on_right_resize, add="+")
        self.after(80, lambda: self._fit_form())

    def _on_right_resize(self, event) -> None:
        if getattr(self, "_last_right_h", None) == event.height:
            return
        self._last_right_h = event.height
        self._fit_form(event.height)

    def _fit_form(self, available: int | None = None) -> None:
        """表单最多占「可用高度 - 结果区最小高度」，避免把结果表挤没。"""
        if not hasattr(self, "form_wrap"):
            return
        if available is None:
            available = self.winfo_height() - 160
        limit = max(200, int(available) - 330)
        self.form_wrap.fit_height(limit)

    # ------------------------------------------------------------------ #
    def _build_sidebar(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="搜索记录", style="Section.TLabel").pack(anchor="w")
        ttk.Label(parent, text="双击任意记录可重新检索", style="Sub.TLabel").pack(anchor="w", pady=(0, 4))

        hist_frame = ttk.Frame(parent)
        hist_frame.pack(fill="both", expand=True)
        self.history_list = tk.Listbox(hist_frame, activestyle="none", font=pick_font(self, 9),
                                       selectmode="extended", relief="flat", highlightthickness=1,
                                       highlightbackground=PALETTE["line"], background=PALETTE["panel"],
                                       width=42)
        hsb = ttk.Scrollbar(hist_frame, orient="vertical", command=self.history_list.yview)
        self.history_list.configure(yscrollcommand=hsb.set)
        self.history_list.pack(side="left", fill="both", expand=True)
        hsb.pack(side="right", fill="y")
        self.history_list.bind("<Double-Button-1>", lambda _e: self.rerun_history())
        self.history_list.bind("<Return>", lambda _e: self.rerun_history())

        hist_btns = ttk.Frame(parent)
        hist_btns.pack(fill="x", pady=(4, 10))
        ttk.Button(hist_btns, text="重新检索", command=self.rerun_history).pack(side="left")
        ttk.Button(hist_btns, text="删除", width=6, command=self.delete_history).pack(side="left", padx=4)
        ttk.Button(hist_btns, text="清空", width=6, command=self.clear_history).pack(side="left")

        ttk.Separator(parent).pack(fill="x", pady=(2, 8))
        ttk.Label(parent, text="下载", style="Section.TLabel").pack(anchor="w")
        ttk.Label(parent, text="保存目录", style="Sub.TLabel").pack(anchor="w", pady=(2, 2))
        self.dir_var = tk.StringVar(value=self.settings.download_dir)
        dir_entry = ttk.Entry(parent, textvariable=self.dir_var, width=32)
        dir_entry.pack(fill="x")
        dir_row = ttk.Frame(parent)
        dir_row.pack(fill="x", pady=(4, 8))
        ttk.Button(dir_row, text="选择目录…", command=self.choose_dir).pack(side="left")
        ttk.Button(dir_row, text="打开", width=6,
                   command=lambda: open_path(self.dir_var.get() or ".")).pack(side="left", padx=4)

        opt_row = ttk.Frame(parent)
        opt_row.pack(fill="x")
        self.filename_style_var = tk.StringVar(value="年份_作者_标题")
        ttk.Label(opt_row, text="文件名：", style="Sub.TLabel").pack(side="left")
        ttk.Combobox(opt_row, textvariable=self.filename_style_var, width=16, state="readonly",
                     values=["年份_作者_标题", "仅标题", "年份_作者_DOI"]).pack(side="left")

        self.download_btn = ttk.Button(parent, text="下载勾选文献 (0)", style="Accent.TButton",
                                       command=self.start_download)
        self.download_btn.pack(fill="x", pady=(8, 4))
        ttk.Button(parent, text="仅导出勾选项链接", command=self.export_selected_links).pack(fill="x")

    # ------------------------------------------------------------------ #
    def _build_form(self, parent: ttk.Frame) -> None:
        pad = {"padx": 2, "pady": 3}

        # 关键词
        row1 = ttk.Frame(parent)
        row1.pack(fill="x", pady=(8, 4))
        ttk.Label(row1, text="关键词", style="Section.TLabel").pack(side="left")
        self.keyword_var = tk.StringVar(value=self.settings.last_keyword)
        self.keyword_entry = ttk.Entry(row1, textvariable=self.keyword_var, font=pick_font(self, 11))
        self.keyword_entry.pack(side="left", fill="x", expand=True, padx=8)
        Tooltip(self.keyword_entry, "支持模糊检索：拼写略有差错、词序不同也能命中；\n"
                                    "多个关键词用空格分隔，例如：deep learning medical image segmentation")
        self.search_btn = ttk.Button(row1, text="🔍 检索", style="Accent.TButton", command=self.start_search)
        self.search_btn.pack(side="left")
        self.stop_btn = ttk.Button(row1, text="停止", width=6, state="disabled", command=self.stop_all)
        self.stop_btn.pack(side="left", padx=(6, 0))

        # 数据源
        src_frame = ttk.Labelframe(parent, text="网址 / 数据源（可多选）", padding=8)
        src_frame.pack(fill="x", pady=6)
        Tooltip(src_frame, "自定义站点可在「设置 → 自定义站点」中添加，勾选后同样参与检索。")
        metas = self.registry.metas()
        grid = ttk.Frame(src_frame)
        grid.pack(fill="x")
        for idx, meta in enumerate(metas):
            var = tk.BooleanVar(value=meta.name in self.settings.sources)
            self.source_vars[meta.name] = var
            cb = ttk.Checkbutton(grid, text=meta.label, variable=var)
            cb.grid(row=idx // 3, column=idx % 3, sticky="w", padx=(0, 16), pady=1)
            Tooltip(cb, f"{meta.description}\n主页：{meta.homepage}")

        # 期刊与影响因子
        jf = ttk.Labelframe(parent, text="期刊与影响因子", padding=8)
        jf.pack(fill="x", pady=6)
        jrow = ttk.Frame(jf)
        jrow.pack(fill="x")
        ttk.Label(jrow, text="期刊：").pack(side="left")
        self.journal_label = ttk.Label(jrow, text="不限", style="Sub.TLabel")
        self.journal_label.pack(side="left")
        ttk.Button(jrow, text="选择 / 保存期刊…", command=self.choose_journals).pack(side="left", padx=8)
        ttk.Button(jrow, text="清除", width=6, command=self.clear_journals).pack(side="left")

        self.fuzzy_journal_var = tk.BooleanVar(value=self.settings.fuzzy_journal)
        ttk.Checkbutton(jrow, text="期刊名模糊匹配", variable=self.fuzzy_journal_var).pack(side="left", padx=(16, 0))
        self.fuzzy_keyword_var = tk.BooleanVar(value=self.settings.fuzzy_keyword)
        ttk.Checkbutton(jrow, text="关键词模糊匹配", variable=self.fuzzy_keyword_var).pack(side="left", padx=(12, 0))

        ifrow = ttk.Frame(jf)
        ifrow.pack(fill="x", pady=(8, 0))
        ttk.Label(ifrow, text="最低影响因子：").pack(side="left")
        self.min_if_var = tk.DoubleVar(value=self.settings.min_impact_factor)
        self.if_scale = ttk.Scale(ifrow, from_=0, to=50, orient="horizontal", length=260,
                                  variable=self.min_if_var, command=self._on_if_scale)
        self.if_scale.pack(side="left")
        self.if_value_label = ttk.Label(ifrow, text="不限", width=8, style="Card.TLabel")
        self.if_value_label.pack(side="left", padx=(6, 14))
        ttk.Label(ifrow, text="精确值：").pack(side="left")
        self.min_if_entry_var = tk.StringVar(value="0")
        min_entry = ttk.Entry(ifrow, textvariable=self.min_if_entry_var, width=7)
        min_entry.pack(side="left")
        min_entry.bind("<Return>", lambda _e: self._apply_if_entry())
        min_entry.bind("<FocusOut>", lambda _e: self._apply_if_entry())
        ttk.Label(ifrow, text="最高：").pack(side="left", padx=(10, 0))
        self.max_if_entry_var = tk.StringVar(value="" if not self.settings.max_impact_factor else f"{self.settings.max_impact_factor:g}")
        max_entry = ttk.Entry(ifrow, textvariable=self.max_if_entry_var, width=7)
        max_entry.pack(side="left")
        max_entry.bind("<Return>", lambda _e: self._apply_if_entry())
        max_entry.bind("<FocusOut>", lambda _e: self._apply_if_entry())

        note = ttk.Label(
            jf,
            text="影响因子来源：内置 JCR 精简库（2022–2025）+ 你导入的数据；"
                 "未收录期刊以 OpenAlex 两年篇均被引估算并标注。",
            style="Sub.TLabel", wraplength=980, justify="left")
        note.pack(anchor="w", pady=(6, 0))

        # 其它条件
        more = ttk.Labelframe(parent, text="其它条件", padding=8)
        more.pack(fill="x", pady=6)
        row = ttk.Frame(more)
        row.pack(fill="x")
        ttk.Label(row, text="年份：").pack(side="left")
        self.year_from_var = tk.StringVar(value=str(self.settings.year_from or ""))
        ttk.Entry(row, textvariable=self.year_from_var, width=7).pack(side="left")
        ttk.Label(row, text="—").pack(side="left", padx=2)
        self.year_to_var = tk.StringVar(value=str(self.settings.year_to or ""))
        ttk.Entry(row, textvariable=self.year_to_var, width=7).pack(side="left")

        ttk.Label(row, text="   结果上限：").pack(side="left")
        self.limit_var = tk.StringVar(value=str(self.settings.result_limit))
        ttk.Combobox(row, textvariable=self.limit_var, width=6, state="readonly",
                     values=["20", "30", "50", "80", "100", "150", "200"]).pack(side="left")

        self.oa_var = tk.BooleanVar(value=self.settings.only_open_access)
        ttk.Checkbutton(row, text="只要开放获取", variable=self.oa_var).pack(side="left", padx=(14, 0))

        row2 = ttk.Frame(more)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Label(row2, text="标题包含：").pack(side="left")
        self.title_contains_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.title_contains_var, width=24).pack(side="left", padx=(0, 12))
        ttk.Label(row2, text="期刊名包含：").pack(side="left")
        self.journal_contains_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.journal_contains_var, width=24).pack(side="left")

    # ------------------------------------------------------------------ #
    def _build_results(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent, padding=(0, 8, 0, 4))
        bar.pack(fill="x")
        self.result_label = ttk.Label(bar, text="尚未检索", style="Section.TLabel")
        self.result_label.pack(side="left")
        self.select_label = ttk.Label(bar, text="已选 0 条", style="Sub.TLabel")
        self.select_label.pack(side="left", padx=(10, 0))

        ttk.Button(bar, text="全选", width=6, command=self.select_all).pack(side="right")
        ttk.Button(bar, text="反选", width=6, command=self.invert_selection).pack(side="right", padx=4)
        ttk.Button(bar, text="清空选择", width=9, command=self.clear_selection).pack(side="right", padx=(0, 4))
        ttk.Button(bar, text="勾选可下载项", command=self.select_downloadable).pack(side="right", padx=(0, 8))

        filt = ttk.Frame(parent)
        filt.pack(fill="x", pady=(0, 4))
        ttk.Label(filt, text="结果内筛选：", style="Sub.TLabel").pack(side="left")
        self.filter_var = tk.StringVar()
        filter_entry = ttk.Entry(filt, textvariable=self.filter_var, width=30)
        filter_entry.pack(side="left", padx=(0, 6))
        filter_entry.bind("<KeyRelease>", lambda _e: self._apply_filter())
        Tooltip(filter_entry, "在当前结果中按标题 / 期刊 / 作者 / DOI / 来源快速筛选")
        ttk.Button(filt, text="清除", width=6,
                   command=lambda: (self.filter_var.set(""), self._apply_filter())).pack(side="left")
        self.only_selected_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(filt, text="只看已勾选", variable=self.only_selected_var,
                        command=self._apply_filter).pack(side="left", padx=(10, 0))
        self.filter_label = ttk.Label(filt, text="", style="Sub.TLabel")
        self.filter_label.pack(side="left", padx=(10, 0))

        table = ScrolledTreeview(parent, RESULT_COLUMNS)
        table.pack(fill="both", expand=True)
        self.table = table.tree
        self.table.configure(height=7)  # 最小行数，窗口变大时由 expand 拉高
        self.table.bind("<Button-1>", self._on_table_click)
        self.table.bind("<space>", lambda _e: self.toggle_selected_rows())
        self.table.bind("<Double-1>", lambda _e: self.show_details())
        self.table.bind("<Button-3>", self._show_context_menu)
        self.table.bind("<Control-a>", lambda _e: (self.select_all(), "break")[1])

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="查看详情", command=self.show_details)
        menu.add_command(label="勾选/取消勾选", command=self.toggle_selected_rows)
        menu.add_separator()
        menu.add_command(label="下载这一条", command=self.download_current)
        menu.add_command(label="复制 DOI", command=lambda: self._copy_field("doi"))
        menu.add_command(label="复制标题", command=lambda: self._copy_field("title"))
        menu.add_command(label="打开链接", command=self.open_landing)
        menu.add_command(label="打开 PDF 链接", command=self.open_pdf)
        self.context_menu = menu

        log_wrap = ttk.Labelframe(parent, text="检索日志", padding=6, height=122)
        log_wrap.pack(fill="x", expand=False, pady=(6, 0))
        log_wrap.pack_propagate(False)
        self.log_text = tk.Text(log_wrap, height=4, wrap="word", relief="flat",
                                background=PALETTE["panel"], font=pick_font(self, 9), padx=6, pady=4)
        log_vsb = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_vsb.set, state="disabled")
        self.log_text.pack(side="left", fill="both", expand=True)
        log_vsb.pack(side="right", fill="y")

    def _build_status(self) -> None:
        bar = ttk.Frame(self, padding=(12, 6))
        bar.pack(fill="x", side="bottom")
        self.status_label = ttk.Label(bar, text="就绪", style="Status.TLabel")
        self.status_label.pack(side="left")
        self.busy = BusyIndicator(bar)
        self.busy.pack(side="right")

    # ================================================================== #
    # 状态加载/保存
    # ================================================================== #
    def _load_state(self) -> None:
        self.journals: list[str] = list(self.settings.journal_names)
        self._update_journal_label()
        self._update_if_label()
        self._refresh_history()
        self.site_profiles = self._load_site_profiles()
        self.log(f"数据目录：{self.config_obj.base}")
        self.log("提示：勾选数据源并输入关键词后点击「检索」（Ctrl+Enter 亦可）。")

    def _load_site_profiles(self) -> list[SiteProfile]:
        raw = self.config_obj.sites.items
        if not raw:
            profiles = [SiteProfile.from_dict(x) for x in BUILTIN_PROFILES]
            self.config_obj.sites.replace([p.to_dict() for p in profiles])
            return profiles
        return [SiteProfile.from_dict(x) for x in raw]

    def _collect_settings(self) -> None:
        s = self.settings
        s.sources = [name for name, var in self.source_vars.items() if var.get()]
        s.journal_names = list(self.journals)
        s.fuzzy_journal = bool(self.fuzzy_journal_var.get())
        s.fuzzy_keyword = bool(self.fuzzy_keyword_var.get())
        s.min_impact_factor = float(self.min_if_var.get() or 0)
        s.max_impact_factor = _safe_float(self.max_if_entry_var.get(), 0.0)
        s.year_from = _safe_int(self.year_from_var.get(), 0)
        s.year_to = _safe_int(self.year_to_var.get(), 0)
        s.result_limit = _safe_int(self.limit_var.get(), 50)
        s.only_open_access = bool(self.oa_var.get())
        s.download_dir = self.dir_var.get().strip()
        s.last_keyword = self.keyword_var.get().strip()
        self.config_obj.save_settings()

    # ================================================================== #
    # 检索
    # ================================================================== #
    def start_search(self) -> None:
        if self._searching:
            messagebox.showinfo("提示", "已有检索正在进行，请稍候或点击「停止」。", parent=self)
            return
        keyword = self.keyword_var.get().strip()
        if not keyword:
            messagebox.showwarning("需要关键词", "请输入检索关键词（支持模糊检索）。", parent=self)
            self.keyword_entry.focus_set()
            return
        self._collect_settings()
        query = self._build_query(keyword)
        if not query.sources:
            messagebox.showwarning("未选数据源", "请至少勾选一个数据源。", parent=self)
            return

        self.last_query = query
        self._searching = True
        self._stop_requested = False
        self.search_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.busy.start("正在检索…")
        self.log("=" * 60)
        self.log(f"检索条件：{query.describe()}")
        self._clear_table()
        self.result_label.configure(text="检索中…")

        def task(worker: Worker) -> None:
            result = self.engine.search(
                query,
                mailto=self.settings.openalex_mailto or self.settings.crossref_mailto,
                pubmed_key="",
                site_profiles=self.site_profiles,
                timeout=self.settings.request_timeout,
                progress=lambda msg: worker.post("log", msg),
                should_stop=lambda: self._stop_requested,
            )
            worker.post("result", result)

        self.worker.run(task, on_message=self._on_search_message, on_done=self._on_search_done)

    def _build_query(self, keyword: str) -> SearchQuery:
        return SearchQuery(
            keyword=keyword,
            sources=[name for name, var in self.source_vars.items() if var.get()],
            journals=list(self.journals),
            min_impact_factor=float(self.min_if_var.get() or 0),
            max_impact_factor=_safe_float(self.max_if_entry_var.get(), 0.0),
            year_from=_safe_int(self.year_from_var.get(), 0),
            year_to=_safe_int(self.year_to_var.get(), 0),
            limit=_safe_int(self.limit_var.get(), 50),
            fuzzy_keyword=bool(self.fuzzy_keyword_var.get()),
            fuzzy_journal=bool(self.fuzzy_journal_var.get()),
            only_open_access=bool(self.oa_var.get()),
            title_contains=self.title_contains_var.get().strip(),
            journal_contains=self.journal_contains_var.get().strip(),
            if_year=self.settings.if_data_year,
        )

    def _on_search_message(self, kind: str, payload: Any) -> None:
        if kind == "log":
            self.log(str(payload))
        elif kind == "result":
            self._show_result(payload)
        elif kind == "error":
            self.log(f"❌ 检索异常：{payload}")
            messagebox.showerror("检索失败", str(payload), parent=self)

    def _on_search_done(self) -> None:
        self._searching = False
        self.search_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.busy.stop("就绪")

    def _show_result(self, result) -> None:
        self.papers = list(result.papers)
        self.visible = list(self.papers)
        self.selected.clear()
        if hasattr(self, "filter_var"):
            self.filter_var.set("")
            self.only_selected_var.set(False)
        self._fill_table()
        total_raw = result.total_raw
        self.result_label.configure(
            text=f"命中 {len(self.papers)} 条 / 原始 {total_raw} 条 · 耗时 {result.elapsed:.1f}s")
        for report in result.reports:
            mark = "✗" if report.error else "✓"
            self.log(f"  {mark} {report.name}：{report.status}")
        if not self.papers:
            self.log("没有符合条件的结果。可尝试：降低最低影响因子、放宽期刊限制、或勾选更多数据源。")

        entry = self.config_obj.history.add(
            HistoryEntry(
                timestamp=time.time(),
                keyword=result.keyword,
                sources=list(self.last_query.sources) if self.last_query else [],
                journals=list(self.journals),
                min_if=float(self.min_if_var.get() or 0),
                extra=(self.title_contains_var.get().strip()),
                result_count=len(self.papers),
            )
        )
        self._refresh_history()
        self._update_selection_label()

    # ------------------------------------------------------------------ #
    def _fill_table(self) -> None:
        """按当前筛选条件重建结果表。"""
        self._clear_table()
        keyword = self.filter_var.get().strip().lower() if hasattr(self, "filter_var") else ""
        only_selected = bool(self.only_selected_var.get()) if hasattr(self, "only_selected_var") else False
        self.visible: list[Paper] = []
        for paper in self.papers:
            if only_selected and paper.key not in self.selected:
                continue
            if keyword:
                haystack = " ".join(
                    [paper.title, paper.journal, paper.doi, paper.source,
                     " ".join(paper.authors[:6]), " ".join(paper.extra.get("all_sources") or [])]
                ).lower()
                if keyword not in haystack:
                    continue
            self.visible.append(paper)
        for idx, paper in enumerate(self.visible, start=1):
            iid = f"p{idx}"
            mark = CHECK_ON if paper.key in self.selected else CHECK_OFF
            self.table.insert("", "end", iid=iid, values=self._row_values(paper, idx, mark),
                              tags=("odd" if idx % 2 else "even",))
            self.item_ids.append(iid)
        if hasattr(self, "filter_label"):
            if len(self.visible) != len(self.papers):
                self.filter_label.configure(text=f"（显示 {len(self.visible)} / {len(self.papers)} 条）")
            else:
                self.filter_label.configure(text="")
        self._update_selection_label()

    def _apply_filter(self) -> None:
        self._fill_table()

    @staticmethod
    def _row_values(paper: Paper, idx: int, mark: str = CHECK_OFF) -> tuple:
        return (
            mark,
            idx,
            paper.title[:220],
            paper.journal or "—",
            paper.if_text,
            paper.year or "—",
            paper.cited_by,
            "是" if paper.is_oa else "否",
            "✔" if paper.has_pdf else "—",
            "、".join(paper.extra.get("all_sources") or [paper.source]),
        )

    def _clear_table(self) -> None:
        self.table.delete(*self.table.get_children())
        self.item_ids = []

    def _paper_at(self, iid: str) -> Paper | None:
        try:
            idx = int(iid[1:]) - 1
            return self.visible[idx]
        except (ValueError, IndexError, AttributeError):
            return None

    def _current_paper(self) -> Paper | None:
        sel = self.table.selection()
        if sel:
            return self._paper_at(sel[0])
        return None

    def _on_table_click(self, event) -> None:
        iid = self.table.identify_row(event.y)
        if not iid:
            return
        if self.table.identify_column(event.x) == "#1":
            self._toggle(iid)
            return "break"
        return None

    def _toggle(self, iid: str) -> None:
        paper = self._paper_at(iid)
        if not paper:
            return
        if paper.key in self.selected:
            self.selected.discard(paper.key)
            mark = CHECK_OFF
        else:
            self.selected.add(paper.key)
            mark = CHECK_ON
        self.table.set(iid, "sel", mark)
        self._update_selection_label()

    def toggle_selected_rows(self) -> None:
        for iid in self.table.selection():
            self._toggle(iid)
        return "break"

    def select_all(self) -> None:
        for iid in self.item_ids:
            paper = self._paper_at(iid)
            if paper:
                self.selected.add(paper.key)
                self.table.set(iid, "sel", CHECK_ON)
        self._update_selection_label()

    def clear_selection(self) -> None:
        self.selected.clear()
        for iid in self.item_ids:
            self.table.set(iid, "sel", CHECK_OFF)
        self._update_selection_label()

    def invert_selection(self) -> None:
        for iid in self.item_ids:
            self._toggle(iid)

    def select_downloadable(self) -> None:
        self.clear_selection()
        for iid in self.item_ids:
            paper = self._paper_at(iid)
            if paper and paper.has_pdf:
                self.selected.add(paper.key)
                self.table.set(iid, "sel", CHECK_ON)
        self._update_selection_label()
        if not self.selected:
            messagebox.showinfo("提示", "当前结果中没有带开放获取 PDF 直链的文献，"
                                        "可尝试勾选更多数据源或使用「导出链接」。", parent=self)

    def _update_selection_label(self) -> None:
        count = len(self.selected)
        self.select_label.configure(text=f"已选 {count} 条")
        self.download_btn.configure(text=f"下载勾选文献 ({count})")

    def _show_context_menu(self, event) -> None:
        iid = self.table.identify_row(event.y)
        if iid:
            if iid not in self.table.selection():
                self.table.selection_set(iid)
            self.context_menu.tk_popup(event.x_root, event.y_root)

    def _copy_field(self, field: str) -> None:
        paper = self._current_paper()
        if not paper:
            return
        value = getattr(paper, field, "")
        self.clipboard_clear()
        self.clipboard_append(value)
        self.log(f"已复制：{value[:80]}")

    # ------------------------------------------------------------------ #
    def show_details(self) -> None:
        paper = self._current_paper()
        if not paper:
            messagebox.showinfo("提示", "请先选中一条结果。", parent=self)
            return
        lines = [
            f"标题：{paper.title}",
            "",
            f"作者：{paper.authors_short or '—'}",
            f"期刊：{paper.journal or '—'}（ISSN：{', '.join(paper.issns) or '—'}）",
            f"年份：{paper.year or '—'}    被引：{paper.cited_by}",
            f"影响因子：{paper.if_text}    来源：{paper.impact_factor_source or '—'}"
            + (f"（{paper.impact_factor_year} 年度）" if paper.impact_factor_year else ""),
            f"开放获取：{'是' if paper.is_oa else '否'}"
            + (f"（{paper.oa_status}）" if paper.oa_status else ""),
            f"类型：{paper.pub_type or '—'}",
            f"数据源：{'、'.join(paper.extra.get('all_sources') or [paper.source])}",
            f"DOI：{paper.doi or '—'}",
            f"页面：{paper.url or '—'}",
            "",
            "PDF 候选地址：",
        ]
        lines += [f"  {i}. {u}" for i, u in enumerate(paper.pdf_urls, 1)] or ["  （无）"]
        lines += ["", "摘要：", paper.abstract or "（该数据源未提供摘要）"]
        TextDialog(self, "文献详情", "\n".join(lines))

    def open_landing(self) -> None:
        paper = self._current_paper()
        if paper and paper.url:
            _open_url(paper.url)
        elif paper:
            messagebox.showinfo("提示", "这条结果没有页面链接。", parent=self)

    def open_pdf(self) -> None:
        paper = self._current_paper()
        if paper and paper.best_pdf:
            _open_url(paper.best_pdf)
        elif paper:
            messagebox.showinfo("提示", "这条结果没有可直接打开的 PDF 链接。", parent=self)

    # ================================================================== #
    # 下载
    # ================================================================== #
    def choose_dir(self) -> None:
        initial = self.dir_var.get() or str(Path.home())
        path = filedialog.askdirectory(title="选择下载保存目录", initialdir=initial, parent=self)
        if path:
            self.dir_var.set(path)
            self.settings.download_dir = path
            self.config_obj.save_settings()

    def selected_papers(self) -> list[Paper]:
        return [p for p in self.papers if p.key in self.selected]
    def start_download(self) -> None:
        if self._downloading:
            messagebox.showinfo("提示", "正在下载中，请稍候。", parent=self)
            return
        papers = self.selected_papers()
        if not papers:
            messagebox.showinfo("提示", "请先在结果列表中勾选要下载的文献。", parent=self)
            return
        directory = self.dir_var.get().strip()
        if not directory:
            self.choose_dir()
            directory = self.dir_var.get().strip()
            if not directory:
                return
        try:
            Path(directory).mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("目录不可用", f"无法创建/写入目录：\n{directory}\n\n{exc}", parent=self)
            return

        without_pdf = [p for p in papers if not p.has_pdf]
        if without_pdf:
            proceed = messagebox.askyesno(
                "部分文献没有直链 PDF",
                f"有 {len(without_pdf)} 条文献没有开放获取 PDF 直链，程序会尝试通过 DOI 抓取，"
                f"通常无法成功。\n\n是否继续下载其余 {len(papers) - len(without_pdf)} 条？",
                parent=self,
            )
            if not proceed:
                papers = [p for p in papers if p.has_pdf]
                if not papers:
                    return

        style_map = {"年份_作者_标题": "year_author_title", "仅标题": "title", "年份_作者_DOI": "doi"}
        self.downloader = Downloader(
            directory,
            workers=self.settings.concurrent_downloads,
            timeout=max(30.0, self.settings.request_timeout),
            filename_style=style_map.get(self.filename_style_var.get(), "year_author_title"),
        )
        self._downloading = True
        self.stop_btn.configure(state="normal")
        self.busy.start("正在下载…")
        self.log(f"开始下载 {len(papers)} 篇到：{directory}")

        def task(worker: Worker) -> None:
            summary = self.downloader.download_all(
                papers,
                progress=lambda done, total, t: worker.post(
                    "progress",
                    (done, total, t.paper.title[:60], t.status, t.message),
                ),
            )
            worker.post("summary", summary)

        self.worker.run(task, on_message=self._on_download_message, on_done=self._on_download_done)

    def _on_download_message(self, kind: str, payload: Any) -> None:
        if kind == "progress":
            done, total, title, status, message = payload
            self.busy.start(f"下载中 {done}/{total}")
            icon = {"ok": "✅", "skipped": "⏭", "failed": "❌", "no_pdf": "🚫"}.get(status, "•")
            self.log(f"  {icon} [{done}/{total}] {title} — {message}")
        elif kind == "summary":
            self._show_download_summary(payload)
        elif kind == "error":
            self.log(f"❌ 下载异常：{payload}")
            messagebox.showerror("下载失败", str(payload), parent=self)

    def _on_download_done(self) -> None:
        self._downloading = False
        self.downloader = None
        self.stop_btn.configure(state="disabled")
        self.busy.stop("就绪")

    def _show_download_summary(self, summary: DownloadSummary) -> None:
        failed = summary.failed
        text = (f"下载完成：成功 {summary.ok_count} 篇，跳过（已存在）{summary.skipped} 篇，"
                f"失败 {len(failed)} 篇，共 {summary.total_bytes/1024/1024:.2f} MB。")
        self.log(text)
        self.result_label.configure(text=text)
        if self.auto_confirm:  # 自动化/无头模式：不弹窗、不打开目录
            return
        if failed:
            detail = "\n".join(f"• {t.paper.title[:70]}\n   原因：{t.message}" for t in failed[:12])
            if len(failed) > 12:
                detail += f"\n… 另有 {len(failed) - 12} 条失败"
            TextDialog(self, "下载失败的文献", text + "\n\n" + detail)
        else:
            messagebox.showinfo("下载完成", text, parent=self)
        if summary.directory:
            open_path(summary.directory)

    def download_current(self) -> None:
        paper = self._current_paper()
        if not paper:
            return
        self.selected = {paper.key}
        iid = self.table.selection()[0] if self.table.selection() else None
        if iid:
            self.table.set(iid, "sel", CHECK_ON)
        self._update_selection_label()
        self.start_download()

    def stop_all(self) -> None:
        self._stop_requested = True
        if self.downloader:
            self.downloader.cancel()
            self.log("已请求停止下载…")
        if self._searching:
            self.log("已请求停止检索…（正在返回的请求会很快结束）")

    # ================================================================== #
    # 导出
    # ================================================================== #
    def export_results(self, fmt: str = "csv") -> None:
        if not self.papers:
            messagebox.showinfo("提示", "当前没有可导出的结果。", parent=self)
            return
        ext = "csv" if fmt == "csv" else "xlsx"
        path = filedialog.asksaveasfilename(
            title="导出检索结果", defaultextension=f".{ext}",
            filetypes=[("CSV 文件", "*.csv")] if fmt == "csv" else [("Excel 工作簿", "*.xlsx")],
            initialfile=f"litcrawler_results_{time.strftime('%Y%m%d_%H%M')}.{ext}", parent=self,
        )
        if not path:
            return
        try:
            _write_results(Path(path), self.papers, fmt)
            self.log(f"已导出 {len(self.papers)} 条到 {path}")
            if messagebox.askyesno("导出完成", f"已导出 {len(self.papers)} 条结果。\n\n是否打开文件？", parent=self):
                open_path(path)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def export_selected_links(self) -> None:
        papers = self.selected_papers() or self.papers
        if not papers:
            messagebox.showinfo("提示", "没有可导出的内容。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            title="导出链接清单", defaultextension=".txt", filetypes=[("文本文件", "*.txt")],
            initialfile=f"litcrawler_links_{time.strftime('%Y%m%d_%H%M')}.txt", parent=self,
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            for paper in papers:
                fh.write(f"{paper.title}\n  PDF: {paper.best_pdf or '（无）'}\n  DOI: {paper.doi or '（无）'}\n\n")
        self.log(f"链接清单已保存：{path}")
        if messagebox.askyesno("导出完成", "链接清单已保存，是否打开？", parent=self):
            open_path(path)

    # ================================================================== #
    # 历史记录
    # ================================================================== #
    def _refresh_history(self) -> None:
        self.history_list.delete(0, "end")
        for entry in self.config_obj.history.entries:
            self.history_list.insert("end", entry.summary())

    def rerun_history(self) -> None:
        selection = self.history_list.curselection()
        if not selection:
            messagebox.showinfo("提示", "请在左侧选择一条搜索记录。", parent=self)
            return
        entry = self.config_obj.history.entries[selection[0]]
        self.keyword_var.set(entry.keyword)
        for name, var in self.source_vars.items():
            var.set(name in entry.sources if entry.sources else var.get())
        self.journals = list(entry.journals)
        self._update_journal_label()
        self.min_if_var.set(entry.min_if)
        self.min_if_entry_var.set(f"{entry.min_if:g}")
        self._update_if_label()
        self.title_contains_var.set(entry.extra or "")
        self.log(f"已恢复搜索记录：{entry.summary()}")
        self.start_search()

    def delete_history(self) -> None:
        selection = list(self.history_list.curselection())
        if not selection:
            return
        self.config_obj.history.remove(selection)
        self._refresh_history()

    def clear_history(self) -> None:
        if not self.config_obj.history.entries:
            return
        if messagebox.askyesno("确认", "确定要清空全部搜索记录吗？", parent=self):
            self.config_obj.history.clear()
            self._refresh_history()

    # ================================================================== #
    # 期刊与影响因子
    # ================================================================== #
    def choose_journals(self) -> None:
        result = JournalPickerDialog.ask(self, self.config_obj.journals, self.journals)
        if result is None:
            return
        self.journals = result
        self.settings.journal_names = list(result)
        self.config_obj.save_settings()
        self._update_journal_label()

    def clear_journals(self) -> None:
        self.journals = []
        self.settings.journal_names = []
        self.config_obj.save_settings()
        self._update_journal_label()

    def _update_journal_label(self) -> None:
        if not self.journals:
            self.journal_label.configure(text="不限（所有期刊）")
        else:
            shown = "、".join(self.journals[:3])
            if len(self.journals) > 3:
                shown += f" 等 {len(self.journals)} 种"
            self.journal_label.configure(text=shown)

    def _on_if_scale(self, _value=None) -> None:
        value = float(self.min_if_var.get())
        self.min_if_entry_var.set(f"{value:g}" if value >= 0.1 else "0")
        self._update_if_label()

    def _apply_if_entry(self) -> None:
        value = _safe_float(self.min_if_entry_var.get(), 0.0)
        value = max(0.0, min(50.0, value))
        self.min_if_var.set(value)
        self.min_if_entry_var.set(f"{value:g}")
        self._update_if_label()

    def _update_if_label(self) -> None:
        value = float(self.min_if_var.get() or 0)
        self.if_value_label.configure(text="不限" if value < 0.05 else f"≥{value:g}")

    def _preload_impact_db(self) -> None:
        def work(worker: Worker) -> None:
            try:
                count = self.impact_db.load_bundle() + self.impact_db.load_cache()
                worker.post("if_loaded", count)
            except Exception as exc:
                worker.post("log", f"影响因子库加载失败：{exc}")

        self.worker.run(work, on_message=self._on_preload, on_done=lambda: None)

    def _on_preload(self, kind: str, payload: Any) -> None:
        if kind == "if_loaded":
            years = "、".join(self.impact_db.available_years())
            self.log(f"影响因子库已就绪：{payload} 条记录（年份：{years or '未知'}）")
        else:
            self.log(str(payload))

    def import_impact_factor(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择影响因子数据文件",
            filetypes=[("表格/JSON", "*.csv *.tsv *.txt *.json *.xlsx"), ("所有文件", "*.*")],
            parent=self,
        )
        if not paths:
            return
        total = 0
        errors = []
        for path in paths:
            try:
                total += self.impact_db.import_file(path)
            except Exception as exc:
                errors.append(f"{Path(path).name}：{exc}")
        self.log(f"影响因子导入完成，共 {total} 条。")
        if errors:
            messagebox.showwarning("部分文件导入失败", "\n".join(errors), parent=self)
        else:
            messagebox.showinfo("导入成功", f"已导入 {total} 条影响因子记录。", parent=self)

    def download_full_if(self) -> None:
        if not messagebox.askyesno(
            "下载完整数据集",
            "将从公开镜像下载完整 JCR 数据集（约 40 MB，年度 2019–2025），"
            "下载后仅载入最新年度的指标到内存。\n\n是否继续？",
            parent=self,
        ):
            return
        target = self.config_obj.if_import_dir / "journal_jcr_full.csv"
        self.busy.start("下载影响因子数据…")

        def task(worker: Worker) -> None:
            path = self.impact_db.download_full_dataset(
                target, progress=lambda msg: worker.post("log", msg)
            )
            worker.post("log", f"完整数据集已保存：{path}")

        self.worker.run(task, on_message=lambda kind, payload: self.log(str(payload)) if kind == "log" else None,
                        on_done=lambda: self.busy.stop("就绪"))

    def show_if_status(self) -> None:
        stats = self.impact_db.stats
        lines = [
            f"记录数：{stats['rows']}",
            f"内置库记录：{stats['bundle_rows']}",
            f"覆盖年份：{'、'.join(stats['years']) or '—'}",
            f"数据目录：{self.config_obj.base}",
            "",
            "已导入文件：",
        ]
        lines += [f"  • {x}" for x in stats["imported"]] or ["  （无）"]
        lines += [
            "",
            "说明：",
            "• 内置库来自 MIT 许可的公开 JCR 整理数据（2022–2025），仅供筛选参考；",
            "• 如需权威指标，请从 JCR 官网导出后使用「导入影响因子数据」；",
            "• 未收录期刊会以 OpenAlex 两年篇均被引估算，并在结果中标注来源。",
        ]
        TextDialog(self, "影响因子库状态", "\n".join(lines))

    # ================================================================== #
    # 设置 / 工具
    # ================================================================== #
    def open_settings(self) -> None:
        SettingsDialog(self)

    def check_sources(self) -> None:
        names = list(self.source_vars)
        self.busy.start("正在自检数据源…")
        self.log("开始数据源自检…")

        def task(worker: Worker) -> None:
            results = self.engine.probe_sources(names, mailto=self.settings.openalex_mailto)
            worker.post("probe", results)

        def on_message(kind: str, payload: Any) -> None:
            if kind == "probe":
                lines = [f"{name}：{text}" for name, text in payload.items()]
                self.log("；".join(lines))
                TextDialog(self, "数据源连通性自检", "\n".join(lines))
            elif kind == "error":
                self.log(f"自检异常：{payload}")

        self.worker.run(task, on_message=on_message, on_done=lambda: self.busy.stop("就绪"))

    def show_help(self) -> None:
        TextDialog(self, "使用说明", HELP_TEXT)

    def show_about(self) -> None:
        TextDialog(
            self,
            "关于",
            f"{__app_name_cn__} LitCrawler v{__version__}\n\n"
            "一个用于学术文献检索与批量下载的桌面工具。\n"
            "· 多源并发检索（OpenAlex / Crossref / Europe PMC / arXiv / DOAJ / PubMed）\n"
            "· 关键词与期刊名模糊匹配\n"
            "· 影响因子筛选（内置 JCR 精简库 + 导入 + OpenAlex 估算）\n"
            "· 勾选后批量下载开放获取 PDF\n\n"
            "数据来源均为公开学术 API，请遵守各站点的使用条款。\n"
            "本工具不绕过付费墙，下载仅限开放获取或你有权访问的内容。",
        )

    # ================================================================== #
    # 杂项
    # ================================================================== #
    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.status_label.configure(text=message[:110])

    def _on_close(self) -> None:
        try:
            self._collect_settings()
            if self.downloader:
                self.downloader.cancel()
            self.worker.stop_polling()
        finally:
            self.destroy()


# ====================================================================== #
# 设置对话框
# ====================================================================== #
class SettingsDialog(tk.Toplevel):
    """程序设置：下载、网络、自定义站点。"""

    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        self.title("设置")
        self.geometry("820x620")
        self.transient(app)
        self.grab_set()

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        self._build_general(nb)
        self._build_sites(nb)

        btns = ttk.Frame(self, padding=(10, 0, 10, 10))
        btns.pack(fill="x")
        ttk.Button(btns, text="关闭", command=self._close).pack(side="right")
        ttk.Button(btns, text="保存", style="Accent.TButton", command=self._save).pack(side="right", padx=6)

    def _build_general(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb, padding=14)
        nb.add(frame, text="通用")
        s = self.app.settings

        ttk.Label(frame, text="联系邮箱（OpenAlex/Crossref 礼貌池，可留空）", style="Section.TLabel").pack(anchor="w")
        self.mailto_var = tk.StringVar(value=s.openalex_mailto)
        ttk.Entry(frame, textvariable=self.mailto_var, width=48).pack(anchor="w", pady=(2, 10))

        ttk.Label(frame, text="并发下载数（1–8）", style="Section.TLabel").pack(anchor="w")
        self.workers_var = tk.IntVar(value=s.concurrent_downloads)
        ttk.Spinbox(frame, from_=1, to=8, textvariable=self.workers_var, width=6).pack(anchor="w", pady=(2, 10))

        ttk.Label(frame, text="网络超时（秒）", style="Section.TLabel").pack(anchor="w")
        self.timeout_var = tk.DoubleVar(value=s.request_timeout)
        ttk.Spinbox(frame, from_=10, to=120, increment=5, textvariable=self.timeout_var, width=6).pack(anchor="w", pady=(2, 10))

        ttk.Label(frame, text="影响因子数据年份（留空=最新）", style="Section.TLabel").pack(anchor="w")
        self.if_year_var = tk.StringVar(value=s.if_data_year)
        ttk.Entry(frame, textvariable=self.if_year_var, width=10).pack(anchor="w", pady=(2, 10))

        ttk.Label(frame, text=f"数据目录：{self.app.config_obj.base}", style="Sub.TLabel").pack(anchor="w", pady=(10, 0))
        row = ttk.Frame(frame)
        row.pack(anchor="w", pady=4)
        ttk.Button(row, text="打开数据目录",
                   command=lambda: open_path(self.app.config_obj.base)).pack(side="left")
        ttk.Button(row, text="打开日志目录",
                   command=lambda: open_path(logs_dir())).pack(side="left", padx=4)
        ttk.Button(row, text="打开影响因子目录",
                   command=lambda: open_path(self.app.config_obj.if_import_dir)).pack(side="left")

    def _build_sites(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb, padding=10)
        nb.add(frame, text="自定义站点")
        ttk.Label(frame, text="用「检索地址模板 + 字段正则」把任意可抓取的期刊网站接入检索。",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 6))

        self.sites_tree = ttk.Treeview(frame, columns=("on", "name", "url"), show="headings", height=8)
        self.sites_tree.heading("on", text="启用")
        self.sites_tree.heading("name", text="站点名称")
        self.sites_tree.heading("url", text="检索地址模板")
        self.sites_tree.column("on", width=50, anchor="center")
        self.sites_tree.column("name", width=140)
        self.sites_tree.column("url", width=520)
        self.sites_tree.pack(fill="both", expand=True)

        for profile in self.app.site_profiles:
            self.sites_tree.insert("", "end", values=("☑" if profile.enabled else "☐",
                                                      profile.name, profile.url_template))

        editor = ttk.Labelframe(frame, text="编辑所选站点", padding=8)
        editor.pack(fill="both", expand=True, pady=(8, 0))
        fields = [
            ("name", "名称"), ("url_template", "检索地址模板（含 {query}）"), ("base_url", "站点根地址"),
            ("list_regex", "结果区块正则"), ("title_regex", "标题正则"), ("authors_regex", "作者正则"),
            ("journal_regex", "期刊正则"), ("doi_regex", "DOI 正则"), ("pdf_regex", "PDF 正则"),
        ]
        self.site_vars: dict[str, tk.StringVar] = {}
        for idx, (key, label) in enumerate(fields):
            ttk.Label(editor, text=label, width=24, anchor="e").grid(row=idx, column=0, sticky="e", pady=1)
            var = tk.StringVar()
            self.site_vars[key] = var
            ttk.Entry(editor, textvariable=var).grid(row=idx, column=1, sticky="ew", pady=1)
        ttk.Label(editor, text="最大页数", width=24, anchor="e").grid(row=len(fields), column=0, sticky="e")
        self.max_pages_var = tk.StringVar(value="1")
        ttk.Entry(editor, textvariable=self.max_pages_var, width=6).grid(row=len(fields), column=1, sticky="w")
        editor.columnconfigure(1, weight=1)

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="新增站点", command=self._add_site).pack(side="left")
        ttk.Button(row, text="保存选中所选", command=self._save_site).pack(side="left", padx=4)
        ttk.Button(row, text="启用/停用", command=self._toggle_site).pack(side="left")
        ttk.Button(row, text="删除", command=self._delete_site).pack(side="left", padx=4)
        self.sites_tree.bind("<<TreeviewSelect>>", self._on_site_select)
        self.sites_tree.bind("<Button-1>", self._on_site_click)

    # ------------------------------------------------------------------ #
    def _selected_site_index(self) -> int:
        sel = self.sites_tree.selection()
        if not sel:
            return -1
        return self.sites_tree.index(sel[0])

    def _on_site_select(self, _event=None) -> None:
        idx = self._selected_site_index()
        if idx < 0 or idx >= len(self.app.site_profiles):
            return
        profile = self.app.site_profiles[idx]
        for key, var in self.site_vars.items():
            var.set(str(getattr(profile, key, "") or ""))
        self.max_pages_var.set(str(profile.max_pages))

    def _on_site_click(self, event) -> None:
        iid = self.sites_tree.identify_row(event.y)
        if iid and self.sites_tree.identify_column(event.x) == "#1":
            self.sites_tree.selection_set(iid)
            self._toggle_site()

    def _toggle_site(self) -> None:
        idx = self._selected_site_index()
        if idx < 0:
            return
        profile = self.app.site_profiles[idx]
        profile.enabled = not profile.enabled
        self.sites_tree.set(self.sites_tree.get_children()[idx], "on", "☑" if profile.enabled else "☐")

    def _add_site(self) -> None:
        profile = SiteProfile("新站点", "https://example.com/search?q={query}&p={page}")
        self.app.site_profiles.append(profile)
        self.sites_tree.insert("", "end", values=("☑", profile.name, profile.url_template))

    def _save_site(self) -> None:
        idx = self._selected_site_index()
        if idx < 0:
            messagebox.showinfo("提示", "请先在上方列表中选择一个站点。", parent=self)
            return
        profile = self.app.site_profiles[idx]
        for key, var in self.site_vars.items():
            setattr(profile, key, var.get().strip())
        profile.max_pages = max(1, _safe_int(self.max_pages_var.get(), 1))
        row = self.sites_tree.get_children()[idx]
        self.sites_tree.item(row, values=("☑" if profile.enabled else "☐", profile.name, profile.url_template))
        messagebox.showinfo("已更新", f"站点「{profile.name}」已更新。", parent=self)

    def _delete_site(self) -> None:
        idx = self._selected_site_index()
        if idx < 0:
            return
        profile = self.app.site_profiles[idx]
        if not messagebox.askyesno("确认删除", f"删除站点「{profile.name}」？", parent=self):
            return
        del self.app.site_profiles[idx]
        self.sites_tree.delete(self.sites_tree.get_children()[idx])

    # ------------------------------------------------------------------ #
    def _save(self) -> None:
        s = self.app.settings
        s.openalex_mailto = self.mailto_var.get().strip()
        s.crossref_mailto = s.openalex_mailto
        s.concurrent_downloads = max(1, min(8, _safe_int(str(self.workers_var.get()), 4)))
        s.request_timeout = max(10.0, float(self.timeout_var.get() or 30))
        s.if_data_year = self.if_year_var.get().strip()
        self.app.config_obj.save_settings()
        self.app.config_obj.sites.replace([p.to_dict() for p in self.app.site_profiles])
        self.app.log("设置已保存。")
        self._close()

    def _close(self) -> None:
        self.grab_release()
        self.destroy()


# ====================================================================== #
# 辅助函数
# ====================================================================== #
def _safe_float(text: str, default: float = 0.0) -> float:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return default


def _safe_int(text: str, default: int = 0) -> int:
    try:
        return int(float(str(text).strip()))
    except (TypeError, ValueError):
        return default


def _open_url(url: str) -> None:
    open_path(url)


def _write_results(path: Path, papers: list[Paper], fmt: str) -> None:
    """写出检索结果（CSV 带 BOM，xlsx 用 openpyxl）。"""
    rows = [p.to_row() for p in papers]
    if fmt == "xlsx":
        try:
            from openpyxl import Workbook
            from openpyxl.utils import get_column_letter
        except ImportError:
            raise RuntimeError("导出 Excel 需要 openpyxl，请改用 CSV 导出")
        wb = Workbook()
        ws = wb.active
        ws.title = "检索结果"
        headers = list(rows[0].keys()) if rows else []
        ws.append(headers)
        for row in rows:
            ws.append([row.get(h) for h in headers])
        widths = {"标题": 60, "作者": 24, "期刊": 32, "PDF链接": 42, "DOI": 26, "链接": 40, "摘要": 80}
        for idx, header in enumerate(headers, start=1):
            ws.column_dimensions[get_column_letter(idx)].width = widths.get(header, 14)
        ws.freeze_panes = "A2"
        wb.save(path)
        return

    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)


HELP_TEXT = """【LitCrawler 使用说明】

一、基本流程
 1. 在「关键词」框输入检索词，例如：deep learning medical image segmentation
 2. 在「网址 / 数据源」中勾选要检索的网站（可多选，推荐 OpenAlex + Europe PMC）
 3. （可选）点击「选择 / 保存期刊…」限定期刊范围，可保存常用期刊、支持多选
 4. （可选）拖动「最低影响因子」滑块或直接输入精确数值
 5. 点击「检索」（或 Ctrl+Enter）
 6. 在结果表中点击第一列 ☐ 打勾选取需要的文献（支持全选 / 反选 / 只勾可下载项）
 7. 在左侧「下载」区选择保存目录，点击「下载勾选文献」

二、模糊搜索
 · 关键词：即使拼写略有差错（neural netwrok）、词序不同或只输入部分关键词，也能命中并排序；
   取消勾选「关键词模糊匹配」可切换到严格模式（更少但更精确的结果）。
 · 期刊名：支持中英文名称、常见缩写（如 "J Am Chem Soc" ↔ "Journal of the American
   Chemical Society"）、ISSN 精确匹配；勾选「期刊名模糊匹配」后可容忍拼写差异。

三、影响因子
 · 程序内置一份 2022–2025 年 JCR 影响因子精简库（含分区与学科），离线可用；
 · 你也可以导入 JCR 官方导出表 / LetPub 导出表 / 自建 CSV（列名包含 JIF、影响因子、
   Impact Factor、ISSN、Journal 等即可自动识别）：菜单「工具 → 导入影响因子数据」；
 · 未收录的期刊会用 OpenAlex 的两年篇均被引估算，并在详情中标注来源；
 · 影响因子筛选同样可以在检索完成后通过调整数值再筛（会重新计算）。

四、下载
 · 只下载开放获取（OA）文献的 PDF 直链；程序不会绕过付费墙；
 · 已存在的同名文件会自动跳过；文件名格式可选「年份_作者_标题 / 仅标题 / 年份_作者_DOI」；
 · 失败的文献会在弹窗中列出具体原因（订阅限制、站点拒绝、非 PDF 响应等），
   可以复制其链接后手动获取。

五、搜索记录
 · 每次检索都会记入左侧「搜索记录」列表，双击即可还原全部条件并重新检索。

六、自定义站点
 · 「设置 → 自定义站点」可添加有检索结果页的期刊网站：填写含 {query} 的地址模板，
   再给出结果区块 / 标题 / 作者 / PDF 等正则表达式即可接入。
 · 仅支持服务端渲染的页面；需要登录或依赖 JavaScript 的站点无法直接抓取。

七、常见问题
 · 结果太少？放宽影响因子、清空期刊限制、增加结果上限、多勾几个数据源。
 · 期刊没有影响因子？该刊可能未被 JCR 收录，或需要导入你自己的数据。
 · 下载失败？多数订阅文献没有合法免费全文，请通过机构权限获取。
"""
