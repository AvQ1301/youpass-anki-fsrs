@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Virtual environment not found. Follow the Windows installation steps in README.md.
  exit /b 1
)
start "YouPass Anki FSRS" http://127.0.0.1:8844
.venv\Scripts\python.exe -m youpass_anki.main
