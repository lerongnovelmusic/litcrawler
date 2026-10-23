# -*- coding: utf-8 -*-
"""端到端联机测试：检索 → 勾选 → 下载 PDF 到临时目录 → 校验文件。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from litcrawler.core.config import Config
from litcrawler.core.downloader import Downloader
from litcrawler.core.impactfactor import ImpactFactorDB, bundle_path
from litcrawler.core.search import SearchEngine, SearchQuery

tmp = Path(tempfile.mkdtemp(prefix="litcrawler_e2e_"))
cfg = Config(tmp / "cfg")
db = ImpactFactorDB(bundle_path(), cfg.if_cache_path, cfg.if_import_dir)
db.load_bundle()

engine = SearchEngine(db)
query = SearchQuery(
    keyword="graph neural network drug discovery",
    sources=["OpenAlex", "Europe PMC", "arXiv"],
    min_impact_factor=0,
    limit=12,
    fuzzy_keyword=True,
)
result = engine.search(query, mailto="litcrawler@example.com", progress=lambda m: print("  ", m))
print(f"\n命中 {len(result.papers)} 条")

with_pdf = [p for p in result.papers if p.has_pdf][:3]
print(f"选择 {len(with_pdf)} 条带 PDF 直链的文献下载")
for p in with_pdf:
    print(f"  · [{p.if_text}] {p.journal[:30]:30s} {p.title[:50]}")

downloader = Downloader(tmp / "downloads", workers=3)
summary = downloader.download_all(
    with_pdf,
    progress=lambda d, t, task: print(f"   [{d}/{t}] {task.status:8s} {task.message}"),
)
print(f"\n下载目录：{summary.directory}")
print(f"成功 {summary.ok_count}，跳过 {summary.skipped}，失败 {len(summary.failed)}，"
      f"共 {summary.total_bytes/1024:.0f} KB")

files = sorted((tmp / "downloads").glob("*.pdf"))
print(f"磁盘上的 PDF 文件：{len(files)}")
for f in files:
    head = f.read_bytes()[:5]
    print(f"  {f.name[:70]:70s} {f.stat().st_size/1024:7.0f} KB  header={head!r}")

ok = summary.ok_count >= 1 and all(f.read_bytes()[:5] == b"%PDF-" for f in files)
print("\n结果：", "端到端下载通过 ✅" if ok else "端到端下载失败 ❌")
sys.exit(0 if ok else 1)
