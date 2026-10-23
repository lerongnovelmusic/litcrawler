# -*- coding: utf-8 -*-
"""Europe PMC 数据源：生命医学领域，开放获取全文 PDF 可直接下载。"""

from __future__ import annotations

from ...core import http
from ...core.models import Paper
from ..base import Source, SourceMeta

API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


class EuropePmcSource(Source):
    meta = SourceMeta(
        name="Europe PMC",
        label="Europe PMC",
        homepage="https://europepmc.org",
        description="生命科学与医学文献库，开放获取全文 PDF 可直接下载",
    )
    min_interval = 0.25

    def search(self, query: str, limit: int) -> list[Paper]:
        papers: list[Paper] = []
        cursor = "*"
        page_size = min(100, max(1, limit))
        while len(papers) < limit:
            self._throttle()
            params = {
                "query": query,
                "format": "json",
                "pageSize": min(page_size, limit - len(papers)),
                "resultType": "core",
                "cursorMark": cursor,
            }
            data = http.get_json(API, params=params, timeout=40)
            results = ((data.get("resultList") or {}).get("result")) or []
            if not results:
                break
            for item in results:
                paper = self._parse(item)
                if paper and paper.title:
                    papers.append(paper)
            next_cursor = data.get("nextCursorMark")
            if not next_cursor or next_cursor == cursor or len(results) < page_size:
                break
            cursor = next_cursor
        return papers[:limit]

    # ------------------------------------------------------------------ #
    def _parse(self, item: dict) -> Paper | None:
        title = (item.get("title") or "").strip().rstrip(".")
        if not title:
            return None

        journal_info = item.get("journalInfo") or {}
        journal = ((journal_info.get("journal") or {}).get("title") or "").strip()
        # Europe PMC 的仓储记录会用 "PubMed Central" 等占位名，这类不是期刊
        if journal.lower() in {"pubmed central", "pmc", "europe pmc", "arxiv", "bioRxiv", "medRxiv".lower()}:
            journal = f"{journal}（仓储）"
        issns = []
        for key in ("issn", "essn", "nlmId"):
            val = (journal_info.get("journal") or {}).get(key)
            if val:
                issns.append(str(val))

        year = None
        for key in ("pubYear", "yearOfPublication", "firstPublicationDate"):
            val = item.get(key) or journal_info.get(key)
            if val and str(val)[:4].isdigit():
                year = int(str(val)[:4])
                break

        authors = []
        author_list = ((item.get("authorList") or {}).get("author")) or []
        for a in author_list[:30]:
            name = a.get("fullName") or a.get("collectiveName") or ""
            if not name:
                name = " ".join(x for x in [a.get("firstName"), a.get("lastName")] if x)
            if name:
                authors.append(name.strip())

        pdfs: list[str] = []
        landing = ""
        for entry in ((item.get("fullTextUrlList") or {}).get("fullTextUrl")) or []:
            url = entry.get("url") or ""
            style = (entry.get("documentStyle") or "").lower()
            if style == "pdf" and url and url not in pdfs:
                pdfs.append(url)
            if style == "html" and not landing:
                landing = url
        pmcid = item.get("pmcid")
        if pmcid and item.get("hasPDF") == "Y":
            canonical = f"https://europepmc.org/articles/{pmcid}?pdf=render"
            if canonical not in pdfs:
                pdfs.insert(0, canonical)
        if not landing:
            landing = f"https://europepmc.org/article/{item.get('source', 'MED')}/{item.get('id', '')}"

        doi = self.clean_doi(item.get("doi") or "")
        abstract = (item.get("abstractText") or "").strip()
        is_oa = (item.get("isOpenAccess") or "").upper() == "Y" or bool(pdfs)

        return Paper(
            title=title,
            authors=authors,
            journal=journal,
            issns=issns,
            year=year,
            doi=doi,
            abstract=abstract[:4000],
            url=landing,
            pdf_urls=pdfs,
            oa_status="oa" if is_oa else "closed",
            is_oa=is_oa,
            cited_by=int(item.get("citedByCount") or 0),
            pub_type=(item.get("pubType") or ""),
            source=self.name,
            source_id=str(item.get("id") or ""),
            extra={"pmid": item.get("pmid") or "", "pmcid": pmcid or ""},
        )
