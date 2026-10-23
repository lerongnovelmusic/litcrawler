# -*- coding: utf-8 -*-
"""数据源包：内置学术数据源 + 自定义站点。"""

from __future__ import annotations

from .adapters.arxiv import ArxivSource
from .adapters.crossref import CrossrefSource
from .adapters.doaj import DoajSource
from .adapters.europepmc import EuropePmcSource
from .adapters.openalex import OpenAlexSource
from .adapters.pubmed import PubMedSource
from .base import Source, SourceError, SourceMeta, SourceRegistry

__all__ = [
    "Source",
    "SourceError",
    "SourceMeta",
    "SourceRegistry",
    "ArxivSource",
    "CrossrefSource",
    "DoajSource",
    "EuropePmcSource",
    "OpenAlexSource",
    "PubMedSource",
    "build_registry",
]


def build_registry() -> SourceRegistry:
    """构建内置数据源注册表。"""
    registry = SourceRegistry()
    registry.register("OpenAlex", OpenAlexSource)
    registry.register("Crossref", CrossrefSource)
    registry.register("Europe PMC", EuropePmcSource)
    registry.register("arXiv", ArxivSource)
    registry.register("DOAJ", DoajSource)
    registry.register("PubMed", PubMedSource)
    return registry
