@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================================
echo   InbriskLLM - Qwen3-0.6B Ultra-Fast Desktop Agent
echo ========================================================

:: Model Ollama'da kayitli mi kontrol et, degilse otomatik derle
ollama list | findstr /i "inbrisk-qwen3:0.6b" >nul
if errorlevel 1 (
    echo [Inbrisk] inbrisk-qwen3:0.6b modeli Modelfile ile hazirlaniyor...
    ollama create inbrisk-qwen3:0.6b -f Modelfile.qwen3-0.6b
    if errorlevel 1 (
        echo [HATA] inbrisk-qwen3:0.6b hazirlanamadi!
        pause
        exit /b 1
    )
    echo [Inbrisk] Model basariyla hazirlandi.
)

echo [Inbrisk] Ajan baslatiliyor (inbrisk-qwen3:0.6b)...
python ai_shell.py --backend ollama --model inbrisk-qwen3:0.6b %*
if errorlevel 1 (
    echo.
    echo Bir hata olustu.
    pause
)