@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
cd /d "%ROOT%"

echo A abrir Backend (porta 8000) e Frontend (Vite) em janelas separadas...
start "API-8000" cmd /k "cd /d "%ROOT%backend" && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
timeout /t 2 /nobreak >nul
start "Vite" cmd /k "cd /d "%ROOT%frontend" && npx vite --host 127.0.0.1 --port 5173"

echo.
echo Backend:  http://127.0.0.1:8000/docs
echo Frontend: http://127.0.0.1:5173  (se a porta estiver ocupada, o Vite usa outra - veja a janela Vite)
echo.
pause
