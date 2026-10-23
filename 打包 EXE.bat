@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo   构建 LitCrawler 单文件 EXE
echo ============================================================
echo.

set "PY=python"
where python >nul 2>nul || (
    echo [错误] 未找到 Python，请先安装 Python 3.9+ 并加入 PATH。
    pause
    exit /b 1
)

echo [1/3] 检查 PyInstaller…
%PY% -c "import PyInstaller" 2>nul || (
    echo       未安装，正在安装…
    %PY% -m pip install pyinstaller || (echo [错误] 安装失败 & pause & exit /b 1)
)

echo [2/3] 清理旧产物…
echo       结束可能正在运行的旧版本…
taskkill /f /im LitCrawler.exe >nul 2>nul
timeout /t 1 /nobreak >nul
if exist dist\LitCrawler.exe attrib -r -h dist\LitCrawler.exe >nul 2>nul
if exist build rmdir /s /q build >nul 2>nul
if exist dist rmdir /s /q dist >nul 2>nul
if exist dist (
    echo [错误] 无法删除 dist 目录，请先关闭正在运行的 LitCrawler.exe 后重试。
    pause
    exit /b 1
)

echo [3/3] 开始打包（约 1-2 分钟）…
%PY% -m PyInstaller packaging\LitCrawler.spec --noconfirm --clean || (
    echo [错误] 打包失败，请查看上面的输出。
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   完成！可执行文件：dist\LitCrawler.exe
echo   直接双击该文件即可运行（无需安装 Python）。
echo   把 dist\LitCrawler.exe 复制到任何地方都能使用。
echo ============================================================
echo.
pause
