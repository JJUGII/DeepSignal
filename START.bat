@echo off
setlocal EnableExtensions
title DeepSignal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
set "PORT=8765"
set "URL=http://127.0.0.1:%PORT%"

if not exist "%PY%" (
  echo.
  echo [오류] 가상환경이 없습니다.
  echo.
  echo 아래를 cmd에서 한 번만 실행하세요:
  echo   python -m venv .venv
  echo   .venv\Scripts\pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

if not exist "logs" mkdir logs
if not exist "outputs" mkdir outputs

netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if %ERRORLEVEL%==0 (
  echo.
  echo DeepSignal Web UI가 이미 실행 중입니다.
  echo %URL%
  echo.
  start "" "%URL%"
  pause
  exit /b 0
)

echo.
echo ========================================
echo   DeepSignal 시작 중...
echo   %URL%
echo   종료: 이 창에서 Ctrl+C
echo ========================================
echo.

start /b cmd /c "timeout /t 2 /nobreak >nul && start "" "%URL%""

"%PY%" main.py web-ui --port %PORT%
set "EC=%ERRORLEVEL%"

echo.
if not "%EC%"=="0" echo [종료] 오류 코드 %EC%
pause
exit /b %EC%
