@echo off
setlocal EnableExtensions
REM DeepSignal: tkinter 조회 전용 대시보드 (프로젝트 루트 = 본 파일의 상위 폴더)
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py dashboard
) else (
  python main.py dashboard
)
exit /b %ERRORLEVEL%
