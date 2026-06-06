@echo off
if not exist ".venv\Scripts\python.exe" ( echo Run install.bat first & exit /b 1 )
.venv\Scripts\python.exe src\main.py %*
