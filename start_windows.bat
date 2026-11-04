@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto create_venv
goto check_dependencies
:create_venv
py -3 -m venv .venv
if errorlevel 1 goto failed
:check_dependencies
".venv\Scripts\python.exe" -c "import fastapi,uvicorn,multipart,pydantic_core,openpyxl,certifi"
if not errorlevel 1 goto launch
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
:launch
".venv\Scripts\python.exe" -X utf8 run_local.py %*
if errorlevel 1 goto failed
if "%~1"=="" pause
exit /b 0
:failed
echo.
echo Startup failed. Keep this window open and send the error above.
echo Run this BAT file. Do not open the HTML file directly.
if "%~1"=="" pause
exit /b 1
