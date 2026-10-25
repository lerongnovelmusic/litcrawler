# -*- coding: utf-8 -*-
"""检索编排：多源并发检索 → 去重合并 → 补充元数据 → 期刊/影响因子过滤 → 模糊重排。

模糊检索策略（"搜索时支持模糊搜索"）：

* 关键词：在数据源侧使用其原生相关度检索，再在本地用
  :mod:`litcrawler.core.fuzzy` 计算相似度，低于阈值的结果被剔除或降权；
* 期刊名：支持中英文别名、缩写展开、ISSN 精确匹配与名称模糊匹配；
* 影响因子：内置 JCR 精简库 + 用户导入库 + OpenAlex 估算，逐条标注数值与分区。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Iterable

from . import http
from .fuzzy import best_similarity, matches_journal, normalize, similarity
from .impactfactor import ImpactFactorDB
from .models import Paper, RejectedPaper, SearchResult, SourceReport
from ..sources import Source, SourceError, build_registry
from ..sources.base import SourceRegistry
from ..sources.sites import ProfileSource, SiteProfile

ProgressFn = Callable[[str], None]


@dataclass
class SearchQuery:
    """一次检索的全部输入条件。"""

    keyword: str = ""
    sources: list[str] = field(default_factory=lambda: ["OpenAlex", "Crossref", "Europe PMC", "arXiv", "DOAJ"])
    journals: list[str] = field(default_factory=list)
    min_impact_factor: float = 0.0
    max_impact_factor: float = 0.0
    year_from: int = 0
    year_to: int = 0
    limit: int = 50
    fuzzy_keyword: bool = True
    fuzzy_journal: bool = True
    only_open_access: bool = False
    journal_strict: bool = False  # True=仅按期刊过滤，不额外做关键词相似度剔除
    min_similarity: float = 0.0     # 0 表示按 fuzzy_keyword 自动决定
    if_year: str = ""
    journal_contains: str = ""
    title_contains: str = ""

    def describe(self) -> str:
        bits = [f"关键词：{self.keyword or '(空)'}"]
        bits.append("数据源：" + "、".join(self.sources) if self.sources else "数据源：(无)")
        if self.journals:
            bits.append("期刊：" + "、".join(self.journals))
        if self.min_impact_factor:
            bits.append(f"影响因子≥{self.min_impact_factor:g}")
        if self.max_impact_factor:
            bits.append(f"≤{self.max_impact_factor:g}")
        if self.year_from or self.year_to:
            bits.append(f"年份：{self.year_from or '不限'}-{self.year_to or '不限'}")
        if self.only_open_access:
            bits.append("仅开放获取")
        if self.title_contains:
            bits.append(f"标题含：{self.title_contains}")
        return " | ".join(bits)


class SearchEngine:
    """检索引擎。"""

    def __init__(self, impact_db: ImpactFactorDB | None = None, registry: SourceRegistry | None = None):
        self.impact_db = impact_db
        self.registry = registry or build_registry()
        self._openalex_estimate_cache: dict[str, float | None] = {}

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: SearchQuery,
        *,
        mailto: str = "",
        pubmed_key: str = "",
        site_profiles: Iterable[SiteProfile] = (),
        timeout: float = 30.0,
        progress: ProgressFn | None = None,
        should_stop: Callable[[], bool] | None = None,
        per_source_limit: int | None = None,
    ) -> SearchResult:
        started = time.time()
        result = SearchResult(keyword=query.keyword)
        log = progress or (lambda _msg: None)
        stop = should_stop or (lambda: False)

        sources = self._instantiate(query.sources, site_profiles, mailto, pubmed_key)
        if not sources:
            result.notes.append("没有可用的数据源，请至少勾选一个。")
            return result

        fetch_limit = per_source_limit or max(query.limit * 3, 30)
        if not query.keyword.strip():
            result.notes.append("请输入关键词后再检索。")
            for _name, _src in sources:
                result.reports.append(SourceReport(name=_name, error="未执行（关键词为空）"))
            log("关键词为空，未发起检索。")
            return result
        log(f"开始检索：{query.keyword}，共 {len(sources)} 个数据源…")

        collected: list[Paper] = []
        with ThreadPoolExecutor(max_workers=min(6, len(sources))) as pool:
            futures = {}
            for name, source in sources:
                if stop():
                    break
                futures[pool.submit(self._run_source, source, query.keyword, fetch_limit)] = name
            for future in as_completed(futures):
                name = futures[future]
                try:
                    papers, elapsed = future.result()
                except Exception as exc:  # 数据源异常不影响整体
                    result.reports.append(SourceReport(name=name, error=str(exc)))
                    log(f"  ✗ {name} 检索失败：{str(exc)[:80]}")
                    continue
                before = len(papers)
                result.reports.append(SourceReport(name=name, found=before, elapsed=elapsed))
                collected.extend(papers)
                log(f"  ✓ {name} 返回 {before} 条（{elapsed:.1f}s）")
                if stop():
                    break

        log(f"共获取 {len(collected)} 条原始记录，正在去重合并…")
        merged = self._dedupe(collected)
        result.notes.append(f"去重后 {len(merged)} 条")
        log(f"去重后剩 {len(merged)} 条，正在补充期刊信息与影响因子…")

        kept: list[Paper] = []
        needs_doi_enrich = [p for p in merged if not p.journal and p.doi][:40]
        if needs_doi_enrich:
            self._enrich_by_doi(needs_doi_enrich, mailto=mailto, timeout=timeout)

        for paper in merged:
            if stop():
                break
            ok, reason = self._passes(paper, query)
            if not ok:
                self._reject(result, paper, reason)
                continue
            self._annotate(paper, query)
            ok_if, if_reason = self._passes_impact(paper, query)
            if not ok_if:
                self._reject(result, paper, if_reason)
                continue
            kept.append(paper)

        kept.sort(key=lambda p: (-p.match_score, -(p.impact_factor or 0), -p.cited_by, -(p.year or 0)))
        result.papers = kept[: query.limit]
        result.elapsed = time.time() - started
        for report in result.reports:
            report.kept = sum(
                1
                for p in result.papers
                if report.name in (p.extra.get("all_sources") or [p.source])
            )
        log(f"完成：命中 {len(result.papers)} 条（{result.elapsed:.1f}s）")
        return result

    # ------------------------------------------------------------------ #
    # 快速「本地筛选」：新条件是旧条件的子集时，不必重新联网
    # ------------------------------------------------------------------ #
    REJECT_CAP = 400

    @staticmethod
    def _reject(result: SearchResult, paper: Paper, reason: str, kind: str = "未通过筛选") -> None:
        if reason and len(result.rejected) < SearchEngine.REJECT_CAP:
            result.rejected.append(RejectedPaper(paper, reason, kind))

    @staticmethod
    def can_refine(old: "SearchQuery | None", new: "SearchQuery") -> bool:
        """新检索范围是否是上一次范围的**子集**（子集才能安全地本地筛选）。"""
        if old is None:
            return False
        if (old.keyword or "").strip().lower() != (new.keyword or "").strip().lower():
            return False
        if old.if_year != new.if_year:
            return False
        # 模糊开关：只能从模糊收紧为严格，不能反过来（反过来会变宽）
        if new.fuzzy_keyword and not old.fuzzy_keyword:
            return False
        if new.fuzzy_journal and not old.fuzzy_journal:
            return False
        if old.min_similarity and new.min_similarity < old.min_similarity:
            return False
        # 数据源：只能是旧的子集
        if not set(new.sources) <= set(old.sources):
            return False
        # 期刊：新的清单必须是旧清单的子集（空=不限，不算子集）
        old_j = {j.strip().lower() for j in old.journals if j.strip()}
        new_j = {j.strip().lower() for j in new.journals if j.strip()}
        if not new_j <= old_j:
            return False
        # 影响因子区间只能收窄
        if new.min_impact_factor < old.min_impact_factor:
            return False
        if new.max_impact_factor:
            if old.max_impact_factor and new.max_impact_factor > old.max_impact_factor:
                return False
        elif old.max_impact_factor:
            return False
        # 年份区间只能收窄
        if new.year_from < old.year_from:
            return False
        if new.year_to:
            if old.year_to and new.year_to > old.year_to:
                return False
        elif old.year_to:
            return False
        # 其它开关只能变严
        if old.only_open_access and not new.only_open_access:
            return False
        if old.title_contains and old.title_contains.lower() not in new.title_contains.lower():
            return False
        if old.journal_contains and old.journal_contains.lower() not in new.journal_contains.lower():
            return False
        return True

    def refine(
        self,
        previous: SearchResult,
        old_query: SearchQuery,
        new_query: SearchQuery,
        *,
        progress: ProgressFn | None = None,
    ) -> SearchResult | None:
        """在已有结果上做本地筛选；不可行时返回 ``None``（调用方应重新检索）。"""
        log = progress or (lambda _m: None)
        if not self.can_refine(old_query, new_query):
            return None
        # 结果集曾达到上限，放宽条数就需要新数据
        if new_query.limit > old_query.limit and len(previous.papers) >= old_query.limit:
            return None

        started = time.time()
        result = SearchResult(keyword=new_query.keyword, from_cache=True)
        result.reports = [
            SourceReport(name=r.name, found=r.found, error=r.error, elapsed=r.elapsed)
            for r in previous.reports
        ]
        old_sources = {s.strip().lower() for s in old_query.sources}
        new_sources = {s.strip().lower() for s in new_query.sources}
        narrowed_sources = new_sources != old_sources

        log("检索条件收窄，正在本地筛选上次结果（无需重新联网）…")
        kept: list[Paper] = []
        for paper in previous.papers:
            if narrowed_sources:
                paper_sources = {s.strip().lower() for s in (paper.extra.get("all_sources") or [paper.source]) if s}
                if paper_sources and not (paper_sources & new_sources):
                    self._reject(result, paper, "不在当前勾选的数据源中")
                    continue
            ok, reason = self._passes(paper, new_query)
            if not ok:
                self._reject(result, paper, reason or "不满足新的筛选条件")
                continue
            ok_if, if_reason = self._passes_impact(paper, new_query)
            if not ok_if:
                self._reject(result, paper, if_reason)
                continue
            kept.append(paper)

        kept.sort(key=lambda p: (-p.match_score, -(p.impact_factor or 0), -p.cited_by, -(p.year or 0)))
        result.papers = kept[: new_query.limit]
        result.elapsed = time.time() - started
        result.notes.append("复用上次检索结果做本地筛选（未重新联网）")
        for report in result.reports:
            report.kept = sum(
                1 for p in result.papers if report.name in (p.extra.get("all_sources") or [p.source])
            )
        log(f"本地筛选完成：{len(result.papers)} 条（{result.elapsed:.2f}s，上一轮 {len(previous.papers)} 条）")
        return result

    # ------------------------------------------------------------------ #
    def _instantiate(
        self,
        selected: Iterable[str],
        site_profiles: Iterable[SiteProfile],
        mailto: str,
        pubmed_key: str,
    ) -> list[tuple[str, Source]]:
        out: list[tuple[str, Source]] = []
        known = set(self.registry.names())
        for key in selected:
            if key in known:
                try:
                    src = self.registry.create(
                        key,
                        mailto=mailto,
                        api_key=pubmed_key if key == "PubMed" else "",
                    )
                except Exception:
                    continue
                out.append((key, src))
            else:
                for profile in site_profiles:
                    if profile.name == key and profile.enabled:
                        out.append((key, ProfileSource(profile, mailto=mailto)))
        return out

    @staticmethod
    def _run_source(source: Source, keyword: str, limit: int) -> tuple[list[Paper], float]:
        t0 = time.time()
        if not keyword.strip():
            return [], time.time() - t0
        try:
            papers = source.search(keyword.strip(), limit)
        except SourceError:
            raise
        except http.HttpError as exc:
            raise SourceError(str(exc)) from exc
        return papers, time.time() - t0

    # ------------------------------------------------------------------ #
    # 去重与合并
    # ------------------------------------------------------------------ #
    @staticmethod
    def _dedupe(papers: list[Paper]) -> list[Paper]:
        by_key: dict[str, Paper] = {}
        order: list[str] = []
        for paper in papers:
            if not paper.title:
                continue
            key = paper.key
            if key not in by_key:
                by_key[key] = paper
                order.append(key)
                continue
            base = by_key[key]
            # 合并互补字段
            base.authors = base.authors or paper.authors
            if len(paper.abstract) > len(base.abstract):
                base.abstract = paper.abstract
            base.journal = base.journal or paper.journal
            if not base.issns:
                base.issns = list(paper.issns)
            base.year = base.year or paper.year
            base.doi = base.doi or paper.doi
            base.url = base.url or paper.url
            base.cited_by = max(base.cited_by, paper.cited_by)
            base.is_oa = base.is_oa or paper.is_oa
            base.oa_status = base.oa_status or paper.oa_status
            for url in paper.pdf_urls:
                if url not in base.pdf_urls:
                    base.pdf_urls.append(url)
            sources = base.extra.setdefault("all_sources", [base.source])
            if paper.source and paper.source not in sources:
                sources.append(paper.source)
            base.extra.setdefault("alt_titles", []).append(paper.title)

        # 标题二次去重（同一文献不同 DOI 记录，或 DOI 缺失）
        merged: list[Paper] = []
        seen_titles: dict[str, Paper] = {}
        for key in order:
            paper = by_key[key]
            tkey = paper.title_key[:110]
            if tkey and tkey in seen_titles:
                host = seen_titles[tkey]
                for url in paper.pdf_urls:
                    if url not in host.pdf_urls:
                        host.pdf_urls.append(url)
                host.cited_by = max(host.cited_by, paper.cited_by)
                host.impact_factor = host.impact_factor or paper.impact_factor
                for url in (paper.extra.get("all_sources") or [paper.source]):
                    srcs = host.extra.setdefault("all_sources", [host.source])
                    if url not in srcs:
                        srcs.append(url)
                continue
            seen_titles[tkey] = paper
            merged.append(paper)
        return merged

    # ------------------------------------------------------------------ #
    # 过滤
    # ------------------------------------------------------------------ #
    def _passes(self, paper: Paper, query: SearchQuery) -> tuple[bool, str]:
        if query.year_from and (paper.year or 0) and paper.year < query.year_from:
            return False, f"年份 {paper.year} 早于 {query.year_from}"
        if query.year_to and (paper.year or 0) and paper.year > query.year_to:
            return False, f"年份 {paper.year} 晚于 {query.year_to}"
        if query.only_open_access and not paper.is_oa:
            return False, "不是开放获取（按设置排除）"
        if query.title_contains and query.title_contains.lower() not in paper.title.lower():
            return False, f"标题不包含「{query.title_contains}」"
        if query.journal_contains and query.journal_contains.lower() not in paper.journal.lower():
            return False, f"期刊名不包含「{query.journal_contains}」"

        # 期刊过滤
        if query.journals:
            hit, matched = matches_journal(
                paper.journal, paper.issns, query.journals, fuzzy=query.fuzzy_journal
            )
            if not hit:
                # arXiv 等预印本没有期刊名，若用户勾了期刊就不应通过
                return False, f"期刊「{paper.journal or '未知'}」不在指定期刊清单中"
            paper.matched_journal = matched

        # 关键词相似度（在本地做模糊判断）
        if query.keyword.strip():
            score = best_similarity(query.keyword, [paper.title, *(paper.extra.get("alt_titles") or [])])
            if score < 0.6 and paper.abstract:
                score = max(score, similarity(query.keyword, paper.title + " " + paper.abstract[:400]) * 0.9)
            paper.match_score = max(paper.match_score, score)
            threshold = query.min_similarity or (0.45 if query.fuzzy_keyword else 0.72)
            if query.journal_strict or query.journals:
                threshold = min(threshold, 0.25)  # 已按期刊筛选时放宽关键词
            if score < threshold:
                return False, f"与关键词相似度 {score:.2f} 低于阈值 {threshold:.2f}"
        return True, ""

    @staticmethod
    def _passes_impact(paper: Paper, query: SearchQuery) -> tuple[bool, str]:
        """影响因子区间过滤（在 ``_annotate`` 之后调用）。"""
        value = paper.impact_factor
        if query.min_impact_factor:
            if value is None:
                return False, f"没有影响因子数据（要求 ≥ {query.min_impact_factor:g}）"
            if value < query.min_impact_factor:
                return False, f"影响因子 {value:g} 低于最低值 {query.min_impact_factor:g}"
        if query.max_impact_factor:
            if value is None:
                return False, f"没有影响因子数据（要求 ≤ {query.max_impact_factor:g}）"
            if value > query.max_impact_factor:
                return False, f"影响因子 {value:g} 高于最高值 {query.max_impact_factor:g}"
        return True, ""

    # ------------------------------------------------------------------ #
    # 影响因子标注
    # ------------------------------------------------------------------ #
    def _annotate(self, paper: Paper, query: SearchQuery) -> None:
        # 预印本 / 仓储记录没有期刊指标，避免拿仓储名去撞期刊库
        journal = paper.journal or ""
        is_preprint = bool(paper.extra.get("is_preprint")) or (")" in journal and ("预印本" in journal or "仓储" in journal))
        if self.impact_db is not None and not is_preprint:
            rec = self.impact_db.lookup(
                journal, paper.issns, year=query.if_year or None, fuzzy=query.fuzzy_journal
            )
            if rec is None and paper.issns:
                rec = self.impact_db.lookup(journal, paper.issns, year=None, fuzzy=True)
            if rec is not None:
                paper.impact_factor = rec.jif
                paper.impact_factor_quartile = rec.quartile
                paper.impact_factor_year = rec.year
                paper.impact_factor_source = rec.source
                return
        # 兜底：OpenAlex 2 年篇均被引（近似指标）
        if not is_preprint and (paper.issns or journal):
            estimate = self._openalex_estimate(paper)
            if estimate is not None:
                paper.impact_factor = estimate
                paper.impact_factor_source = "OpenAlex 2yr 篇均被引（估算）"

    def _openalex_estimate(self, paper: Paper) -> float | None:
        key = (paper.issns[0] if paper.issns else paper.journal).lower()
        if not key:
            return None
        if key in self._openalex_estimate_cache:
            return self._openalex_estimate_cache[key]
        value: float | None = None
        try:
            params = {"per-page": 1, "select": "id,display_name,summary_stats,issn_l"}
            if paper.issns:
                params["filter"] = f"issn:{paper.issns[0]}"
            elif paper.journal:
                params["search"] = paper.journal
            data = http.get_json("https://api.openalex.org/sources", params=params, timeout=20, retries=1)
            for src in data.get("results") or []:
                stats = src.get("summary_stats") or {}
                val = stats.get("2yr_mean_citedness")
                if val:
                    value = float(val)
                    break
        except Exception:
            value = None
        self._openalex_estimate_cache[key] = value
        return value

    # ------------------------------------------------------------------ #
    # DOI 反查补全期刊/ISSN
    # ------------------------------------------------------------------ #
    def _enrich_by_doi(self, papers: list[Paper], *, mailto: str, timeout: float) -> None:
        def work(paper: Paper) -> None:
            params = {"select": "id,display_name,primary_location,locations,publication_year"}
            if mailto:
                params["mailto"] = mailto
            try:
                data = http.get_json(
                    f"https://api.openalex.org/works/https://doi.org/{paper.doi}",
                    params={"select": params["select"], **({"mailto": mailto} if mailto else {})},
                    timeout=timeout,
                    retries=1,
                )
            except Exception:
                return
            loc = data.get("primary_location") or {}
            src = loc.get("source") or {}
            if src.get("display_name"):
                paper.journal = src["display_name"]
            issns = [i for i in (src.get("issn") or []) if i]
            if src.get("issn_l") and src["issn_l"] not in issns:
                issns.append(src["issn_l"])
            if issns:
                paper.issns = issns
            pdf_url = loc.get("pdf_url")
            if pdf_url and pdf_url not in paper.pdf_urls:
                paper.pdf_urls.insert(0, pdf_url)
            if data.get("publication_year") and not paper.year:
                paper.year = data["publication_year"]

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(work, papers))

    # ------------------------------------------------------------------ #
    def apply_impact_filter(self, papers: list[Paper], query: SearchQuery) -> list[Paper]:
        """在结果集上按影响因子做二次筛选（用户调整滑块时无需重新联网）。"""
        out = []
        for paper in papers:
            if self.impact_db is not None and paper.impact_factor is None:
                self._annotate(paper, query)
            ok, _reason = self._passes_impact(paper, query)
            if ok:
                out.append(paper)
        return out

    def probe_sources(self, names: Iterable[str], *, mailto: str = "") -> dict[str, str]:
        """连通性自检，返回 ``{源名: 结果文本}``。"""
        results: dict[str, str] = {}
        for name in names:
            try:
                src = self.registry.create(name, mailto=mailto)
            except Exception as exc:
                results[name] = f"不可用：{exc}"
                continue
            try:
                results[name] = src.probe()
            except Exception as exc:
                results[name] = f"失败：{str(exc)[:100]}"
        return results
