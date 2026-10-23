# -*- coding: utf-8 -*-
"""命令行入口：不打开界面也能检索 / 下载 / 管理影响因子库。

示例：
    python -m litcrawler search "deep learning medical imaging" --min-if 5 -n 20
    python -m litcrawler download "graph neural network" --min-if 3 -n 5 -o D:\\papers
    python -m litcrawler if-import "C:\\path\\jcr.csv"
    python -m litcrawler if-status
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __app_name__, __version__
from .core.config import Config
from .core.downloader import Downloader
from .core.impactfactor import ImpactFactorDB, bundle_path
from .core.search import SearchEngine, SearchQuery


def _engine(config: Config) -> SearchEngine:
    db = ImpactFactorDB(bundle_path(), config.if_cache_path, config.if_import_dir)
    db.load_bundle()
    db.load_cache()
    return SearchEngine(db)


def _make_query(args) -> SearchQuery:
    return SearchQuery(
        keyword=args.keyword,
        sources=[s.strip() for s in args.sources.split(",") if s.strip()],
        journals=[j.strip() for j in args.journals.split(",") if j.strip()] if args.journals else [],
        min_impact_factor=args.min_if,
        max_impact_factor=args.max_if,
        year_from=args.year_from,
        year_to=args.year_to,
        limit=args.number,
        fuzzy_keyword=not args.exact,
        fuzzy_journal=not args.exact,
        only_open_access=args.oa_only,
    )


def cmd_search(args) -> int:
    config = Config()
    engine = _engine(config)
    query = _make_query(args)
    result = engine.search(query, mailto=args.mailto, progress=lambda m: print(m, flush=True))
    print()
    if not result.papers:
        print("没有命中结果。")
        return 1
    header = f"{'#':>3}  {'年份':<5}{'IF':<8}{'期刊':<34}标题"
    print(header)
    print("-" * 118)
    for idx, paper in enumerate(result.papers, start=1):
        print(f"{idx:>3}  {str(paper.year or '-'):<5}{paper.if_text:<8}{(paper.journal or '-')[:32]:<34}{paper.title[:64]}")
    print("-" * 118)
    print(f"共 {len(result.papers)} 条；其中带 PDF 直链 {sum(1 for p in result.papers if p.has_pdf)} 条")
    if args.save_csv:
        from .gui.app import _write_results  # 复用导出逻辑

        target = Path(args.save_csv)
        _write_results(target, result.papers, "csv" if target.suffix != ".xlsx" else "xlsx")
        print(f"结果已保存：{target}")
    if args.download:
        return _download(result.papers, args)
    return 0


def _download(papers, args) -> int:
    targets = [p for p in papers if p.has_pdf]
    if not targets:
        print("没有带开放获取 PDF 直链的文献。")
        return 1
    directory = Path(args.output or (Path.home() / "Documents" / "LitCrawler下载"))
    downloader = Downloader(directory, workers=args.workers)
    print(f"开始下载 {len(targets)} 篇到 {directory}")
    summary = downloader.download_all(
        targets,
        progress=lambda done, total, task: print(f"  [{done}/{total}] {task.status:<8}{task.paper.title[:52]} — {task.message}"),
    )
    print(f"完成：成功 {summary.ok_count}，跳过 {summary.skipped}，失败 {len(summary.failed)}，"
          f"{summary.total_bytes/1024/1024:.2f} MB")
    return 0 if not summary.failed else 2


def cmd_download(args) -> int:
    args.download = True
    args.save_csv = getattr(args, "save_csv", None)
    return cmd_search(args)


def cmd_if_import(args) -> int:
    config = Config()
    db = ImpactFactorDB(bundle_path(), config.if_cache_path, config.if_import_dir)
    db.load_bundle()
    total = 0
    for path in args.paths:
        try:
            count = db.import_file(path)
            print(f"导入 {path}：{count} 条")
            total += count
        except Exception as exc:
            print(f"导入 {path} 失败：{exc}", file=sys.stderr)
    print(f"合计导入 {total} 条。")
    return 0


def cmd_if_status(args) -> int:
    config = Config()
    db = ImpactFactorDB(bundle_path(), config.if_cache_path, config.if_import_dir)
    db.load_bundle()
    db.load_cache()
    stats = db.stats
    print(f"记录数：{stats['rows']}（内置 {stats['bundle_rows']}）")
    print(f"覆盖年份：{'、'.join(stats['years'])}")
    print(f"已导入：{stats['imported'] or '（无）'}")
    for name in args.check or []:
        rec = db.lookup(name, [])
        print(f"  {name} -> {rec.label if rec else '未收录'}")
    return 0


def cmd_probe(args) -> int:
    config = Config()
    engine = _engine(config)
    names = [s.strip() for s in args.sources.split(",") if s.strip()]
    for name, text in engine.probe_sources(names, mailto=args.mailto).items():
        print(f"{name}: {text}")
    return 0


def cmd_gui(args) -> int:
    from .gui import main

    return main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="litcrawler", description=f"{__app_name__} 文献检索与下载工具")
    parser.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    sub = parser.add_subparsers(dest="command")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("keyword", help="检索关键词（支持模糊）")
    common.add_argument("-s", "--sources", default="OpenAlex,Crossref,Europe PMC",
                        help="数据源，逗号分隔（OpenAlex/Crossref/Europe PMC/arXiv/DOAJ/PubMed）")
    common.add_argument("-j", "--journals", default="", help="限定期刊，逗号分隔")
    common.add_argument("--min-if", type=float, default=0.0, help="最低影响因子")
    common.add_argument("--max-if", type=float, default=0.0, help="最高影响因子（0=不限）")
    common.add_argument("--year-from", type=int, default=0)
    common.add_argument("--year-to", type=int, default=0)
    common.add_argument("-n", "--number", type=int, default=20, help="结果条数上限")
    common.add_argument("--oa-only", action="store_true", help="只要开放获取")
    common.add_argument("--exact", action="store_true", help="关闭模糊匹配")
    common.add_argument("--mailto", default="", help="联系邮箱（礼貌池）")
    common.add_argument("--save-csv", default="", help="把结果保存为 CSV/XLSX")

    p_search = sub.add_parser("search", parents=[common], help="检索文献")
    p_search.set_defaults(func=cmd_search, download=False)

    p_dl = sub.add_parser("download", parents=[common], help="检索并下载开放获取 PDF")
    p_dl.add_argument("-o", "--output", default="", help="下载目录")
    p_dl.add_argument("--workers", type=int, default=4, help="并发下载数")
    p_dl.set_defaults(func=cmd_download, download=True, save_csv="")

    p_if = sub.add_parser("if-import", help="导入影响因子数据")
    p_if.add_argument("paths", nargs="+")
    p_if.set_defaults(func=cmd_if_import)

    p_status = sub.add_parser("if-status", help="查看影响因子库状态")
    p_status.add_argument("--check", nargs="*", default=[], help="查询指定期刊的影响因子")
    p_status.set_defaults(func=cmd_if_status)

    p_probe = sub.add_parser("probe", help="数据源连通性自检")
    p_probe.add_argument("-s", "--sources", default="OpenAlex,Crossref,Europe PMC,arXiv,DOAJ")
    p_probe.add_argument("--mailto", default="")
    p_probe.set_defaults(func=cmd_probe)

    p_gui = sub.add_parser("gui", help="打开图形界面")
    p_gui.set_defaults(func=cmd_gui)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        return cmd_gui(args)
    return args.func(args)
