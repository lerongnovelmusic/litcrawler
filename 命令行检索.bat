@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   LitCrawler 命令行模式
echo ============================================
echo.
set /p KW=请输入检索关键词（支持模糊，例如 deep learning medical imaging）: 
set /p IF=请输入最低影响因子（直接回车=不限）: 
if "%IF%"=="" set IF=0
echo.
python -m litcrawler search "%KW%" -s "OpenAlex,Crossref,Europe PMC" --min-if %IF% -n 20
echo.
pause
