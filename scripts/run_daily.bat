@echo off
setlocal EnableExtensions
REM DeepSignal: 일일 파이프라인 (프로젝트 루트 = 본 파일의 상위 폴더)
cd /d "%~dp0.."
if not exist "logs" mkdir logs

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo.>> "logs\run_daily_console.log"
echo ===== run-daily %date% %time% =====>> "logs\run_daily_console.log"
"%PY%" main.py run-daily --log-json 1>> "logs\run_daily_console.log" 2>&1
set EXITCODE=%ERRORLEVEL%
echo run-daily EXITCODE=%EXITCODE%>> "logs\run_daily_console.log"
if %EXITCODE% neq 0 (
  echo ERRORLEVEL=%EXITCODE%
  exit /b %EXITCODE%
)
exit /b 0
