@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

REM --- Port: varsayilan 4000, FREELM_PORT ile degistirilebilir
if not "%FREELM_PORT%"=="" set "PORT=%FREELM_PORT%"
if not defined PORT set "PORT=4000"
set "BASE=http://127.0.0.1:%PORT%/v1"
set "HEALTH=http://127.0.0.1:%PORT%/health"

REM --- Keyless (kayitsiz) uc noktalar son care olarak hep acik olsun.
REM     Aksi halde tek anahtarli kurulumda saglayici 503/429 donerse
REM     failover'in gidecek baska yeri kalmaz ve oturum kirilir.
if not defined FREELM_KEYLESS set "FREELM_KEYLESS=1"

echo ========================================================
echo   freelm + Inbrisk AGENT
echo   Konusma ile PC yonetimi - model: freelm / ucretsiz API
echo   backend: openai -^> %BASE%
echo ========================================================

REM --- freelm komutunu cozelim (PATH'te yoksa python -m freelm)
set "FREELM=freelm"
where freelm >nul 2>nul
if errorlevel 1 set "FREELM=python -m freelm"

REM --- Uc nokta zaten ayakta mi?
set "OWN_SERVER=0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-RestMethod -Uri '%HEALTH%' -TimeoutSec 3 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 goto start_server
echo [freelm] Uc nokta zaten ayakta: %BASE%
goto run_agent

:start_server
echo [freelm] Yerel uc nokta baslatiliyor...
set "OWN_SERVER=1"
start "freelm-serve" /min cmd /c "!FREELM! serve --port %PORT%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$u='%HEALTH%'; for($i=0;$i -lt 60;$i++){ try{ Invoke-RestMethod -Uri $u -TimeoutSec 3 | Out-Null; exit 0 }catch{ Start-Sleep -Milliseconds 500 } }; exit 1"
if errorlevel 1 goto serve_failed
echo [freelm] Hazir: %BASE%
goto run_agent

:serve_failed
echo [hata] freelm sunucusu acilamadi. Elle dene: freelm serve --port %PORT%
pause
exit /b 1

:run_agent
python ai_shell.py --backend openai --base-url "%BASE%" --api-key "freelm" %*
set "RC=%errorlevel%"

REM --- Sunucuyu biz baslattiysak kapat
if "%OWN_SERVER%"=="0" goto done
echo.
echo [freelm] Sunucu kapatiliyor...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }"

:done
if "%RC%"=="0" exit /b 0
echo.
echo Bir hata olustu (cikis kodu %RC%).
pause
exit /b %RC%