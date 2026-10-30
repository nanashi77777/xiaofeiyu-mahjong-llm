@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m majsoul_llm.cli serve
pause
