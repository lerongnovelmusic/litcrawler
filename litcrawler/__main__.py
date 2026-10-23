# -*- coding: utf-8 -*-
"""``python -m litcrawler`` 入口：无参数时启动图形界面。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
