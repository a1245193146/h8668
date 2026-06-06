@echo off
if not exist ".venv\Scripts\python.exe" ( echo Run install.bat first & exit /b 1 )
echo Dashboard: http://127.0.0.1:8080/
.venv\Scripts\python.exe src\run_server.py --dashboard-only %*
