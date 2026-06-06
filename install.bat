@echo off
echo [Backup System Installer]
echo [1/3] Creating virtual environment...
uv venv .venv
if errorlevel 1 ( echo ERROR: uv venv failed & exit /b 1 )
echo [2/3] Installing dependencies (offline)...
uv pip install --no-index --find-links vendor -r requirements.txt
if errorlevel 1 ( echo ERROR: install failed & exit /b 1 )
echo [3/3] Done! Run start.bat --dry-run to verify.
