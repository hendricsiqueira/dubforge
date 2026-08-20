@echo off
setlocal
cd /d "%~dp0"
if not defined ZAST_TRANSLATE_PATH set "ZAST_TRANSLATE_PATH=%~dp0..\ZastTranslate"
set "PYTHON_EXE=%ZAST_TRANSLATE_PATH%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo ZastTranslate ou o ambiente .venv nao foi encontrado em:
  echo   %ZAST_TRANSLATE_PATH%
  echo Defina ZAST_TRANSLATE_PATH para a pasta correta e tente novamente.
  pause
  exit /b 1
)
set "PATH=%ZAST_TRANSLATE_PATH%\.venv\Scripts;%PATH%"
"%PYTHON_EXE%" app.py
pause
