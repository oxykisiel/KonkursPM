@echo off
chcp 65001 >nul
cd /d "D:\Programy\KonkursPM"
call .venv\Scripts\activate.bat
python pm_agent_multi.py %*
echo.
echo ========================================
echo   Gotowe! Nacisnij dowolny klawisz...
echo ========================================
pause >nul
