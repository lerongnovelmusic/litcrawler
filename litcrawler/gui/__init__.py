# -*- coding: utf-8 -*-
"""GUI 包。"""

from .app import App

__all__ = ["App", "main"]


def main() -> int:
    """启动图形界面。"""
    app = App()
    app.mainloop()
    return 0
