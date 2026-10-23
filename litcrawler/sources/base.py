# -*- coding: utf-8 -*-
"""数据源基类与注册表。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable

from ..core.models import Paper


@dataclass
class SourceMeta:
    """数据源的展示信息。"""

    name: str
    label: str
    homepage: str
    description: str
    needs_key: bool = False
    default_on: bool = True


class SourceError(Exception):
    """数据源检索失败。"""


class Source:
    """所有数据源适配器的基类。"""

    meta: SourceMeta
    #: 相邻两次请求的最小间隔（秒），用于自我限速
    min_interval: float = 0.0
    _last_call: float = 0.0

    def __init__(self, *, mailto: str = "", api_key: str = ""):
        self.mailto = mailto
        self.api_key = api_key

    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return self.meta.name

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        wait = self.min_interval - (time.time() - self.__class__._last_call)
        if wait > 0:
            time.sleep(wait)
        self.__class__._last_call = time.time()

    # ------------------------------------------------------------------ #
    def search(self, query: str, limit: int) -> list[Paper]:
        """按关键词检索，返回统一格式的文献记录。"""
        raise NotImplementedError

    def probe(self) -> str:
        """连通性自检，返回可读结果。"""
        papers = self.search("machine learning", 1)
        return f"正常（返回 {len(papers)} 条示例）"

    # ------------------------------------------------------------------ #
    @staticmethod
    def clean_doi(doi: str) -> str:
        if not doi:
            return ""
        doi = str(doi).strip()
        for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:", "DOI:"):
            if doi.lower().startswith(prefix.lower()):
                doi = doi[len(prefix) :]
        return doi.strip()


# ---------------------------------------------------------------------- #
# 注册表
# ---------------------------------------------------------------------- #
class SourceRegistry:
    """内置数据源注册表。"""

    def __init__(self):
        self._factories: dict[str, Callable[..., Source]] = {}
        self._order: list[str] = []

    def register(self, key: str, factory: Callable[..., Source]) -> None:
        if key not in self._factories:
            self._order.append(key)
        self._factories[key] = factory

    def create(self, key: str, **kwargs) -> Source:
        if key not in self._factories:
            raise KeyError(f"未知数据源：{key}")
        return self._factories[key](**kwargs)

    def names(self) -> list[str]:
        return list(self._order)

    def metas(self) -> list[SourceMeta]:
        out = []
        for key in self._order:
            try:
                out.append(self.create(key).meta)
            except Exception:
                continue
        return out

    def build(self, selected: Iterable[str], **kwargs) -> list[tuple[str, Source | None, str]]:
        """按用户选择构建数据源实例。

        返回 ``[(key, source_or_None, error)]``。
        """
        result: list[tuple[str, Source | None, str]] = []
        for key in selected:
            if key not in self._factories:
                result.append((key, None, "未安装该数据源"))
                continue
            try:
                result.append((key, self.create(key, **kwargs), ""))
            except Exception as exc:
                result.append((key, None, str(exc)))
        return result
