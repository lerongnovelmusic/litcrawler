# -*- coding: utf-8 -*-
"""配置与本地存储：设置、搜索记录、期刊清单、下载目录。

数据默认保存在用户目录（%%APPDATA%%\\LitCrawler），也支持便携模式：
应用目录下存在 ``portable.txt`` 时改在应用目录旁的 ``data`` 子目录保存。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .. import __app_name__

_lock = threading.RLock()


def app_root() -> Path:
    """应用安装/源码目录。"""
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """可写数据目录。"""
    portable = app_root() / "portable.txt"
    if portable.exists():
        base = app_root() / "data"
    else:
        base = Path(os.environ.get("APPDATA") or Path.home()) / __app_name__
    base.mkdir(parents=True, exist_ok=True)
    return base


def logs_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def downloads_default_dir() -> Path:
    d = Path.home() / "Documents" / "LitCrawler下载"
    return d


def resource_path(*parts: str) -> Path:
    """打包资源路径（PyInstaller onefile 下位于 _MEIPASS）。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", app_root()))
    else:
        base = app_root()
    return base.joinpath(*parts)


@dataclass
class Settings:
    """用户设置。"""

    sources: list[str] = field(default_factory=lambda: ["OpenAlex", "Crossref", "Europe PMC", "arXiv", "DOAJ"])
    journal_names: list[str] = field(default_factory=list)
    fuzzy_journal: bool = True
    fuzzy_keyword: bool = True
    min_impact_factor: float = 0.0
    max_impact_factor: float = 0.0  # 0 表示不限
    year_from: int = 0
    year_to: int = 0
    result_limit: int = 50
    only_open_access: bool = False
    oa_only_for_filter: bool = False
    download_dir: str = ""
    openalex_mailto: str = ""
    crossref_mailto: str = ""
    concurrent_downloads: int = 4
    request_timeout: float = 30.0
    if_data_year: str = ""  # 影响因子数据年份
    use_bundled_impact_factor: bool = True
    theme: str = "clam"
    last_keyword: str = ""

    @classmethod
    def load(cls, path: Path) -> "Settings":
        if not path.exists():
            obj = cls()
            obj.download_dir = str(downloads_default_dir())
            return obj
        try:
            raw = json.loads(path.read_text("utf-8"))
        except Exception:
            obj = cls()
            obj.download_dir = str(downloads_default_dir())
            return obj
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in raw.items() if k in known}
        obj = cls(**clean)
        if not obj.download_dir:
            obj.download_dir = str(downloads_default_dir())
        return obj

    def save(self, path: Path) -> None:
        with _lock:
            _atomic_write_json(path, asdict(self))


def _atomic_write_json(path: Path, payload: Any) -> None:
    """原子写入 JSON，避免中途崩溃损坏数据。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass
class HistoryEntry:
    """一次检索的记录。"""

    timestamp: float = field(default_factory=time.time)
    keyword: str = ""
    sources: list[str] = field(default_factory=list)
    journals: list[str] = field(default_factory=list)
    min_if: float = 0.0
    extra: str = ""
    result_count: int = 0
    downloaded: int = 0

    @property
    def when(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))

    def summary(self) -> str:
        bits = [f"[{self.when}] {self.keyword or '(空关键词)'}"]
        if self.journals:
            bits.append("期刊:" + "/".join(self.journals[:2]) + ("…" if len(self.journals) > 2 else ""))
        if self.min_if:
            bits.append(f"IF≥{self.min_if:g}")
        bits.append(f"{self.result_count}条")
        return " | ".join(bits)


class HistoryStore:
    """搜索记录存储（最多保留 N 条）。"""

    def __init__(self, path: Path, limit: int = 300):
        self.path = path
        self.limit = limit
        self.entries: list[HistoryEntry] = []
        self.load()

    def load(self) -> None:
        self.entries = []
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except Exception:
            return
        for item in raw if isinstance(raw, list) else []:
            known = {f for f in HistoryEntry.__dataclass_fields__}
            try:
                self.entries.append(HistoryEntry(**{k: v for k, v in item.items() if k in known}))
            except Exception:
                continue
        self.entries.sort(key=lambda e: e.timestamp, reverse=True)

    def save(self) -> None:
        with _lock:
            payload = [asdict(e) for e in self.entries[: self.limit]]
            _atomic_write_json(self.path, payload)

    def add(self, entry: HistoryEntry) -> HistoryEntry:
        """同关键词+同筛选的连续检索合并为一条，避免刷屏。"""
        if self.entries:
            top = self.entries[0]
            if (
                top.keyword == entry.keyword
                and top.sources == entry.sources
                and top.journals == entry.journals
                and abs(top.min_if - entry.min_if) < 1e-9
            ):
                self.entries[0] = entry
                self.save()
                return entry
        self.entries.insert(0, entry)
        del self.entries[self.limit :]
        self.save()
        return entry

    def remove(self, indexes: list[int]) -> None:
        for idx in sorted(set(indexes), reverse=True):
            if 0 <= idx < len(self.entries):
                del self.entries[idx]
        self.save()

    def clear(self) -> None:
        self.entries.clear()
        self.save()


class JournalListStore:
    """常用期刊清单。"""

    def __init__(self, path: Path):
        self.path = path
        self.names: list[str] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            if isinstance(raw, list):
                self.names = [str(x).strip() for x in raw if str(x).strip()]
        except Exception:
            self.names = []

    def save(self) -> None:
        with _lock:
            _atomic_write_json(self.path, self.names)

    def add(self, names: list[str]) -> int:
        added = 0
        existing = {n.lower() for n in self.names}
        for n in names:
            n = n.strip()
            if n and n.lower() not in existing:
                self.names.append(n)
                existing.add(n.lower())
                added += 1
        if added:
            self.save()
        return added

    def remove(self, name: str) -> None:
        self.names = [n for n in self.names if n != name]
        self.save()


class JsonListStore:
    """通用 JSON 列表存储（用于自定义站点等）。"""

    def __init__(self, path: Path):
        self.path = path
        self.items: list[dict] = []
        self.load()

    def load(self) -> None:
        self.items = []
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except Exception:
            return
        if isinstance(raw, list):
            self.items = [x for x in raw if isinstance(x, dict)]

    def save(self) -> None:
        with _lock:
            _atomic_write_json(self.path, self.items)

    def replace(self, items: list[dict]) -> None:
        self.items = list(items)
        self.save()


class Config:
    """聚合配置对象。``base`` 可以是 Path 或字符串，缺省为数据目录。"""

    def __init__(self, base: Path | str | None = None):
        self.base = Path(base) if base else data_dir()
        self.base.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.base / "settings.json"
        self.history_path = self.base / "history.json"
        self.journals_path = self.base / "journals.json"
        self.sites_path = self.base / "sites.json"
        self.if_cache_path = self.base / "impact_factor_cache.json"
        self.if_import_dir = self.base / "impact_factor"
        self.settings = Settings.load(self.settings_path)
        self.history = HistoryStore(self.history_path)
        self.journals = JournalListStore(self.journals_path)
        self.sites = JsonListStore(self.sites_path)

    def save_settings(self) -> None:
        self.settings.save(self.settings_path)
