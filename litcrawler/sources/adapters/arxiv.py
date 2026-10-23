# -*- coding: utf-8 -*-
"""arXiv 数据源：预印本，全部提供 PDF 直链。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ...core import http
from ...core.models import Paper
from ..base import Source, SourceMeta

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class ArxivSource(Source):
    meta = SourceMeta(
        name="arXiv",
        label="arXiv",
        homepage="https://arxiv.org",
        description="物理/数学/计算机预印本，100% 开放获取，可直接下载 PDF",
    )
    min_interval = 3.0  # arXiv 要求请求间隔 ≥3 秒
    page_size = 50

    def search(self, query: str, limit: int) -> list[Paper]:
        papers: list[Paper] = []
        start = 0
        # arXiv 语法：all:"phrase" 更精确；用户输入较长时按短语检索
        term = query.strip()
        search_query = f'all:"{term}"' if " " in term else f"all:{term}"
        while len(papers) < limit:
            self._throttle()
            params = {
                "search_query": search_query,
                "start": start,
                "max_results": min(self.page_size, limit - len(papers)),
                "sortBy": "relevance",
                "sortOrder": "descending",
            }
            resp = http.request(API, params=params, timeout=40, retries=2)
            batch = self._parse_feed(resp.text())
            papers.extend(batch)
            start += params["max_results"]
            if not batch or len(papers) >= limit or start >= 300:
                break
        return papers[:limit]

    # ------------------------------------------------------------------ #
    def _parse_feed(self, xml: str) -> list[Paper]:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []
        out: list[Paper] = []
        for entry in root.findall("a:entry", NS):
            title = re.sub(r"\s+", " ", (entry.findtext("a:title", "", NS) or "")).strip()
            if not title:
                continue
            pdf = ""
            landing = ""
            for link in entry.findall("a:link", NS):
                rel, ctype, href = link.get("rel"), link.get("type"), link.get("href")
                if ctype == "application/pdf" and not pdf:
                    pdf = href or ""
                if rel == "alternate" and not landing:
                    landing = href or ""
            published = entry.findtext("a:published", "", NS) or ""
            year = int(published[:4]) if published[:4].isdigit() else None
            authors = [
                (a.findtext("a:name", "", NS) or "").strip()
                for a in entry.findall("a:author", NS)
            ]
            journal_ref = (entry.findtext("arxiv:journal_ref", "", NS) or "").strip()
            doi = self.clean_doi(entry.findtext("arxiv:doi", "", NS) or "")
            summary = re.sub(r"\s+", " ", entry.findtext("a:summary", "", NS) or "").strip()
            ident = (entry.findtext("a:id", "", NS) or "").rsplit("/", 1)[-1]
            out.append(
                Paper(
                    title=title,
                    authors=[a for a in authors if a],
                    journal=journal_ref or "arXiv 预印本",
                    year=year,
                    doi=doi,
                    abstract=summary[:4000],
                    url=landing or (f"https://arxiv.org/abs/{ident}" if ident else ""),
                    pdf_urls=[pdf] if pdf else [],
                    oa_status="green",
                    is_oa=True,
                    cited_by=0,
                    pub_type="preprint",
                    source=self.name,
                    source_id=ident,
                )
            )
        return out
