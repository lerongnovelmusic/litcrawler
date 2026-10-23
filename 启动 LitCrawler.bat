@echo off
chcp 65001 >nul
title LitCrawler 文献猎手
cd /d "%~dp0"

set "PY="
if exist "%~dp0LitCrawler.exe" (
    start "" "%~dp0LitCrawler.exe"
    exit /b 0
)

rem 优先使用自带 / 已安装的 pythonw（无控制台窗口）
where pythonw >nul 2>nul && set "PY=pythonw"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (
    echo [错误] 没有找到 Python。请先安装 Python 3.9+ 并勾选 "Add to PATH"。
    echo         安装地址：https://www.python.org/downloads/
    pause
    exit /b 1
)

start "" %PY% -m litcrawler
exit /b 0
