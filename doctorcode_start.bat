@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m doctorcode.launch %*
) else (
    python -m doctorcode.launch %*
)
if errorlevel 1 pause
endlocal
