# -*- coding: utf-8 -*-
"""DOAJ 数据源：开放获取期刊目录，全部为免费全文。"""

from __future__ import annotations

import urllib.parse

from ...core import http
from ...core.models import Paper
from ..base import Source, SourceMeta

API = "https://doaj.org/api/search/articles/"


class DoajSource(Source):
    meta = SourceMeta(
        name="DOAJ",
        label="DOAJ",
        homepage="https://doaj.org",
        description="开放获取期刊目录（1.9 万种期刊），全部免费全文",
    )
    min_interval = 0.3

    def search(self, query: str, limit: int) -> list[Paper]:
        papers: list[Paper] = []
        page = 1
        page_size = min(100, max(1, limit))
        while len(papers) < limit:
            self._throttle()
            url = API + urllib.parse.quote(query, safe="")
            params = {"pageSize": min(page_size, limit - len(papers)), "page": page}
            data = http.get_json(url, params=params, timeout=40)
            results = data.get("results") or []
            if not results:
                break
            for item in results:
                paper = self._parse(item)
                if paper and paper.title:
                    papers.append(paper)
            if len(results) < page_size:
                break
            page += 1
        return papers[:limit]

    # ------------------------------------------------------------------ #
    def _parse(self, item: dict) -> Paper | None:
        bib = item.get("bibjson") or {}
        title = (bib.get("title") or "").strip()
        if not title:
            return None
        journal_obj = bib.get("journal") or {}
        journal = (journal_obj.get("title") or "").strip()
        issns = [str(i) for i in (journal_obj.get("issns") or []) if i]

        doi = ""
        for ident in bib.get("identifier") or []:
            if (ident.get("type") or "").lower() == "doi":
                doi = self.clean_doi(ident.get("id") or "")
                break

        year = None
        if str(bib.get("year") or "").isdigit():
            year = int(bib["year"])

        authors = [a.get("name", "").strip() for a in (bib.get("author") or []) if a.get("name")]
        links = [l.get("url") for l in (bib.get("link") or []) if l.get("url")]
        pdfs = [u for u in links if u.lower().endswith(".pdf") or "pdf" in u.lower()]
        landing = links[0] if links else (f"https://doi.org/{doi}" if doi else "")

        return Paper(
            title=title,
            authors=authors,
            journal=journal,
            issns=issns,
            year=year,
            doi=doi,
            abstract=(bib.get("abstract") or "").strip()[:4000],
            url=landing,
            pdf_urls=pdfs,
            oa_status="gold",
            is_oa=True,
            cited_by=0,
            pub_type="journal article",
            source=self.name,
            source_id=(item.get("id") or ""),
        )
