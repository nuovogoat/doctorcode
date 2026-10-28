@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m doctorcode.launch %*
) else (
    python -m doctorcode.launch %*
)
endlocal
