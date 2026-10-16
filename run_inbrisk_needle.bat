@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================================
echo   InbriskLLM - Needle 3 Yerel Arac-Cagri Modu
echo ========================================================

:: needle paketi kurulu mu kontrol et, degilse otomatik kur
python -c "import needle" >nul 2>nul
if errorlevel 1 (
    echo [Inbrisk] cactus-needle paketi kuruluyor...
    pip install cactus-needle
    if errorlevel 1 (
        echo [HATA] cactus-needle kurulamadi!
        pause
        exit /b 1
    )
)

:: Model agirliklari (needle3.cact, 33 MB) ve motor WinDLL cache'de mi?
python -c "from needle.agent import fetch; import os; p=fetch.fetch_weights(3); print('[Inbrisk] Model hazir: %s (%d MB)' % (p, os.path.getsize(p)//(1024*1024)))"
if errorlevel 1 (
    echo [HATA] needle3.cact indirilemedi!
    pause
    exit /b 1
)

echo [Inbrisk] Ajan baslatiliyor (needle3, yerel motor)...
python ai_shell.py --backend needle --model needle3 %*
if errorlevel 1 (
    echo.
    echo Bir hata olustu.
    pause
)
