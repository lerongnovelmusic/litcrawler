# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：生成单文件 LitCrawler.exe。

用法：
    python -m pip install pyinstaller
    python -m PyInstaller packaging/LitCrawler.spec --noconfirm
产物：dist/LitCrawler.exe（约 15 MB，无需安装 Python）
"""

from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH 由 PyInstaller 注入)

a = Analysis(
    [str(ROOT / "run_litcrawler.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "litcrawler" / "resources"), "litcrawler/resources")],
    hiddenimports=["litcrawler.gui.app", "openpyxl"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["numpy", "pandas", "matplotlib", "PIL", "lxml", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LitCrawler",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 不显示控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
