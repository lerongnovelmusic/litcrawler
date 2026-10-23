# -*- coding: utf-8 -*-
"""自定义站点数据源（正则抽取）。

不少期刊网站 / 数据库没有公开 API，但检索结果页是服务端渲染的 HTML。
用户可以填写“检索地址模板 + 字段正则”，把任意站点接进本程序。

``{query}`` 会被替换成 URL 编码后的关键词，``{page}`` 替换成页码。

注意：只支持服务端渲染的页面；依赖 JavaScript 渲染（如知网、万方）或需要
登录的站点无法直接使用，请改用本程序内置的开放数据源。
"""

from __future__ import annotations

import html
import re
import urllib.parse

from ..core import http
from ..core.models import Paper
from .base import Source, SourceError, SourceMeta

DEFAULT_LIST_RE = r'<(?:article|li|div|tr)[^>]*class="[^"]*(?:result|item|record|entry)[^"]*"[^>]*>.*?</(?:article|li|div|tr)>'


class SiteProfile:
    """用户自定义站点配置。"""

    def __init__(
        self,
        name: str,
        url_template: str,
        *,
        enabled: bool = True,
        list_regex: str = "",
        title_regex: str = r'<a[^>]*>(.*?)</a>',
        authors_regex: str = "",
        journal_regex: str = "",
        year_regex: str = r'\b(19|20)\d{2}\b',
        doi_regex: str = r'10\.\d{4,9}/[-._;()/:A-Za-z0-9]+',
        pdf_regex: str = r'href="([^"]+\.pdf[^"]*)"',
        base_url: str = "",
        max_pages: int = 1,
        encoding: str = "",
        headers: dict | None = None,
    ):
        self.name = name
        self.url_template = url_template
        self.enabled = enabled
        self.list_regex = list_regex or DEFAULT_LIST_RE
        self.title_regex = title_regex
        self.authors_regex = authors_regex
        self.journal_regex = journal_regex
        self.year_regex = year_regex
        self.doi_regex = doi_regex
        self.pdf_regex = pdf_regex
        self.base_url = base_url
        self.max_pages = max(1, min(10, int(max_pages or 1)))
        self.encoding = encoding
        self.headers = headers or {}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "url_template": self.url_template,
            "enabled": self.enabled,
            "list_regex": self.list_regex,
            "title_regex": self.title_regex,
            "authors_regex": self.authors_regex,
            "journal_regex": self.journal_regex,
            "year_regex": self.year_regex,
            "doi_regex": self.doi_regex,
            "pdf_regex": self.pdf_regex,
            "base_url": self.base_url,
            "max_pages": self.max_pages,
            "encoding": self.encoding,
            "headers": self.headers,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SiteProfile":
        known = {
            "name", "url_template", "enabled", "list_regex", "title_regex", "authors_regex",
            "journal_regex", "year_regex", "doi_regex", "pdf_regex", "base_url", "max_pages",
            "encoding", "headers",
        }
        return cls(**{k: v for k, v in data.items() if k in known})

    def build_url(self, query: str, page: int) -> str:
        return (
            self.url_template.replace("{query}", urllib.parse.quote_plus(query))
            .replace("{query_raw}", query)
            .replace("{page}", str(page))
        )


def _clean_html(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _first(pattern: str, text: str, group: int = 1) -> str:
    if not pattern:
        return ""
    try:
        m = re.search(pattern, text, flags=re.S | re.I)
    except re.error:
        return ""
    if not m:
        return ""
    try:
        return m.group(group)
    except (IndexError, re.error):  # pragma: no cover
        return m.group(0)


class ProfileSource(Source):
    """基于 :class:`SiteProfile` 的通用数据源。"""

    meta = SourceMeta(
        name="自定义站点",
        label="自定义站点",
        homepage="",
        description="用「检索地址模板 + 字段正则」接入任意可抓取的期刊网站",
    )

    def __init__(self, profile: SiteProfile, **kwargs):
        super().__init__(**kwargs)
        self.profile = profile
        self.meta = SourceMeta(
            name=profile.name,
            label=profile.name,
            homepage=profile.base_url,
            description=f"自定义站点：{profile.url_template[:60]}",
        )

    @property
    def name(self) -> str:
        return self.profile.name

    def search(self, query: str, limit: int) -> list[Paper]:
        profile = self.profile
        if "{query}" not in profile.url_template and "{query_raw}" not in profile.url_template:
            raise SourceError("自定义站点地址模板缺少 {query} 占位符")
        per_page_cap = max(1, limit)
        papers: list[Paper] = []
        for page in range(1, profile.max_pages + 1):
            if len(papers) >= per_page_cap:
                break
            url = profile.build_url(query, page)
            headers = {"Accept": "text/html,application/xhtml+xml"}
            headers.update(profile.headers)
            resp = http.request(url, timeout=40, retries=2, browser_ua=True, headers=headers)
            body = resp.text(profile.encoding or None)
            blocks = self._blocks(body)
            if not blocks:
                raise SourceError("未匹配到结果区块（请检查 list_regex）")
            for block in blocks:
                paper = self._parse_block(block, url)
                if paper and paper.title:
                    papers.append(paper)
                if len(papers) >= per_page_cap:
                    break
        return papers[:limit]

    def _blocks(self, body: str) -> list[str]:
        pattern = self.profile.list_regex or DEFAULT_LIST_RE
        try:
            if "(" in pattern and pattern.count("(") == pattern.count(")"):
                blocks = [m.group(1) if m.groups() else m.group(0) for m in re.finditer(pattern, body, flags=re.S | re.I)]
                if blocks:
                    return blocks
            return [m.group(0) for m in re.finditer(pattern, body, flags=re.S | re.I)]
        except re.error as exc:
            raise SourceError(f"list_regex 语法错误：{exc}") from exc

    def _parse_block(self, block: str, page_url: str) -> Paper | None:
        p = self.profile
        title = _clean_html(_first(p.title_regex, block))
        if not title:
            return None
        authors = [
            a.strip()
            for a in re.split(r"[,;，；]", _clean_html(_first(p.authors_regex, block)))
            if a.strip()
        ]
        journal = _clean_html(_first(p.journal_regex, block))
        year_match = re.search(p.year_regex, block) if p.year_regex else None
        year = None
        if year_match:
            digits = re.search(r"(19|20)\d{2}", year_match.group(0))
            if digits:
                year = int(digits.group(0))
        doi = ""
        doi_match = re.search(p.doi_regex, block, flags=re.I) if p.doi_regex else None
        if doi_match:
            doi = self.clean_doi(doi_match.group(0))
        pdfs: list[str] = []
        if p.pdf_regex:
            for m in re.finditer(p.pdf_regex, block, flags=re.S | re.I):
                href = m.group(1) if m.groups() else m.group(0)
                full = urllib.parse.urljoin(p.base_url or page_url, href)
                if full not in pdfs:
                    pdfs.append(full)
        return Paper(
            title=title,
            authors=authors,
            journal=journal,
            year=year,
            doi=doi,
            url=page_url,
            pdf_urls=pdfs,
            is_oa=bool(pdfs),
            oa_status="unknown",
            source=self.name,
            pub_type="",
        )


#: 常见开放站点的现成模板（用户可直接选用）
BUILTIN_PROFILES: list[dict] = [
    {
        "name": "PubMed",
        "url_template": "https://pubmed.ncbi.nlm.nih.gov/?term={query}&size=50&page={page}",
        "list_regex": r'<article[^>]*class="[^"]*full-docsum[^"]*"[^>]*>(.*?)</article>',
        "title_regex": r'class="docsum-title"[^>]*>(.*?)</a>',
        "authors_regex": r'class="docsum-authors[^"]*"[^>]*>(.*?)</span>',
        "journal_regex": r'class="docsum-journal-citation"[^>]*>(.*?)</span>',
        "year_regex": r"\b(19|20)\d{2}\b",
        "base_url": "https://pubmed.ncbi.nlm.nih.gov",
        "max_pages": 2,
        "enabled": False,
    },
    {
        "name": "示例：某期刊站",
        "url_template": "https://example.com/search?q={query}&p={page}",
        "list_regex": r'<div[^>]*class="[^"]*result-item[^"]*"[^>]*>(.*?)</div>\s*</div>',
        "title_regex": r'<h3[^>]*>\s*<a[^>]*>(.*?)</a>',
        "authors_regex": r'class="authors"[^>]*>(.*?)</',
        "journal_regex": r'class="journal"[^>]*>(.*?)</',
        "doi_regex": r'10\.\d{4,9}/[-._;()/:A-Za-z0-9]+',
        "pdf_regex": r'href="([^"]+\.pdf[^"]*)"',
        "base_url": "https://example.com",
        "max_pages": 1,
        "enabled": False,
    },
]
