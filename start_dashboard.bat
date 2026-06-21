@echo off
if not exist ".venv\Scripts\python.exe" ( echo Run install.bat first & exit /b 1 )
if not exist "config.json" ( echo config.json not found. Copy config.example.json to config.json and fill in your settings. & exit /b 1 )
echo Dashboard: http://127.0.0.1:8080/
echo Reading backup status from storage server (refreshes every 5 minutes)...
.venv\Scripts\python.exe src\run_server.py --dashboard-only --config config.json %*
