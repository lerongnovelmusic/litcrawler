# -*- coding: utf-8 -*-
"""模糊匹配工具：关键词相似度、期刊名归一化、ISSN 归一化。

只使用标准库 difflib，保证零依赖；对中英文混排做了兼容。
"""

from __future__ import annotations

import difflib
import re
import unicodedata

_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_CJK_RE = re.compile(r"([\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af])")
_WS_RE = re.compile(r"\s+")
_ISSN_RE = re.compile(r"(\d{4})\s*-?\s*(\d{3}[\dxX])")

# 期刊名里常见的、可忽略的缩略/等价写法（单 token 直接查表）
_ABBREV = {
    "j": "journal", "int": "international", "sci": "science", "med": "medicine",
    "biol": "biology", "chem": "chemistry", "phys": "physics", "comput": "computing",
    "eng": "engineering", "technol": "technology", "rev": "review", "res": "research",
    "natl": "national", "acad": "academy", "univ": "university", "proc": "proceedings",
    "appl": "applied", "math": "mathematics", "stat": "statistics", "mol": "molecular",
    "clin": "clinical", "exp": "experimental", "gen": "general",
    "am": "american", "eur": "european", "assoc": "association", "soc": "society",
    "dev": "development", "env": "environmental", "environ": "environmental",
    "educ": "education", "psychol": "psychology", "pharmacol": "pharmacology",
    "biomed": "biomedical", "bioeng": "bioengineering", "neuro": "neuroscience",
    "immunol": "immunology", "microbiol": "microbiology",
    "genet": "genetics", "physiol": "physiology", "nutr": "nutrition",
    "manag": "management", "econ": "economics", "bus": "business",
    "ind": "industrial", "mater": "materials",
    "sustain": "sustainable", "agri": "agriculture",
    "trans": "transactions", "commun": "communications", "lett": "letters",
    "oncol": "oncology", "cardiol": "cardiology", "neurol": "neurology",
    "radi": "radiology", "rep": "reports", "re": "reports",
    "anal": "analytical",
    "geophys": "geophysics", "astron": "astronomy", "astrophys": "astrophysics",
    "biochem": "biochemistry", "biophys": "biophysics", "biotechnol": "biotechnology",
    "cardiovasc": "cardiovascular", "gastroenterol": "gastroenterology",
    "hepatol": "hepatology", "nephrol": "nephrology", "hematol": "hematology",
    "endocrinol": "endocrinology", "epidemiol": "epidemiology",
    "infect": "infectious", "dis": "diseases", "surg": "surgery", "pediatr": "pediatrics",
    "obstet": "obstetrics", "gynecol": "gynecology", "psychiatry": "psychiatry",
    "dent": "dentistry", "orthop": "orthopaedics", "ophthalmol": "ophthalmology",
    "otolaryngol": "otolaryngology", "urol": "urology", "dermatol": "dermatology",
    "anesthesiol": "anesthesiology", "pathol": "pathology", "toxicol": "toxicology",
    "veter": "veterinary", "zool": "zoology", "bot": "botany", "ecol": "ecology",
    "evolut": "evolution", "meteorol": "meteorology", "oceanogr": "oceanography",
    "nano": "nanotechnology", "electron": "electronics", "telecommun": "telecommunications",
    "inform": "informatics", "informationssysteme": "information systems",
    "q": "quarterly", "bull": "bulletin", "ann": "annals", "arch": "archives",
}

# 期刊名中不参与区分的常见噪声词
_JOURNAL_NOISE = {
    "the", "of", "and", "for", "in", "on", "a", "an", "to", "with",
    "journal", "journals", "international", "int", "research", "review",
    "reviews", "letters", "proceedings", "transactions", "acta", "annals",
    "archive", "archives", "bulletin", "frontiers", "advances", "open",
    "new", "current", "science", "sciences", "studies", "series", "part",
}


def strip_accents(text: str) -> str:
    """去掉音标符号（café -> cafe）。"""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize(text: str, *, keep_cjk_spacing: bool = False) -> str:
    """归一化文本：小写、去音标、标点转空格、压缩空白。"""
    if not text:
        return ""
    text = strip_accents(str(text)).lower()
    text = text.replace("&", " and ")
    text = _PUNCT_RE.sub(" ", text)
    if keep_cjk_spacing:
        text = _CJK_RE.sub(r" \1 ", text)
    return _WS_RE.sub(" ", text).strip()


def tokens(text: str) -> list[str]:
    """切词：英文按空白，中文按字。"""
    norm = normalize(text, keep_cjk_spacing=True)
    return [t for t in norm.split(" ") if t]


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _partial_ratio(needle: str, haystack: str) -> float:
    """needle 在 haystack 中的最佳局部相似度（用于处理部分输入）。"""
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    n = len(needle)
    if len(haystack) <= n:
        return _ratio(needle, haystack)
    best = 0.0
    window = max(n, 4)
    for i in range(0, len(haystack) - window + 1, max(1, window // 2)):
        best = max(best, _ratio(needle, haystack[i : i + window]))
    best = max(best, _ratio(needle, haystack[-window:]))
    return best


def similarity(query: str, target: str) -> float:
    """0~1 的相似度：整串相似度与「查询词覆盖率」的加权组合。

    这样既支持拼写错误（"neural netwrok" 命中 "neural network"），
    也支持部分关键词（"medical image segmentation" 命中更长的标题）。
    """
    q = normalize(query)
    t = normalize(target)
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0

    whole = _ratio(q, t)
    partial = _partial_ratio(q, t)

    q_tokens = [tok for tok in q.split(" ") if tok]
    t_tokens = set(tok for tok in t.split(" ") if tok)
    if q_tokens:
        hits = 0.0
        for qt in q_tokens:
            if qt in t_tokens:
                hits += 1.0
                continue
            # 允许单复数 / 词形差异
            if qt.rstrip("s") in {tt.rstrip("s") for tt in t_tokens}:
                hits += 0.95
                continue
            best = 0.0
            for tt in t_tokens:
                if abs(len(tt) - len(qt)) > max(3, len(qt) * 0.5):
                    continue
                best = max(best, _ratio(qt, tt))
                if best >= 0.99:
                    break
            hits += best if best >= 0.75 else 0.0
        coverage = hits / len(q_tokens)
    else:
        coverage = 0.0

    return round(max(whole, partial * 0.85, coverage * 0.95), 4)


def best_similarity(query: str, targets) -> float:
    """对多个候选目标取最高相似度。"""
    best = 0.0
    for t in targets:
        if not t:
            continue
        best = max(best, similarity(query, t))
        if best >= 1.0:
            break
    return best


def normalize_issn(value: str) -> str:
    """归一化 ISSN：``1234-5678`` / ``12345678`` -> ``12345678``。"""
    if not value:
        return ""
    m = _ISSN_RE.search(str(value))
    if m:
        return (m.group(1) + m.group(2)).lower()
    digits = re.sub(r"[^0-9xX]", "", str(value))
    return digits.lower() if len(digits) == 8 else ""


# 词形归一：把同一词的常见变体收敛到一个规范形式
_CANON = {
    "chemical": "chemistry", "medicinal": "medicine", "medical": "medicine",
    "biological": "biology", "physical": "physics", "mathematical": "mathematics",
    "statistical": "statistics", "psychological": "psychology",
    "pharmacological": "pharmacology", "clinical": "clinic",
    "biomedical": "biomedicine", "molecular": "molecule",
    "environment": "environmental", "microbiological": "microbiology",
    "genetical": "genetics", "physiological": "physiology",
    "nutritional": "nutrition", "industrial": "industry",
    "economical": "economics", "sustainable": "sustainability",
    "paediatric": "pediatric", "orthopaedic": "orthopedic",
    "anaesthesia": "anesthesia", "haematology": "hematology",
    "scientific": "science",
}


def _canon(word: str) -> str:
    return _CANON.get(word, word)


def _expand_abbrev(text: str) -> str:
    """按 token 展开期刊名中常见的缩写（J -> journal、Trans -> transactions…）。"""
    return " ".join(_ABBREV.get(word, word) for word in text.split(" ") if word)


def _words(name: str) -> list[str]:
    """期刊名归一化后的词序列（展开缩写 → 词形归一 → 去标点 → 去噪声词）。"""
    if not name:
        return []
    expanded = [_canon(w) for w in _expand_abbrev(normalize(name)).split(" ")]
    words = [w for w in _PUNCT_RE.sub(" ", " ".join(expanded)).split(" ") if w]
    if not words:
        return []
    meaningful = [w for w in words if w not in _JOURNAL_NOISE]
    return meaningful or words


def normalize_journal(name: str) -> str:
    """期刊名归一化：展开常见缩写、去标点、去噪声词。"""
    return " ".join(_words(name))


def journal_key(name: str) -> str:
    """期刊名的强归一化键，用于精确查表。"""
    return "".join(_words(name))


def _is_abbrev_of(short: str, long: str) -> bool:
    """判断 ``short`` 是否是 ``long`` 的合理缩写形式。

    例：chem ≈ chemistry、reports ≈ rep、image ≈ imaging。
    为避免误判，只允许非常短的词（≤3 字符）做前缀匹配。
    """
    if not short or not long or short == long:
        return False
    a = short.rstrip(".").lower()
    b = long.rstrip(".").lower()
    if a in b or b in a:
        limit = 4 if len(a) >= 4 else 3
        return len(a) >= limit
    # 去常见英文词尾后再比较（chemistry→chemistr、production→product、imaging→imag）
    for suffix in ("ing", "ed", "es", "s", "y", "e", "ical", "ics"):
        if b.endswith(suffix) and len(b) - len(suffix) >= len(a):
            b2 = b[: -len(suffix)]
            if a == b2 or (len(a) >= 4 and b2.startswith(a)):
                return True
    return False


def _token_similarity(x: str, y: str) -> float:
    """期刊名单词相似度（含缩写容忍，严格前缀式）。

    只在较短词是较长词的前缀时才给分，避免 "rep" 与 "scientific" 这类仅共享
    个别字母的误配；"reports"/"rep"、"imaging"/"image" 仍可命中。
    """
    if x == y:
        return 1.0
    shorter, longer = (x, y) if len(x) <= len(y) else (y, x)
    if not longer.startswith(shorter):
        return 0.0
    if _is_abbrev_of(shorter, longer):
        return 0.9
    score = _ratio(x, y)
    base = len(longer) - len(shorter)
    return min(1.0, score * (1.0 - 0.3 * base / max(1, len(longer))))


def _expanded_words(name: str) -> list[str]:
    """展开缩写并做词形归一后的词序列（保留 journal/of 等词），用于词数对比。"""
    if not name:
        return []
    expanded = [_canon(w) for w in _expand_abbrev(normalize(name)).split(" ")]
    return [w for w in _PUNCT_RE.sub(" ", " ".join(expanded)).split(" ") if w]


def _ordered_align(short: list[str], long_: list[str]) -> float:
    """按顺序对齐两组词（允许缩写/词形差异），返回 ``得分 / 长名词数``。"""
    used: set[int] = set()
    total = 0.0
    for token in short:
        best, best_idx = 0.0, -1
        for i, other in enumerate(long_):
            if i in used:
                continue
            score = _token_similarity(token, other)
            if score > best:
                best, best_idx = score, i
        if best_idx >= 0 and best > 0:
            used.add(best_idx)
            total += best
    return total / len(long_) if long_ else 0.0


def journal_similarity(a: str, b: str) -> float:
    """期刊名相似度（0~1）。

    综合三个对称信号，专门针对期刊名调参：

    * **token 覆盖**：短名词被长名词覆盖的比例（缩写按 0.9 计），
      使 "Nature" 与 "Nature Communications" 只得到约 0.5；
    * **顺序对齐**：按出现顺序逐词匹配，支持 "J Am Chem Soc" → 全称；
    * **前缀对齐**：允许部分匹配，捕捉词序不同与拼写差异。
    """
    ka, kb = journal_key(a), journal_key(b)
    if ka and kb and ka == kb:
        return 1.0
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    coverage = sum(max((_token_similarity(t, o) for o in long_), default=0.0) for t in short) / len(long_)
    ordered = _ordered_align(short, long_)
    # 以“展开缩写后的实际词数”为前缀对齐的分母，避免 "Journal of ..." 被去词后失真
    ea, eb = _expanded_words(a), _expanded_words(b)
    eshort, elong = (ea, eb) if len(ea) <= len(eb) else (eb, ea)
    prefix = _ordered_align(eshort, elong)
    return min(1.0, round(0.5 * coverage + 0.25 * ordered + 0.25 * prefix, 4))


def matches_journal(
    journal: str,
    issns: list[str],
    wanted: list[str],
    *,
    threshold: float = 0.62,
    fuzzy: bool = True,
) -> tuple[bool, str]:
    """判断期刊是否命中用户指定的期刊清单。

    返回 ``(是否命中, 命中的清单项)``。支持 ISSN 精确匹配、名称包含与模糊匹配。
    """
    if not wanted:
        return True, ""
    if journal:
        jkey = journal_key(journal)
        jnorm = normalize_journal(journal)
        for want in wanted:
            if not want.strip():
                continue
            if jkey and jkey == journal_key(want):
                return True, want
            want_norm = normalize_journal(want)
            # 多词期刊的整词包含（如 "nature communications" ⊂ "nature communications biology"）；
            # 单词期刊名不走这条路，避免 "Nature" 误命中 "Nature Communications"
            if want_norm and want_norm != jnorm:
                shorter, longer = (want_norm, jnorm) if len(want_norm) <= len(jnorm) else (jnorm, want_norm)
                if len(shorter.split(" ")) >= 2:
                    word_re = r"(?<![a-z0-9])" + r"\s+".join(re.escape(w) for w in shorter.split(" ")) + r"(?![a-z0-9])"
                    if re.search(word_re, longer):
                        return True, want
    for want in wanted:
        w = normalize_issn(want)
        if w and w in {normalize_issn(i) for i in issns if i}:
            return True, want
    if fuzzy and journal:
        best, best_want = 0.0, ""
        for want in wanted:
            if not want.strip():
                continue
            score = journal_similarity(want, journal)
            if score > best:
                best, best_want = score, want
        if best >= threshold:
            return True, best_want
    return False, ""
