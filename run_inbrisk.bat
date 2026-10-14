@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================================
echo   InbriskLLM - Qwen2.5-Coder-3B Fine-Tuned + Canli MCP
echo ========================================================

:: Model Ollama'da kayitli mi kontrol et, degilse otomatik kaydet
ollama list | findstr /i "inbrisk-3b" >nul
if errorlevel 1 (
    if exist "%~dp0inbrisk-qwen3b-q4.gguf" (
        echo [Inbrisk] inbrisk-3b modeli Ollama'ya kaydediliyor...
        ollama create inbrisk-3b -f "%~dp0Modelfile"
        if errorlevel 1 (
            echo [HATA] Model Ollama'ya eklenemedi!
            pause
            exit /b 1
        )
        echo [Inbrisk] Model basariyla kaydedildi.
    ) else (
        echo [UYARI] inbrisk-qwen3b-q4.gguf dosyasi klasorde bulunamadi.
        echo Lutfen Colab'den inen modeli bu klasore koyun.
        echo.
    )
)

echo [Inbrisk] Ajan baslatiliyor...
python ai_shell.py --backend ollama --model inbrisk-3b %*
if errorlevel 1 (
    echo.
    echo Bir hata olustu.
    pause
)