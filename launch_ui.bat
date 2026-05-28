@echo off
setlocal
set "PROJECT_ROOT=%~dp0"
set "VENV_PYTHON=%PROJECT_ROOT%.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
  echo Virtual environment not found at .venv
  echo Create it first with: python -m venv .venv
  exit /b 1
)

pushd "%PROJECT_ROOT%"
"%VENV_PYTHON%" "ui.py"
set "EXITCODE=%ERRORLEVEL%"
popd
exit /b %EXITCODE%
