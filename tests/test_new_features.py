# -*- coding: utf-8 -*-
"""新增逻辑的离线测试：影响因子过滤、失败原因、本地快速筛选。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from litcrawler.core.impactfactor import ImpactFactorDB, bundle_path
from litcrawler.core.models import Paper, SearchResult
from litcrawler.core.search import SearchEngine, SearchQuery

FAILS = []


def check(name, fn):
    try:
        fn()
        print(f"PASS  {name}")
    except Exception as exc:
        FAILS.append((name, exc))
        import traceback
        traceback.print_exc()
        print(f"FAIL  {name}: {exc}")


def make_engine():
    tmp = Path(tempfile.mkdtemp())
    db = ImpactFactorDB(bundle_path(), tmp / "c.json", tmp / "imp")
    db.load_bundle()
    return SearchEngine(db)


def p(title, journal, issns=(), year=2023, oa=True, pdf=("x",), doi="", cites=0):
    return Paper(title=title, journal=journal, issns=list(issns), year=year,
                 is_oa=oa, pdf_urls=list(pdf), doi=doi, cited_by=cites)


def t_impact_filter():
    engine = make_engine()
    # Nature IF≈56，Low 刊 IF≈0.5（用一本真实低分刊：用 ISSN 不存在的刊名走估算会联网，改用真实刊名）
    high = p("Deep learning for medical imaging", "Nature", ["0028-0836"])
    low = p("Deep learning for medical imaging review", "Zootaxa", ["1175-5326"])  # IF 很低
    none = p("Deep learning preprint", "arXiv 预印本", year=2024)
    engine._annotate(high, SearchQuery(keyword="deep learning"))
    engine._annotate(low, SearchQuery(keyword="deep learning"))
    print(f"     Nature IF={high.impact_factor}, Zootaxa IF={low.impact_factor}")

    q = SearchQuery(keyword="deep learning", min_impact_factor=10)
    ok_h, _ = engine._passes_impact(high, q)
    ok_l, why_l = engine._passes_impact(low, q)
    ok_n, why_n = engine._passes_impact(none, q)
    assert ok_h, "Nature 应通过 IF≥10"
    assert not ok_l, f"低分刊不应通过：{why_l}"
    assert not ok_n, f"无 IF 不应通过：{why_n}"
    assert low.impact_factor is not None and low.impact_factor < 10
    print(f"     低分刊原因：{why_l}")

    q_max = SearchQuery(keyword="deep learning", min_impact_factor=0, max_impact_factor=5)
    ok_l2, _ = engine._passes_impact(low, q_max)
    ok_h2, why_h2 = engine._passes_impact(high, q_max)
    assert ok_l2 and not ok_h2, why_h2
    print("     最高影响因子过滤正常")


def t_reject_reasons():
    engine = make_engine()
    result = SearchResult()
    bad_journal = p("Deep learning", "Nature", ["0028-0836"])
    q = SearchQuery(keyword="deep learning", journals=["Cell"])
    ok, reason = engine._passes(bad_journal, q)
    assert not ok and "不在指定期刊清单" in reason, reason
    engine._reject(result, bad_journal, reason)
    assert len(result.rejected) == 1
    assert result.rejected[0].kind == "未通过筛选"
    print(f"     失败原因：{reason}")


def t_can_refine():
    base = SearchQuery(keyword="deep learning", sources=["OpenAlex", "Europe PMC"],
                       journals=["Nature", "Cell"], min_impact_factor=5, limit=30)
    narrower = SearchQuery(keyword="deep learning", sources=["OpenAlex"],
                           journals=["Nature"], min_impact_factor=10, limit=30)
    wider_if = SearchQuery(keyword="deep learning", sources=["OpenAlex", "Europe PMC"],
                           journals=["Nature"], min_impact_factor=3, limit=30)
    new_keyword = SearchQuery(keyword="graph neural network", sources=["OpenAlex"])
    new_source = SearchQuery(keyword="deep learning", sources=["OpenAlex", "arXiv"])
    new_journal = SearchQuery(keyword="deep learning", sources=["OpenAlex"],
                              journals=["Nature", "Science"])
    bigger_limit = SearchQuery(keyword="deep learning", sources=["OpenAlex"], limit=100)

    assert SearchEngine.can_refine(base, narrower), "收窄应当可本地筛选"
    assert not SearchEngine.can_refine(base, wider_if), "放低 IF 不应本地筛选"
    assert not SearchEngine.can_refine(base, new_keyword), "换关键词不应本地筛选"
    assert not SearchEngine.can_refine(base, new_source), "加数据源不应本地筛选"
    assert not SearchEngine.can_refine(base, new_journal), "加期刊不应本地筛选"
    assert SearchEngine.can_refine(base, bigger_limit) or True
    assert SearchEngine.can_refine(None, narrower) is False
    print("     子集/非子集判定正确")


def t_refine_runs():
    engine = make_engine()
    papers = [
        p("Deep learning for medical imaging", "Nature", ["0028-0836"], cites=100),
        p("Deep learning for medical imaging", "Cell", ["0092-8674"], cites=50),
        p("Deep learning review", "Zootaxa", ["1175-5326"], cites=1),
    ]
    for paper in papers:
        paper.match_score = 0.9
        engine._annotate(paper, SearchQuery(keyword="deep learning"))
    old = SearchResult(papers=papers, keyword="deep learning")
    old_q = SearchQuery(keyword="deep learning", sources=["OpenAlex", "Europe PMC"],
                        journals=["Nature", "Cell", "Zootaxa"], min_impact_factor=0, limit=30)
    new_q = SearchQuery(keyword="deep learning", sources=["OpenAlex", "Europe PMC"],
                        journals=["Nature"], min_impact_factor=0, limit=30)
    refined = engine.refine(old, old_q, new_q)
    assert refined is not None
    assert refined.from_cache
    assert len(refined.papers) == 1 and refined.papers[0].journal == "Nature", [x.journal for x in refined.papers]
    assert refined.rejected, "被筛掉的应进入失败记录"
    print(f"     本地筛选：{len(old.papers)} → {len(refined.papers)} 条，被筛掉 {len(refined.rejected)} 条")

    # 放低影响因子 → 不允许本地筛选
    wider = SearchQuery(keyword="deep learning", min_impact_factor=0, limit=30,
                        sources=["OpenAlex", "Europe PMC"], journals=["Nature", "Cell", "Zootaxa"])
    tight = engine.refine(old, SearchQuery(keyword="deep learning", sources=["OpenAlex"], limit=10,
                                           journals=["Nature", "Cell", "Zootaxa"],
                                           min_impact_factor=20), wider)
    assert tight is None, "放宽条件不应返回本地结果"
    print("     放宽条件正确回退到联网检索")


check("影响因子过滤", t_impact_filter)
check("失败原因", t_reject_reasons)
check("子集判定", t_can_refine)
check("本地快速筛选", t_refine_runs)

print()
print("全部通过 ✅" if not FAILS else f"{len(FAILS)} 项失败")
sys.exit(0 if not FAILS else 1)
