# -*- coding: utf-8 -*-
"""PubMed 数据源（NCBI E-utilities，无需 API Key）。"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ...core import http
from ...core.models import Paper
from ..base import Source, SourceMeta

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


class PubMedSource(Source):
    meta = SourceMeta(
        name="PubMed",
        label="PubMed",
        homepage="https://pubmed.ncbi.nlm.nih.gov",
        description="美国国立医学图书馆生物医学文献库，含 PubMed Central 免费全文链接",
    )
    min_interval = 0.4

    def search(self, query: str, limit: int) -> list[Paper]:
        self._throttle()
        params: dict = {"db": "pubmed", "term": query, "retmode": "json", "retmax": min(200, max(1, limit)), "sort": "relevance"}
        if self.api_key:
            params["api_key"] = self.api_key
        if self.mailto:
            params["email"] = self.mailto
        data = http.get_json(ESEARCH, params=params, timeout=40)
        ids = ((data.get("esearchresult") or {}).get("idlist")) or []
        if not ids:
            return []

        self._throttle()
        fetch_params: dict = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
        if self.api_key:
            fetch_params["api_key"] = self.api_key
        if self.mailto:
            fetch_params["email"] = self.mailto
        resp = http.request(EFETCH, params=fetch_params, timeout=60, retries=2)
        return self._parse(resp.text())

    # ------------------------------------------------------------------ #
    def _parse(self, xml: str) -> list[Paper]:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []
        out: list[Paper] = []
        for art in root.findall(".//PubmedArticle"):
            medline = art.find("MedlineCitation")
            if medline is None:
                continue
            pmid = (medline.findtext("PMID") or "").strip()
            article = medline.find("Article")
            if article is None:
                continue
            title = "".join(article.find("ArticleTitle").itertext()).strip() if article.find("ArticleTitle") is not None else ""
            if not title:
                continue
            journal_el = article.find("Journal")
            journal = (journal_el.findtext("Title") or "").strip() if journal_el is not None else ""
            issns = []
            if journal_el is not None:
                for issn_el in journal_el.findall(".//ISSN"):
                    if issn_el.text:
                        issns.append(issn_el.text.strip())
            year = None
            pubdate = journal_el.find(".//PubDate") if journal_el is not None else None
            if pubdate is not None:
                for tag in ("Year", "MedlineDate"):
                    val = pubdate.findtext(tag)
                    if val and val[:4].isdigit():
                        year = int(val[:4])
                        break
            authors = []
            for a in article.findall(".//AuthorList/Author"):
                name = " ".join(x for x in [a.findtext("ForeName"), a.findtext("LastName")] if x)
                if not name:
                    name = a.findtext("CollectiveName") or ""
                if name:
                    authors.append(name.strip())
            abstract = " ".join(
                "".join(t.itertext()).strip()
                for t in article.findall(".//Abstract/AbstractText")
            ).strip()

            doi = ""
            pmcid = ""
            for aid in art.findall(".//ArticleIdList/ArticleId"):
                idtype = (aid.get("IdType") or "").lower()
                if idtype == "doi" and aid.text:
                    doi = self.clean_doi(aid.text)
                if idtype == "pmc" and aid.text:
                    pmcid = aid.text.strip()

            pdfs = []
            if pmcid:
                pdfs.append(f"https://europepmc.org/articles/{pmcid}?pdf=render")
            landing = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""
            out.append(
                Paper(
                    title=title,
                    authors=authors,
                    journal=journal,
                    issns=issns,
                    year=year,
                    doi=doi,
                    abstract=abstract[:4000],
                    url=landing,
                    pdf_urls=pdfs,
                    oa_status="green" if pdfs else "closed",
                    is_oa=bool(pdfs),
                    cited_by=0,
                    pub_type="journal article",
                    source=self.name,
                    source_id=pmid,
                    extra={"pmid": pmid, "pmcid": pmcid},
                )
            )
        return out
