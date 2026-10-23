# -*- coding: utf-8 -*-
"""轻量日志：同时输出到控制台（若有）和 ``数据目录/logs/litcrawler.log``。

日志按天滚动保留最近 5 个文件，方便用户反馈问题时提供线索。
"""

from __future__ import annotations

import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

_configured = False


class _SafeStreamHandler(logging.StreamHandler):
    """在无控制台（pythonw / exe）环境下自动失效，不抛异常。"""

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover
        try:
            super().emit(record)
        except Exception:
            self.stream = None


def setup(log_dir: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    """初始化根日志器（重复调用是安全的）。"""
    global _configured
    logger = logging.getLogger("litcrawler")
    if _configured:
        return logger

    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")

    stream_ok = sys.stderr is not None and getattr(sys.stderr, "write", None) is not None
    if stream_ok:
        try:
            sh = _SafeStreamHandler(sys.stderr)
            sh.setFormatter(fmt)
            logger.addHandler(sh)
        except Exception:
            pass

    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(
                log_dir / "litcrawler.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except Exception:
            pass

    _configured = True
    logger.debug("日志初始化于 %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    return logger


def get_logger(name: str = "litcrawler") -> logging.Logger:
    return logging.getLogger(name if name.startswith("litcrawler") else f"litcrawler.{name}")


def install_excepthook(logger: logging.Logger) -> None:
    """把未捕获异常写入日志，便于排查。"""

    def hook(exc_type, exc_value, exc_tb):  # pragma: no cover
        if issubclass(exc_type, KeyboardInterrupt):
            return
        logger.error("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = hook
