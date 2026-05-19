@echo off
chcp 65001 >nul
setlocal

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%backend"

if not exist "%BACKEND_DIR%\app\main.py" (
  echo [ERRO] Nao encontrei "%BACKEND_DIR%\app\main.py".
  echo Verifique se este .bat esta na raiz do projeto.
  pause
  exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
  echo [ERRO] Python nao encontrado no PATH.
  echo Instale o Python ou ative seu ambiente virtual antes de executar.
  pause
  exit /b 1
)

cd /d "%BACKEND_DIR%"
echo Iniciando backend em http://127.0.0.1:8000 ...
echo Docs: http://127.0.0.1:8000/docs
echo.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo Backend finalizado.
pause
