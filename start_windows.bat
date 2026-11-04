@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -c "import fastapi,uvicorn,multipart,pydantic_core"
if errorlevel 1 (
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -X utf8 run_local.py
if errorlevel 1 goto failed
goto end
:failed
echo 啟動失敗，請保留上方錯誤訊息。請勿直接開啟 HTML 檔案。
:end
pause
