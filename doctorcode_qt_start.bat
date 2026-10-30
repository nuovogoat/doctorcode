@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m doctorcode.launch --qt %*
) else (
    python -m doctorcode.launch --qt %*
)
if errorlevel 1 pause
endlocal
