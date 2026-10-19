@echo off
rem Start Render Upscaler from source on Windows.
rem First run creates a private Python environment and installs what is needed.
cd /d "%~dp0"
if not exist .venv (
  echo First start: setting up ^(one time, a few minutes^)...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip -q
  .venv\Scripts\python -m pip install -r requirements.txt -q
)
.venv\Scripts\pythonw app.py %*
