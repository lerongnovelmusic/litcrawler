# -*- coding: utf-8 -*-
"""把下载的 JCR 原始 CSV 压缩成本程序内置的精简影响因子库。

用法：
    python tools/build_if_bundle.py <原始CSV> <输出目录>

输出：``litcrawler/resources/impact_factor_jcr.tsv.gz``
列：year / name / issn_p / issn_e / jif / jif_q / category / edition
"""

from __future__ import annotations

import csv
import gzip
import sys
from pathlib import Path

MIN_YEAR = 2022


def build(src: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "impact_factor_jcr.tsv.gz"

    seen: set[tuple[str, str, str, str]] = set()
    kept = 0
    total = 0
    with src.open("r", encoding="utf-8", newline="") as fh, gzip.open(
        target, "wt", encoding="utf-8", newline="\n"
    ) as out:
        reader = csv.DictReader(fh)
        writer = csv.writer(out, delimiter="\t", lineterminator="\n")
        writer.writerow(["year", "name", "issn_p", "issn_e", "jif", "jif_q", "category", "edition"])
        for row in reader:
            total += 1
            year = (row.get("Year") or "").strip()
            if not year.isdigit() or int(year) < MIN_YEAR:
                continue
            jif = (row.get("JIF") or "").strip()
            if not jif:
                continue
            name = (row.get("Name") or "").strip()
            if not name:
                continue
            key = (year, name.lower(), (row.get("ISSN_P") or "").strip(), (row.get("ISSN_E") or "").strip())
            if key in seen:
                continue
            seen.add(key)
            writer.writerow(
                [
                    year,
                    name,
                    (row.get("ISSN_P") or "").strip(),
                    (row.get("ISSN_E") or "").strip(),
                    jif,
                    (row.get("JIF_Q") or "").strip(),
                    (row.get("Category") or "").strip()[:120],
                    (row.get("Edition") or "").strip()[:40],
                ]
            )
            kept += 1
    print(f"原始行数 {total}，保留 {kept} 行 -> {target} ({target.stat().st_size/1024:.0f} KB)")
    return target


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    build(Path(sys.argv[1]), Path(sys.argv[2]))
