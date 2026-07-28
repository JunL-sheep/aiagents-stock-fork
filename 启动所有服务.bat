@echo off
chcp 65001 >nul
echo ========================================
echo   智瞰龙虎 启动器
echo   顺序: tdx-api (后端行情) → app (前端 UI)
echo ========================================
echo.

REM ---------- 1. 检查 tdx-api 是否已在跑 ----------
echo [1/4] 检查 tdx-api (端口 9999) ...
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 9999 -State Listen -ErrorAction SilentlyContinue; if ($c) { Write-Host '   ✓ 已在运行，跳过' -ForegroundColor Green; exit 0 } else { exit 1 }"
if %ERRORLEVEL% EQU 0 goto :app_start

echo    未运行，正在启动 tdx-api ...

REM ---------- 2. 启动 tdx-api (新窗口，后台) ----------
echo [2/4] 启动 tdx-api (新窗口) ...
start "tdx-api" /min cmd /c "cd /d D:\projects\projects\tdx-api\web && server.exe"
echo    ✓ tdx-api 启动中，等待 15 秒加载 5 万只股票代码...
timeout /t 15 /nobreak >nul

REM 验证 tdx-api 起来
powershell -NoProfile -Command "$r=Invoke-WebRequest -Uri 'http://localhost:9999/api/health' -UseBasicParsing -TimeoutSec 5 -ErrorAction SilentlyContinue; if ($r.StatusCode -eq 200) { Write-Host '   ✓ tdx-api 健康检查通过' -ForegroundColor Green } else { Write-Host '   ⚠ tdx-api 还没起来，稍等 10 秒重试...' -ForegroundColor Yellow; timeout /t 10 /nobreak >nul }"

:app_start
echo.
echo [3/4] 启动 Streamlit app ...
echo    (浏览器会自动打开 http://localhost:8503)

REM ---------- 3. 启动 Streamlit app ----------
cd /d D:\projects\projects\aiagents-stock-main\aiagents-stock-main
call venv\Scripts\activate.bat
start "" streamlit run app.py --server.port 8503 --server.headless false

echo.
echo ========================================
echo   启动完成！
echo ========================================
echo.
echo   访问地址: http://localhost:8503
echo.
echo   关闭服务:
echo     - tdx-api:   任务管理器结束 server.exe
echo     - Streamlit: 当前窗口 Ctrl+C
echo.
echo   (此窗口可关闭)
timeout /t 5 /nobreak >nul
exit
