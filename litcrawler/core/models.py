# -*- coding: utf-8 -*-
"""数据模型：文献记录与检索结果。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


@dataclass
class Paper:
    """统一的文献记录（各数据源适配后的结果）。"""

    title: str = ""
    authors: list[str] = field(default_factory=list)
    journal: str = ""
    issns: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str = ""
    abstract: str = ""
    url: str = ""
    pdf_urls: list[str] = field(default_factory=list)
    oa_status: str = ""          # gold / green / hybrid / bronze / closed / unknown
    is_oa: bool = False
    cited_by: int = 0
    pub_type: str = ""
    source: str = ""             # 命中该记录的数据源
    source_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    # 过滤/排序阶段填充
    impact_factor: float | None = None
    impact_factor_source: str = ""
    impact_factor_quartile: str = ""
    impact_factor_year: str = ""
    matched_journal: str = ""
    relevance: float = 0.0
    match_score: float = 0.0

    # ------------------------------------------------------------------ #
    @property
    def key(self) -> str:
        """去重键：优先 DOI，其次标题+年份指纹。"""
        doi = self.doi.lower()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if doi.startswith(prefix):
                doi = doi[len(prefix) :]
        if doi:
            return "doi:" + doi.strip()
        basis = f"{_clean(self.title).lower()}|{self.year or ''}"
        return "t:" + hashlib.md5(basis.encode("utf-8")).hexdigest()

    @property
    def title_key(self) -> str:
        """标题指纹：只保留字母数字与汉字，用于跨源去重。"""
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", self.title.lower())

    @property
    def authors_short(self) -> str:
        if not self.authors:
            return ""
        if len(self.authors) <= 3:
            return ", ".join(self.authors)
        return f"{self.authors[0]} 等 {len(self.authors)} 人"

    @property
    def best_pdf(self) -> str:
        return self.pdf_urls[0] if self.pdf_urls else ""

    @property
    def has_pdf(self) -> bool:
        return bool(self.pdf_urls)

    @property
    def if_text(self) -> str:
        if self.impact_factor is None:
            return "—"
        text = f"{self.impact_factor:g}"
        if self.impact_factor_quartile:
            text += f" ({self.impact_factor_quartile})"
        return text

    def to_row(self) -> dict:
        """导出为表格/CSV 行。"""
        return {
            "标题": self.title,
            "作者": self.authors_short,
            "期刊": self.journal,
            "年份": self.year or "",
            "影响因子": "" if self.impact_factor is None else self.impact_factor,
            "分区": self.impact_factor_quartile,
            "被引": self.cited_by,
            "开放获取": "是" if self.is_oa else "否",
            "可下载PDF": "是" if self.has_pdf else "否",
            "PDF链接": self.best_pdf,
            "DOI": self.doi,
            "来源": self.source,
            "链接": self.url,
            "摘要": _clean(self.abstract),
        }

    @classmethod
    def from_row(cls, row: dict) -> "Paper":
        """从导出的 CSV 行还原（用于离线复用结果）。"""
        def num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        year = row.get("年份") or ""
        try:
            year_val: int | None = int(float(year))
        except (TypeError, ValueError):
            year_val = None
        pdf = _clean(str(row.get("PDF链接") or ""))
        return cls(
            title=_clean(str(row.get("标题") or "")),
            authors=[a.strip() for a in str(row.get("作者") or "").split(",") if a.strip()],
            journal=_clean(str(row.get("期刊") or "")),
            year=year_val,
            doi=_clean(str(row.get("DOI") or "")),
            url=_clean(str(row.get("链接") or "")),
            pdf_urls=[pdf] if pdf else [],
            is_oa=str(row.get("开放获取") or "").strip() == "是",
            cited_by=int(num(row.get("被引")) or 0),
            source=_clean(str(row.get("来源") or "")),
            abstract=_clean(str(row.get("摘要") or "")),
            impact_factor=num(row.get("影响因子")),
            impact_factor_quartile=_clean(str(row.get("分区") or "")),
        )


@dataclass
class SourceReport:
    """单个数据源的检索报告。"""

    name: str
    found: int = 0
    kept: int = 0
    error: str = ""
    elapsed: float = 0.0

    @property
    def status(self) -> str:
        if self.error:
            return f"失败：{self.error[:60]}"
        return f"检索 {self.found} 条，保留 {self.kept} 条"


@dataclass
class SearchResult:
    """一次检索的完整结果。"""

    papers: list[Paper] = field(default_factory=list)
    reports: list[SourceReport] = field(default_factory=list)
    keyword: str = ""
    elapsed: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def total_raw(self) -> int:
        return sum(r.found for r in self.reports)

    @property
    def error_text(self) -> str:
        errs = [f"{r.name}: {r.error}" for r in self.reports if r.error]
        return "\n".join(errs)
