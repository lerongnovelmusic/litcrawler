# -*- coding: utf-8 -*-
"""Crossref 数据源：DOI 权威元数据，覆盖期刊/会议/学位论文。"""

from __future__ import annotations

import re

from ...core import http
from ...core.models import Paper
from ..base import Source, SourceMeta

API = "https://api.crossref.org/works"
_TAG_RE = re.compile(r"<[^>]+>")


class CrossrefSource(Source):
    meta = SourceMeta(
        name="Crossref",
        label="Crossref",
        homepage="https://www.crossref.org",
        description="全球 DOI 注册机构，元数据权威；部分出版商提供全文链接",
    )
    min_interval = 0.3

    def search(self, query: str, limit: int) -> list[Paper]:
        papers: list[Paper] = []
        offset = 0
        rows = min(100, max(1, limit))
        while len(papers) < limit:
            self._throttle()
            params = {
                "query.bibliographic": query,
                "rows": min(rows, limit - len(papers)),
                "offset": offset,
                "select": "DOI,title,container-title,short-container-title,ISSN,issued,published,"
                          "published-print,type,author,abstract,link,URL,is-referenced-by-count,"
                          "license,subject,publisher,volume,issue,page",
            }
            if self.mailto:
                params["mailto"] = self.mailto
            data = http.get_json(API, params=params, timeout=40)
            message = data.get("message") or {}
            items = message.get("items") or []
            if not items:
                break
            for item in items:
                paper = self._parse(item)
                if paper and paper.title:
                    papers.append(paper)
            offset += len(items)
            if len(items) < rows or offset > 1000:
                break
        return papers[:limit]

    # ------------------------------------------------------------------ #
    def _parse(self, item: dict) -> Paper | None:
        titles = item.get("title") or []
        title = _TAG_RE.sub(" ", titles[0]).strip() if titles else ""
        if not title:
            return None
        containers = item.get("container-title") or []
        journal = containers[0].strip() if containers else ""

        year = None
        for field in ("issued", "published", "published-print", "published-online", "created"):
            parts = ((item.get(field) or {}).get("date-parts") or [[None]])[0]
            if parts and parts[0]:
                year = int(parts[0])
                break

        authors = []
        for a in item.get("author") or []:
            name = " ".join(x for x in [a.get("given"), a.get("family")] if x) or a.get("name") or ""
            if name:
                authors.append(name.strip())

        pdfs: list[str] = []
        for link in item.get("link") or []:
            url = link.get("URL")
            ctype = (link.get("content-type") or "").lower()
            if url and ("pdf" in ctype or url.lower().endswith(".pdf")):
                if url not in pdfs:
                    pdfs.append(url)

        abstract = _TAG_RE.sub(" ", item.get("abstract") or "")
        abstract = re.sub(r"\s+", " ", abstract).replace("Abstract", "", 1).strip()

        licenses = item.get("license") or []
        is_oa = bool(pdfs) or any("creativecommons" in (lic.get("URL") or "").lower() for lic in licenses)

        return Paper(
            title=title,
            authors=authors,
            journal=journal,
            issns=[i for i in (item.get("ISSN") or []) if i],
            year=year,
            doi=self.clean_doi(item.get("DOI") or ""),
            abstract=abstract[:4000],
            url=item.get("URL") or (f"https://doi.org/{item.get('DOI')}" if item.get("DOI") else ""),
            pdf_urls=pdfs,
            oa_status="oa" if is_oa else "closed",
            is_oa=is_oa,
            cited_by=int(item.get("is-referenced-by-count") or 0),
            pub_type=(item.get("type") or "").replace("-", " "),
            source=self.name,
            extra={"publisher": item.get("publisher") or ""},
        )
