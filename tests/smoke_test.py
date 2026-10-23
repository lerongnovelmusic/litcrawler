# -*- coding: utf-8 -*-
"""冒烟测试：导入检查 + 逻辑单测（不联网 / 联网两部分）。"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def check(name, fn):
    try:
        fn()
        print(f"PASS  {name}")
    except Exception as exc:
        FAILS.append((name, exc))
        print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
        traceback.print_exc()


def t_imports():
    import litcrawler
    from litcrawler import cli
    from litcrawler.core import config, downloader, fuzzy, http, impactfactor, models, search
    from litcrawler.gui import app, dialogs, widgets
    from litcrawler.sources import build_registry
    from litcrawler.sources import sites
    reg = build_registry()
    names = reg.names()
    assert names == ["OpenAlex", "Crossref", "Europe PMC", "arXiv", "DOAJ", "PubMed"], names
    for name in names:
        src = reg.create(name)
        assert src.meta.description
    print("     数据源：", "、".join(names))


def t_fuzzy():
    from litcrawler.core.fuzzy import (journal_key, journal_similarity, matches_journal, normalize,
                                       normalize_issn, similarity)
    assert normalize("Café &  Test!!") == "cafe and test"
    assert normalize_issn("1234-5678") == "12345678"
    assert normalize_issn("2041-1723") == "20411723"
    s1 = similarity("neural netwrok", "Deep neural network for image classification")
    s2 = similarity("neural network", "Deep neural network for image classification")
    assert s1 > 0.45, s1
    assert s2 > s1, (s1, s2)
    assert similarity("deep learning", "deep learning") == 1.0

    # 期刊名：应当命中的场景
    for want, journal in [
        ("Nature Communications", "Nature Communications"),
        ("Journal of Cleaner Production", "J Clean Prod"),
        ("J Clean Prod", "Journal of Cleaner Production"),
        ("Applied Physics Letters", "Appl Phys Lett"),
        ("IEEE Trans Med Imaging", "IEEE Transactions on Medical Imaging"),
        ("PLOS ONE", "Plos One"),
        ("The Lancet Oncology", "Lancet Oncol"),
    ]:
        hit, matched = matches_journal(journal, [], [want], fuzzy=True)
        assert hit, f"{want} 应命中 {journal}（相似度 {journal_similarity(want, journal):.3f}）"
        assert matched == want
    # 不应命中的场景
    for want, journal in [
        ("Cell", "Nature"),
        ("Nature", "Nature Communications"),
        ("NEJM", "New England Journal of Medicine"),
        ("Lancet", "The Lancet Oncology"),
        ("IEEE Trans Med Imaging", "IEEE Transactions on Information Technology in Biomedicine"),
    ]:
        hit, _ = matches_journal(journal, [], [want], fuzzy=True)
        assert not hit, f"{want} 不应命中 {journal}"
    # ISSN 精确匹配 + 关闭模糊时不误配
    hit2, _ = matches_journal("随便什么刊", ["2041-1723"], ["2041-1723"])
    assert hit2, "ISSN 应精确命中"
    hit3, _ = matches_journal("Nature Communications", [], ["Nature"], fuzzy=False)
    assert not hit3, "关闭模糊后不应命中"
    assert journal_key("Journal of the American Chemical Society") == \
        journal_key("J Am Chem Soc"), "缩写应展开为同一键"
    print(f"     关键词容错 {s1:.2f}/{s2:.2f}；期刊匹配 7 命中 / 5 排除 全部正确")


def t_models():
    from litcrawler.core.models import Paper
    p = Paper(title="A Test Title", authors=["A B", "C D", "E F", "G H"], year=2020,
              doi="https://doi.org/10.1/ABC")
    assert p.key == "doi:10.1/abc", p.key
    assert p.title_key == "atesttitle"
    assert "4 人" in p.authors_short
    p2 = Paper.from_row(p.to_row())
    assert p2.title == p.title and p2.year == 2020, p2
    p3 = Paper(title="A  Test   Title")
    assert p3.title_key == p.title_key, p3.title_key
    print("     to_row/from_row 往返一致，标题指纹忽略空格")


def t_downloader_utils():
    from litcrawler.core.downloader import Downloader, looks_like_pdf, paper_filename, sanitize_filename
    from litcrawler.core.models import Paper
    assert sanitize_filename('a/b:c*d?e"f<g>h|i') == "a b c d e f g h i"
    p = Paper(title="Deep Learning: A Survey/Review", year=2023, authors=["Zhang Wei", "Li Ming"],
              doi="10.1000/xyz-123")
    name = paper_filename(p, index=7)
    assert name.startswith("007_2023_Zhang"), name
    assert "/" not in name and ":" not in name
    assert looks_like_pdf(b"%PDF-1.7 xxxx")
    assert not looks_like_pdf(b"<html><body>")
    cands = Downloader.pdf_candidates(Paper(doi="10.1/x"))
    assert cands == ["https://doi.org/10.1/x"], cands


def t_impact_db():
    from litcrawler.core.impactfactor import ImpactFactorDB, bundle_path
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    db = ImpactFactorDB(bundle_path(), tmp / "cache.json", tmp / "imports")
    n = db.load_bundle()
    assert n > 50000, n
    rec = db.lookup("Nature", ["0028-0836"])
    assert rec and rec.jif > 30, rec
    rec2 = db.lookup("", ["2041-1723"])
    assert rec2 and rec2.jif > 5, rec2
    years = db.available_years()
    print(f"     内置 {n} 条，年份 {years}；Nature IF={rec.jif} {rec.quartile} ({rec.year})")
    # 模糊兜底
    rec3 = db.lookup("Nautre Communications", [])
    assert rec3 is None or rec3.jif > 0
    # 导入 CSV
    csv_path = tmp / "mine.csv"
    csv_path.write_text("Journal Name,ISSN,2024 JIF,分区\n我的期刊,1234-5678,9.9,Q1\n", encoding="utf-8")
    cnt = db.import_file(csv_path)
    assert cnt == 1, cnt
    rec4 = db.lookup("我的期刊", ["1234-5678"])
    assert rec4 and abs(rec4.jif - 9.9) < 1e-6, rec4
    # 导入 JSON
    json_path = tmp / "mine.json"
    json_path.write_text('[{"name":"另一本期刊","issn":"8765-4321","jif":"3.3"}]', encoding="utf-8")
    assert db.import_file(json_path) == 1
    assert db.lookup("", ["8765-4321"]).jif == 3.3
    print("     导入 CSV/JSON 生效")


def t_search_logic_offline():
    from litcrawler.core.models import Paper
    from litcrawler.core.search import SearchEngine, SearchQuery
    engine = SearchEngine(None)
    papers = [
        Paper(title="Deep learning for medical image segmentation", journal="IEEE TMI",
              issns=["0278-0062"], year=2023, is_oa=True, pdf_urls=["x"]),
        Paper(title="Quantum computing primer", journal="Nature Physics", year=2019),
    ]
    q = SearchQuery(keyword="medical image segmentation", journals=[], min_impact_factor=0, limit=10)
    kept = [p for p in papers if engine._passes(p, q)[0]]
    assert len(kept) == 1, [p.title for p in kept]
    q2 = SearchQuery(keyword="deep learning", journals=["IEEE TMI"], limit=10)
    kept2 = [p for p in papers if engine._passes(p, q2)[0]]
    assert len(kept2) == 1
    # 去重
    dupes = [
        Paper(title="Same Paper", doi="10.1/X", journal="A"),
        Paper(title="Same Paper", doi="10.1/x", abstract="longer", pdf_urls=["p"]),
        Paper(title="Same Paper ", journal="B"),
    ]
    merged = engine._dedupe(dupes)
    assert len(merged) == 1, [p.title for p in merged]
    assert merged[0].pdf_urls == ["p"]
    assert merged[0].abstract == "longer"
    print("     关键词过滤 / DOI 去重 / 标题去重正常")


def t_gui_smoke():
    import os
    from litcrawler.core.config import Config
    if not os.environ.get("DISPLAY") and sys.platform != "win32":
        print("     跳过（无显示环境）")
        return
    from litcrawler.gui.app import App
    import tempfile
    cfg = Config(Path(tempfile.mkdtemp()))
    app = App(cfg)
    app.update()
    app.log("冒烟测试")
    assert app.source_vars
    app.keyword_var.set("deep learning")
    app.select_all()
    app.update()
    app.destroy()
    print("     主窗口可创建并销毁")


check("imports", t_imports)
check("fuzzy", t_fuzzy)
check("models", t_models)
check("downloader-utils", t_downloader_utils)
check("impact-db", t_impact_db)
check("search-logic-offline", t_search_logic_offline)
check("gui-smoke", t_gui_smoke)

print()
if FAILS:
    print(f"{len(FAILS)} 项失败：")
    for name, exc in FAILS:
        print(f"  - {name}: {exc}")
    sys.exit(1)
print("全部通过 ✅")
