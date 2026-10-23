# -*- coding: utf-8 -*-
"""PyInstaller / 直接运行 的统一入口。

    python run_litcrawler.py            # 打开图形界面
    python run_litcrawler.py search ...  # 命令行模式（等同于 python -m litcrawler）
"""

import multiprocessing
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from litcrawler.cli import main  # noqa: E402

if __name__ == "__main__":
    multiprocessing.freeze_support()  # Windows 打包后必须
    sys.exit(main())
