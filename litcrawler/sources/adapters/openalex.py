# -*- coding: utf-8 -*-
"""OpenAlex 数据源（主力源）：摘要、开放获取 PDF 链接、期刊信息一应俱全。"""

from __future__ import annotations

from ...core import http
from ...core.models import Paper
from ..base import Source, SourceMeta

API = "https://api.openalex.org/works"

SELECT_FIELDS = ",".join(
    [
        "id", "doi", "title", "display_name", "publication_year", "publication_date", "type",
        "cited_by_count", "is_retracted", "language", "open_access", "best_oa_location",
        "primary_location", "locations", "authorships", "abstract_inverted_index", "ids",
        "biblio", "type_crossref", "is_paratext",
    ]
)


class OpenAlexSource(Source):
    meta = SourceMeta(
        name="OpenAlex",
        label="OpenAlex",
        homepage="https://openalex.org",
        description="开放学术图谱，覆盖 2.5 亿+ 文献，含摘要与开放获取 PDF 链接",
    )
    min_interval = 0.15

    def _params(self, query: str, limit: int, page: int) -> dict:
        params = {
            "search": query,
            "per-page": min(200, max(1, limit)),
            "page": page,
            "select": SELECT_FIELDS,
            "sort": "relevance_score:desc",
        }
        if self.mailto:
            params["mailto"] = self.mailto
        return params

    def search(self, query: str, limit: int) -> list[Paper]:
        papers: list[Paper] = []
        page = 1
        per_page = min(200, max(1, limit))
        while len(papers) < limit:
            self._throttle()
            data = http.get_json(API, params=self._params(query, min(per_page, limit - len(papers)), page), timeout=40)
            results = data.get("results") or []
            if not results:
                break
            for item in results:
                paper = self._parse(item)
                if paper and paper.title:
                    papers.append(paper)
            if len(results) < per_page:
                break
            page += 1
            if page > 6:  # 安全上限
                break
        return papers[:limit]

    # ------------------------------------------------------------------ #
    def _parse(self, item: dict) -> Paper | None:
        if item.get("is_paratext") or item.get("is_retracted"):
            return None
        title = (item.get("title") or item.get("display_name") or "").strip()
        if not title:
            return None

        locations: list[dict] = []
        best = item.get("best_oa_location")
        primary = item.get("primary_location") or {}
        if best:
            locations.append(best)
        if primary and primary not in locations:
            locations.append(primary)
        for loc in item.get("locations") or []:
            if loc and loc not in locations:
                locations.append(loc)

        pdfs: list[str] = []
        for loc in locations:
            url = (loc or {}).get("pdf_url")
            if url and url not in pdfs:
                pdfs.append(url)
        landing = ""
        for loc in locations:
            if (loc or {}).get("landing_page_url"):
                landing = loc["landing_page_url"]
                break
        if not landing and item.get("doi"):
            landing = item["doi"]

        journal = ""
        journal_type = ""
        issns: list[str] = []
        for loc in locations:
            src = (loc or {}).get("source") or {}
            src_type = (src.get("type") or "").lower()
            name = src.get("display_name")
            # 优先取正式出版物（期刊/会议/丛书），仓储（arXiv、PubMed Central 等）仅作兜底
            if name and src_type in {"journal", "conference", "book series", "ebook platform"} and not journal:
                journal, journal_type = name, src_type
            elif name and not journal and src_type not in {"repository"}:
                journal, journal_type = name, src_type
            if name and src_type == "repository" and not journal:
                journal = name
            for issn in (src.get("issn") or []):
                if issn and issn not in issns:
                    issns.append(issn)
            if src.get("issn_l") and src["issn_l"] not in issns:
                issns.append(src["issn_l"])

        # 仓储来源（如 arXiv 预印本）单独标注，避免被当成期刊名用于影响因子匹配
        is_preprint = journal_type == "repository" or (item.get("type") or "").lower() == "preprint"
        if is_preprint and journal:
            journal = f"{journal}（预印本/仓储）"

        authors = []
        for authorship in (item.get("authorships") or [])[:30]:
            name = ((authorship or {}).get("author") or {}).get("display_name")
            if name:
                authors.append(name)

        oa = item.get("open_access") or {}
        return Paper(
            title=title,
            authors=authors,
            journal=journal,
            issns=issns,
            year=item.get("publication_year"),
            doi=self.clean_doi(item.get("doi") or (item.get("ids") or {}).get("doi") or ""),
            abstract=self._abstract(item.get("abstract_inverted_index")),
            url=landing or "",
            pdf_urls=pdfs,
            oa_status=(oa.get("oa_status") or "").lower(),
            is_oa=bool(oa.get("is_oa")) or bool(pdfs),
            cited_by=int(item.get("cited_by_count") or 0),
            pub_type=(item.get("type_crossref") or item.get("type") or "").replace("-", " "),
            source=self.name,
            source_id=(item.get("id") or "").rsplit("/", 1)[-1],
            extra={"is_preprint": is_preprint, "journal_type": journal_type},
        )

    @staticmethod
    def _abstract(inverted: dict | None) -> str:
        """OpenAlex 用倒排索引保存摘要，这里还原成文本。"""
        if not inverted:
            return ""
        positions: list[tuple[int, str]] = []
        for word, idxs in inverted.items():
            for i in idxs:
                positions.append((i, word))
        positions.sort()
        return " ".join(word for _, word in positions)[:4000]
