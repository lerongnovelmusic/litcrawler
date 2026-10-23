# -*- coding: utf-8 -*-
"""影响因子（Journal Impact Factor）数据库。

数据来源（三条并行路径，互为补充）：

1. **内置精简库** — 随程序打包 JCR 影响因子（含分区与学科），离线可用；
2. **用户导入** — 支持 JCR 官方导出、LetPub 导出、自建 CSV/TSV/JSON，导入即生效；
3. **OpenAlex 补充** — 未收录期刊用 OpenAlex 的 2 年篇均被引（2yr mean citedness）估算。

内置数据来自 MIT 许可的公开数据集（lcpmgh/Journal，整理自公开 JCR 资料），
仅作检索筛选参考，如需正式指标请以 JCR 官方数据为准。
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import http
from .fuzzy import journal_key, normalize, normalize_issn

BUNDLE_FILE = "impact_factor_jcr.tsv.gz"
FULL_CSV_URL = "https://raw.githubusercontent.com/lcpmgh/Journal/master/journal_jcr_2025.csv"
FULL_CSV_LICENSE = "MIT © 2025 Liang Chen (lcpmgh/Journal)，数据整理自公开 JCR 资料"

BUNDLE_COLUMNS = ["year", "name", "issn_p", "issn_e", "jif", "jif_q", "category", "edition"]


def bundle_path() -> Path:
    """内置影响因子库的文件路径（打包后位于资源目录）。"""
    from .config import resource_path

    return resource_path("litcrawler", "resources", BUNDLE_FILE)


@dataclass
class ImpactFactor:
    """一条影响因子记录。"""

    name: str
    jif: float
    year: str = ""
    quartile: str = ""
    category: str = ""
    edition: str = ""
    issns: tuple[str, ...] = ()
    source: str = "内置库"

    @property
    def label(self) -> str:
        bits = [f"IF {self.jif:g}"]
        if self.quartile:
            bits.append(self.quartile)
        if self.year:
            bits.append(f"{self.year}")
        if self.source and self.source != "内置库":
            bits.append(self.source)
        return " · ".join(bits)


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "—", "N/A", "n/a", "NA", "null", "None"}:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


class ImpactFactorDB:
    """影响因子查询库（线程安全、延迟加载）。"""

    def __init__(self, bundle_path: Path, cache_path: Path, import_dir: Path):
        self.bundle_path = bundle_path
        self.cache_path = cache_path
        self.import_dir = import_dir
        self._lock = threading.RLock()
        self._by_issn: dict[tuple[str, str], ImpactFactor] = {}
        self._by_name: dict[tuple[str, str], ImpactFactor] = {}
        # 名称键 -> 记录（用于模糊兜底）
        self._name_bucket: dict[str, list[ImpactFactor]] = {}
        self._loaded_bundle = False
        self._loaded_imports = False
        self._imported_files: list[str] = []
        self._stats = {"rows": 0, "years": set(), "bundle_rows": 0}

    # ------------------------------------------------------------------ #
    # 加载
    # ------------------------------------------------------------------ #
    def load_bundle(self) -> int:
        with self._lock:
            if self._loaded_bundle:
                return self._stats["bundle_rows"]
            self._loaded_bundle = True
            if not self.bundle_path.exists():
                return 0
            count = 0
            try:
                with gzip.open(self.bundle_path, "rt", encoding="utf-8", newline="") as fh:
                    reader = csv.DictReader(fh, delimiter="\t")
                    for row in reader:
                        if self._add_row(row, source="内置库"):
                            count += 1
            except Exception:
                pass
            self._stats["bundle_rows"] = count
            return count

    def load_imports(self, force: bool = False) -> list[str]:
        """扫描导入目录中的用户数据文件。"""
        with self._lock:
            if self._loaded_imports and not force:
                return list(self._imported_files)
            self._loaded_imports = True
            files: list[str] = []
            if self.import_dir.exists():
                for path in sorted(self.import_dir.iterdir()):
                    if path.suffix.lower() not in {".csv", ".tsv", ".txt", ".json", ".xlsx"}:
                        continue
                    try:
                        n = self.import_file(path, save_copy=False)
                        files.append(f"{path.name}（{n} 条）")
                    except Exception as exc:  # 单个文件失败不影响其它
                        files.append(f"{path.name}（导入失败：{exc}）")
            self._imported_files = files
            return list(files)

    def ensure_loaded(self) -> None:
        with self._lock:
            if not self._loaded_bundle:
                self.load_bundle()
        if not self._loaded_imports:
            self.load_imports()

    # ------------------------------------------------------------------ #
    # 导入
    # ------------------------------------------------------------------ #
    def import_file(self, path: str | Path, *, save_copy: bool = True) -> int:
        """导入 CSV / TSV / JSON / XLSX 影响因子数据，返回导入条数。"""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        suffix = path.suffix.lower()
        rows: list[dict] = []
        source = f"导入:{path.name}"
        if suffix == ".json":
            data = json.loads(path.read_text("utf-8"))
            if isinstance(data, dict):
                data = data.get("journals") or data.get("data") or list(data.values())
            rows = [r for r in data if isinstance(r, dict)]
            fieldnames = list(rows[0].keys()) if rows else []
        elif suffix == ".xlsx":
            rows, fieldnames = self._read_xlsx(path)
        else:
            text = self._read_text(path)
            sample = text[:8192]
            delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
            reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
            fieldnames = reader.fieldnames or []
            rows = list(reader)

        mapping = self._detect_columns(fieldnames, rows[:5])
        if "name" not in mapping and "issn" not in mapping:
            raise ValueError("未识别的列名（需要期刊名称或 ISSN 列）")

        count = 0
        with self._lock:
            for row in rows:
                rec = self._record_from_mapping(row, mapping, source)
                if rec and self._add(rec):
                    count += 1
            copies = []
            if save_copy and path.parent.resolve() != self.import_dir.resolve():
                self.import_dir.mkdir(parents=True, exist_ok=True)
                target = self.import_dir / path.name
                try:
                    target.write_bytes(path.read_bytes())
                    copies.append(target)
                except Exception:
                    pass
            self._imported_files.append(f"{path.name}（{count} 条）")
            self._loaded_imports = True
        self._save_cache()
        return count

    @staticmethod
    def _read_text(path: Path) -> str:
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")

    @staticmethod
    def _read_xlsx(path: Path) -> tuple[list[dict], list[str]]:
        try:
            import openpyxl  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("读取 Excel 需要 openpyxl，请改用 CSV 导出") from exc
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        header = [str(c).strip() if c is not None else "" for c in next(rows_iter)]
        rows = []
        for values in rows_iter:
            rows.append({header[i]: values[i] for i in range(min(len(header), len(values)))})
        wb.close()
        return rows, header

    @staticmethod
    def _detect_columns(fieldnames: Iterable[str], sample_rows: list[dict]) -> dict[str, str]:
        """把任意表头映射到内部字段。"""
        aliases = {
            "name": ["name", "journal", "journal name", "journal_name", "title", "full journal title",
                     "期刊", "期刊名称", "刊名", "杂志名称", "journal title", "so", "source title"],
            "issn": ["issn", "issn_p", "issn (print)", "print issn", "issn print", "issnl", "issn_l",
                     "issn_e", "eissn", "issn (online)", "online issn", "issn online"],
            "jif": ["jif", "jif_2024", "if", "impact factor", "impact_factor", "journal impact factor",
                    "2023 jif", "2024 jif", "影响因子", "最新影响因子", "if(2023)", "if值", "jcr"],
            "quartile": ["jif_q", "quartile", "jcr quartile", "q", "分区", "jcr分区", "jcr quartile",
                         "jif quartile", "wos quartile"],
            "category": ["category", "subject", "学科", "领域", "categories", "web of science categories"],
            "edition": ["edition", "index", "收录", "版本", "数据库"],
            "year": ["year", "jcr year", "年份", "年度", "jif_year", "metric year"],
        }
        norm_map = {str(f).strip().lower(): str(f) for f in fieldnames if f}
        mapping: dict[str, str] = {}
        for field, names in aliases.items():
            for cand in names:
                if cand in norm_map:
                    mapping[field] = norm_map[cand]
                    break
            if field in mapping:
                continue
            # 模糊匹配表头
            for key, original in norm_map.items():
                if any(cand in key for cand in names if len(cand) > 2):
                    mapping[field] = original
                    break
        # ISSN 可能在单独一列或多列中
        issn_cols = [orig for key, orig in norm_map.items() if "issn" in key]
        if issn_cols:
            mapping["issn"] = issn_cols[0]
            mapping["issn_extra"] = issn_cols  # type: ignore[assignment]
        return mapping

    def _record_from_mapping(self, row: dict, mapping: dict, source: str) -> ImpactFactor | None:
        def pick(field: str) -> str:
            col = mapping.get(field)
            if not col:
                return ""
            return str(row.get(col) or "").strip()

        name = pick("name")
        issns: list[str] = []
        for col in mapping.get("issn_extra", []) or [mapping.get("issn", "")]:
            if not col:
                continue
            norm = normalize_issn(str(row.get(col) or ""))
            if norm and norm not in issns:
                issns.append(norm)
        jif = _to_float(pick("jif"))
        if jif is None or (not name and not issns):
            return None
        if jif <= 0 or jif > 2000:
            return None
        return ImpactFactor(
            name=name or (issns[0] if issns else ""),
            jif=jif,
            year=pick("year") or self.default_year,
            quartile=pick("quartile").upper()[:8],
            category=pick("category")[:120],
            edition=pick("edition")[:40],
            issns=tuple(issns),
            source=source,
        )

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    @property
    def default_year(self) -> str:
        years = sorted(self._stats["years"])
        return years[-1] if years else ""

    @property
    def stats(self) -> dict:
        self.ensure_loaded()
        return {
            "rows": self._stats["rows"],
            "years": sorted(self._stats["years"]),
            "bundle_rows": self._stats["bundle_rows"],
            "imported": list(self._imported_files),
        }

    def available_years(self) -> list[str]:
        self.ensure_loaded()
        return sorted(self._stats["years"])

    def lookup(
        self,
        journal: str = "",
        issns: Iterable[str] = (),
        *,
        year: str | None = None,
        fuzzy: bool = True,
        fuzzy_threshold: float = 0.62,
    ) -> ImpactFactor | None:
        """按 ISSN / 期刊名查找影响因子，优先返回指定年份（默认最新）。"""
        self.ensure_loaded()
        years = [year] if year else []
        years += [y for y in sorted(self._stats["years"], reverse=True) if y not in years]

        issn_keys = [normalize_issn(i) for i in issns if i]
        issn_keys = [i for i in issn_keys if i]
        name_key = journal_key(journal) if journal else ""

        for y in years:
            for ik in issn_keys:
                rec = self._by_issn.get((ik, y))
                if rec:
                    return rec
            if name_key:
                rec = self._by_name.get((name_key, y))
                if rec:
                    return rec

        if fuzzy and journal:
            candidates = self._name_bucket.get(name_key, [])
            if not candidates:
                from difflib import SequenceMatcher

                best: tuple[float, ImpactFactor] | None = None
                for key, recs in self._name_bucket.items():
                    if abs(len(key) - len(name_key)) > max(6, len(name_key) * 0.4):
                        continue
                    score = SequenceMatcher(None, name_key, key).ratio()
                    if score >= fuzzy_threshold and (best is None or score > best[0]):
                        best = (score, recs[-1])
                return best[1] if best else None
            return candidates[-1]
        return None

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def _add_row(self, row: dict, source: str) -> bool:
        jif = _to_float(row.get("jif"))
        if jif is None or jif <= 0:
            return False
        issns = tuple(
            x for x in {normalize_issn(row.get("issn_p", "")), normalize_issn(row.get("issn_e", ""))} if x
        )
        rec = ImpactFactor(
            name=str(row.get("name") or "").strip(),
            jif=jif,
            year=str(row.get("year") or "").strip(),
            quartile=str(row.get("jif_q") or "").strip().upper()[:8],
            category=str(row.get("category") or "").strip()[:120],
            edition=str(row.get("edition") or "").strip()[:40],
            issns=issns,
            source=source,
        )
        return self._add(rec)

    def _add(self, rec: ImpactFactor) -> bool:
        year = rec.year or self.default_year or "latest"
        added = False
        for issn in rec.issns:
            self._by_issn[(issn, year)] = rec
            added = True
        if rec.name:
            key = journal_key(rec.name)
            if key:
                self._by_name[(key, year)] = rec
                self._name_bucket.setdefault(key, []).append(rec)
                added = True
        if added:
            self._stats["rows"] += 1
            self._stats["years"].add(year)
        return added

    # ------------------------------------------------------------------ #
    # 缓存（用户导入的数据）
    # ------------------------------------------------------------------ #
    def _save_cache(self) -> None:
        records = []
        seen = set()
        for rec in self._name_bucket.values():
            for r in rec:
                if r.source == "内置库":
                    continue
                ident = (r.name, r.jif, r.year, r.issns)
                if ident in seen:
                    continue
                seen.add(ident)
                records.append(
                    {
                        "name": r.name,
                        "jif": r.jif,
                        "year": r.year,
                        "quartile": r.quartile,
                        "category": r.category,
                        "edition": r.edition,
                        "issns": list(r.issns),
                        "source": r.source,
                    }
                )
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(records, ensure_ascii=False), "utf-8")
        except Exception:
            pass

    def load_cache(self) -> int:
        if not self.cache_path.exists():
            return 0
        try:
            records = json.loads(self.cache_path.read_text("utf-8"))
        except Exception:
            return 0
        count = 0
        with self._lock:
            for item in records if isinstance(records, list) else []:
                jif = _to_float(item.get("jif"))
                if jif is None:
                    continue
                rec = ImpactFactor(
                    name=str(item.get("name") or ""),
                    jif=jif,
                    year=str(item.get("year") or ""),
                    quartile=str(item.get("quartile") or ""),
                    category=str(item.get("category") or ""),
                    edition=str(item.get("edition") or ""),
                    issns=tuple(item.get("issns") or ()),
                    source=str(item.get("source") or "导入"),
                )
                if self._add(rec):
                    count += 1
            self._loaded_imports = True
        return count

    # ------------------------------------------------------------------ #
    # 完整数据集下载
    # ------------------------------------------------------------------ #
    def download_full_dataset(self, target: Path, progress=None) -> Path:
        """下载完整多年度 JCR 数据集（CSV）。

        下载后自动导入（只取最新年度指标以控制内存），并保存到导入目录。
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        if progress:
            progress("正在下载完整 JCR 数据集（约 40 MB）…")
        resp = http.request(FULL_CSV_URL, timeout=180.0, retries=2, browser_ua=True, max_bytes=None)
        if resp.status != 200 or not resp.content:
            raise RuntimeError(f"下载失败（HTTP {resp.status}）")
        target.write_bytes(resp.content)
        if progress:
            progress("下载完成，正在解析…")
        # 只导入最新年份，避免内存膨胀
        text = self._read_text(target)
        reader = csv.DictReader(io.StringIO(text))
        mapping = self._detect_columns(reader.fieldnames or [], [])
        latest = ""
        rows = list(reader)
        years = sorted({str(r.get(mapping.get("year", "Year")) or "").strip() for r in rows if r})
        latest = years[-1] if years else ""
        count = 0
        with self._lock:
            for row in rows:
                if latest and str(row.get(mapping.get("year", "Year")) or "").strip() != latest:
                    continue
                rec = self._record_from_mapping(row, mapping, f"完整库:{latest}")
                if rec and self._add(rec):
                    count += 1
        self._save_cache()
        if progress:
            progress(f"已载入 {count} 条（{latest} 年度）")
        return target
