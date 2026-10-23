# -*- coding: utf-8 -*-
"""PDF 下载引擎：并发下载、文件名净化、断点跳过、失败原因上报。"""

from __future__ import annotations

import os
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import http
from .models import Paper

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SPACES = re.compile(r"\s+")
_DOI_TAIL = re.compile(r"10\.\d{4,9}/\S+")

MAX_FILENAME = 150
CHUNK = 64 * 1024


@dataclass
class DownloadTask:
    """单个下载任务。"""

    paper: Paper
    status: str = "pending"   # pending/ok/skipped/failed/no_pdf
    path: Path | None = None
    message: str = ""
    bytes: int = 0
    elapsed: float = 0.0
    url: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "skipped"}


@dataclass
class DownloadSummary:
    """一次批量下载的汇总。"""

    tasks: list[DownloadTask] = field(default_factory=list)
    directory: Path | None = None
    elapsed: float = 0.0

    @property
    def ok_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == "ok")

    @property
    def skipped(self) -> int:
        return sum(1 for t in self.tasks if t.status == "skipped")

    @property
    def failed(self) -> list[DownloadTask]:
        return [t for t in self.tasks if t.status in {"failed", "no_pdf"}]

    @property
    def total_bytes(self) -> int:
        return sum(t.bytes for t in self.tasks)


def sanitize_filename(name: str, max_length: int = MAX_FILENAME) -> str:
    """把标题转成 Windows/Linux 都可用的文件名。"""
    name = _ILLEGAL.sub(" ", name or "")
    name = _SPACES.sub(" ", name).strip(" .")
    name = name.replace("&amp;", "&")
    if len(name) > max_length:
        name = name[:max_length].rstrip(" .")
    return name or "untitled"


def paper_filename(paper: Paper, *, index: int | None = None, style: str = "year_author_title") -> str:
    """生成 PDF 文件名（不含扩展名，自动去重后缀由调用方添加）。"""
    year = str(paper.year) if paper.year else ""
    first_author = ""
    if paper.authors:
        first_author = re.split(r"[,\s]", paper.authors[0].strip())[0]
    elif paper.journal:
        first_author = paper.journal

    title = _SPACES.sub(" ", paper.title or "").strip()
    if style == "title":
        base = title
    elif style == "doi":
        tail = ""
        if paper.doi:
            m = _DOI_TAIL.search(paper.doi)
            tail = m.group(0).replace("/", "_") if m else paper.doi.replace("/", "_")
        base = "_".join(x for x in [year, first_author, tail or title] if x)
    else:
        # 默认：年份_作者_标题（下划线分隔，跨平台最稳妥）
        base = "_".join(x for x in [year, first_author, title] if x)
    if index is not None:
        base = f"{index:03d}_{base}"
    return sanitize_filename(base)


def looks_like_pdf(prefix: bytes) -> bool:
    return prefix[:5] == b"%PDF-" or prefix[:1024].lstrip().startswith(b"%PDF")


class Downloader:
    """批量下载器。"""

    def __init__(self, directory: Path | str, *, workers: int = 4, timeout: float = 60.0,
                 filename_style: str = "year_author_title", mailto: str = ""):
        self.directory = Path(directory)
        self.workers = max(1, min(8, int(workers or 4)))
        self.timeout = timeout
        self.filename_style = filename_style
        self.mailto = mailto
        self._lock = threading.RLock()
        self._used_names: set[str] = set()
        self._cancel = threading.Event()

    # ------------------------------------------------------------------ #
    def cancel(self) -> None:
        self._cancel.set()

    def reset_cancel(self) -> None:
        self._cancel.clear()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # ------------------------------------------------------------------ #
    def download_all(
        self,
        papers: Iterable[Paper],
        *,
        progress: Callable[[int, int, DownloadTask], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> DownloadSummary:
        """并发下载多篇文献，返回汇总结果。"""
        papers = [p for p in papers]
        self.directory.mkdir(parents=True, exist_ok=True)
        summary = DownloadSummary(directory=self.directory)
        summary.tasks = [DownloadTask(paper=p) for p in papers]
        started = time.time()
        total = len(summary.tasks)
        done = 0

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {}
            for idx, task in enumerate(summary.tasks, start=1):
                futures[pool.submit(self._download_one, task, idx)] = task
            for future in as_completed(futures):
                task = futures[future]
                if future.cancelled():
                    task.status, task.message = "failed", "已取消"
                else:
                    exc = future.exception()
                    if exc is not None:
                        task.status, task.message = "failed", f"{type(exc).__name__}: {exc}"
                done += 1
                if progress:
                    progress(done, total, task)
                if should_stop and should_stop():
                    self.cancel()
        summary.elapsed = time.time() - started
        return summary

    # ------------------------------------------------------------------ #
    def _download_one(self, task: DownloadTask, index: int) -> DownloadTask:
        t0 = time.time()
        paper = task.paper
        if self._cancel.is_set():
            task.status, task.message = "failed", "已取消"
            return task

        planned = self._plan_path(paper, index)
        if planned.exists() and planned.stat().st_size > 1024:
            task.status, task.path, task.message = "skipped", planned, "已存在，跳过"
            task.bytes = planned.stat().st_size
            task.elapsed = time.time() - t0
            return task

        candidates = self.pdf_candidates(paper)
        if not candidates:
            task.status, task.message = "no_pdf", "未找到可用的 PDF 链接（可能是订阅文献）"
            task.elapsed = time.time() - t0
            return task

        errors: list[str] = []
        for url in candidates:
            if self._cancel.is_set():
                task.status, task.message = "failed", "已取消"
                return task
            target = planned
            try:
                size = self._fetch_to(url, target, referer=paper.url)
            except _NotPdf as exc:
                errors.append(f"{_short_url(url)} → 非 PDF（{exc}）")
                continue
            except Exception as exc:
                errors.append(f"{_short_url(url)} → {type(exc).__name__}: {str(exc)[:60]}")
                continue
            task.status, task.path, task.bytes, task.url = "ok", target, size, url
            task.message = f"已下载 {size/1024:.0f} KB"
            task.elapsed = time.time() - t0
            return task
        task.status, task.message = "failed", "；".join(errors[:3]) or "下载失败"
        task.elapsed = time.time() - t0
        return task

    def _plan_path(self, paper: Paper, index: int) -> Path:
        base = paper_filename(paper, index=index, style=self.filename_style)
        with self._lock:
            candidate = base
            n = 1
            while candidate.lower() in self._used_names:
                n += 1
                candidate = f"{base} ({n})"
            self._used_names.add(candidate.lower())
        return self.directory / f"{candidate}.pdf"

    @staticmethod
    def pdf_candidates(paper: Paper) -> list[str]:
        """按成功率排序的候选 PDF 地址。"""
        out: list[str] = []
        for url in paper.pdf_urls:
            if url and url not in out:
                out.append(url)
        if not out and paper.doi:
            # 部分出版商在 DOI 上加 /pdf 可直接拿到全文
            out.append(f"https://doi.org/{paper.doi}")
        return out

    def _fetch_to(self, url: str, target: Path, *, referer: str = "") -> int:
        headers = {"Accept": "application/pdf,*/*"}
        if referer:
            headers["Referer"] = referer
        tmp = target.with_suffix(".pdf.part")
        req_url = url
        written = 0
        resp = http.request(
            req_url,
            timeout=self.timeout,
            retries=2,
            browser_ua=True,
            headers=headers,
        )
        ctype = resp.content_type
        if "pdf" not in ctype and not looks_like_pdf(resp.content[:2048]):
            raise _NotPdf(ctype or "unknown content-type")
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as fh:
            fh.write(resp.content)
            written = len(resp.content)
        if not looks_like_pdf(_read_head(tmp)):
            try:
                tmp.unlink()
            except OSError:
                pass
            raise _NotPdf("内容不是 PDF")
        os.replace(tmp, target)
        return written


class _NotPdf(Exception):
    """响应不是 PDF。"""


def _read_head(path: Path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(1024)


def _short_url(url: str, limit: int = 48) -> str:
    host = urllib.parse.urlparse(url).netloc or url
    return host[:limit]


def default_directory() -> Path:
    return Path.home() / "Documents" / "LitCrawler下载"
