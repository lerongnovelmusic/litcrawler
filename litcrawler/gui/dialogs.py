# -*- coding: utf-8 -*-
"""期刊选择对话框：支持多选、保存常用期刊、输入新期刊。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ..core.fuzzy import journal_similarity
from .widgets import PALETTE, Tooltip, pick_font


class JournalPickerDialog(tk.Toplevel):
    """模态对话框：勾选本次检索要限定的期刊。"""

    def __init__(self, master, store, selected: list[str] | None = None):
        super().__init__(master)
        self.title("选择期刊")
        self.store = store
        self.result: list[str] | None = None
        self.geometry("640x560")
        self.minsize(560, 460)
        self.transient(master)
        self.grab_set()

        selected = selected or []
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="本次检索限定的期刊（不选=不限期刊）",
                  style="Section.TLabel").pack(anchor="w")
        ttk.Label(outer, text="支持中文/英文期刊名、缩写、ISSN；勾选“模糊匹配”后可容错拼写。",
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 8))

        # 搜索框
        search_row = ttk.Frame(outer)
        search_row.pack(fill="x", pady=(0, 6))
        ttk.Label(search_row, text="过滤：").pack(side="left")
        self.filter_var = tk.StringVar()
        entry = ttk.Entry(search_row, textvariable=self.filter_var)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        entry.bind("<KeyRelease>", lambda _e: self._refresh())
        Tooltip(entry, "输入关键词筛选下方期刊列表，也可直接点“添加”把输入内容保存为新期刊")

        # 列表
        list_frame = ttk.Frame(outer)
        list_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(list_frame, columns=("sel", "name"), show="headings",
                                 selectmode="extended")
        self.tree.heading("sel", text="选")
        self.tree.heading("name", text="期刊名称")
        self.tree.column("sel", width=44, anchor="center", stretch=False)
        self.tree.column("name", width=460, anchor="w")
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)

        # 按钮区
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="添加并保存", command=self._add_current).pack(side="left")
        ttk.Button(row, text="删除所选", command=self._delete_selected).pack(side="left", padx=4)
        ttk.Button(row, text="反选", command=self._invert).pack(side="left")
        ttk.Button(row, text="全选", command=self._select_all).pack(side="left", padx=4)
        ttk.Button(row, text="清空选择", command=self._clear_selection).pack(side="left")

        row2 = ttk.Frame(outer)
        row2.pack(fill="x", pady=(8, 0))
        self.fuzzy_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="期刊名模糊匹配（推荐）", variable=self.fuzzy_var).pack(side="left")
        ttk.Button(row2, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(row2, text="确定", style="Accent.TButton",
                   command=self._confirm).pack(side="right", padx=6)

        self._checked: set[str] = set(selected)
        self._all_names: list[str] = []
        self._refresh()
        entry.focus_set()
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._confirm())

    # ------------------------------------------------------------------ #
    def _refresh(self) -> None:
        keyword = self.filter_var.get().strip()
        names = list(self.store.names)
        # 把已选但未保存的期刊也列出来
        for name in sorted(self._checked):
            if name not in names:
                names.append(name)
        if keyword:
            scored = []
            for name in names:
                score = journal_similarity(keyword, name)
                if keyword.lower() in name.lower():
                    score = max(score, 1.0)
                if score >= 0.5:
                    scored.append((score, name))
            scored.sort(key=lambda x: (-x[0], x[1]))
            names = [n for _s, n in scored]
        self._all_names = names

        self.tree.delete(*self.tree.get_children())
        for idx, name in enumerate(names):
            mark = "☑" if name in self._checked else "☐"
            self.tree.insert("", "end", iid=str(idx), values=(mark, name),
                             tags=("odd" if idx % 2 else "even",))
        self.tree.tag_configure("odd", background="#f8fafc")
        self.tree.tag_configure("even", background=PALETTE["panel"])

    def _name_at(self, iid: str) -> str:
        try:
            return self._all_names[int(iid)]
        except (ValueError, IndexError):
            return ""

    def _toggle(self, iid: str) -> None:
        name = self._name_at(iid)
        if not name:
            return
        if name in self._checked:
            self._checked.discard(name)
        else:
            self._checked.add(name)
        mark = "☑" if name in self._checked else "☐"
        self.tree.set(iid, "sel", mark)

    def _on_click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        if self.tree.identify_column(event.x) == "#1":
            self._toggle(iid)

    def _on_space(self, _event=None) -> None:
        for iid in self.tree.selection():
            self._toggle(iid)

    def _select_all(self) -> None:
        for iid in self.tree.get_children():
            name = self._name_at(iid)
            if name:
                self._checked.add(name)
        self._refresh()

    def _clear_selection(self) -> None:
        self._checked.clear()
        self._refresh()

    def _invert(self) -> None:
        names = set(self._all_names)
        self._checked = {n for n in names if n not in self._checked} | (self._checked - names)
        self._refresh()

    def _add_current(self) -> None:
        text = self.filter_var.get().strip()
        if not text:
            messagebox.showinfo("提示", "请先在输入框中填写期刊名称", parent=self)
            return
        added = self.store.add([text])
        self._checked.add(text)
        self.filter_var.set("")
        self._refresh()
        if added:
            messagebox.showinfo("已保存", f"已保存期刊：{text}", parent=self)

    def _delete_selected(self) -> None:
        names = [self._name_at(iid) for iid in self.tree.selection()]
        names = [n for n in names if n]
        if not names:
            messagebox.showinfo("提示", "请先在列表中选择要删除的期刊", parent=self)
            return
        if not messagebox.askyesno("确认删除", "从常用期刊中删除：\n" + "\n".join(names), parent=self):
            return
        for name in names:
            self.store.remove(name)
            self._checked.discard(name)
        self._refresh()

    def _confirm(self) -> None:
        self.result = [n for n in self.store.names if n in self._checked]
        for name in sorted(self._checked):
            if name not in self.result:
                self.result.append(name)
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    # ------------------------------------------------------------------ #
    @classmethod
    def ask(cls, master, store, selected: list[str] | None = None) -> list[str] | None:
        dlg = cls(master, store, selected)
        master.wait_window(dlg)
        return dlg.result
