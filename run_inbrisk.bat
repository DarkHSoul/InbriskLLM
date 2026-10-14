@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================================
echo   InbriskLLM - Yerel 1.7B Master Agent + Canli MCP
echo ========================================================
python ai_shell.py --backend inbrisk-agent %*
if errorlevel 1 (
    echo.
    echo Bir hata olustu.
    pause
)