@echo off
REM HETA Kalkulationsassistent - Start unter Windows
REM Lauscht ausschliesslich auf 127.0.0.1:8000 (keine Netzwerkfreigabe).
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %ERRORLEVEL%==0 (set PYLAUNCH=py -3) else (set PYLAUNCH=python)

if not exist ".venv\Scripts\python.exe" (
    echo [HETA] Erstelle virtuelle Python-Umgebung ...
    %PYLAUNCH% -m venv .venv || goto :fehler
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fehler
)

if not exist ".env" if exist ".env.example" copy ".env.example" ".env" >nul

echo [HETA] Starte Anwendung unter http://127.0.0.1:8000
start "" "http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
goto :eof

:fehler
echo [HETA] Start fehlgeschlagen. Bitte Python 3.12+ installieren und Internetzugang fuer pip pruefen.
pause
exit /b 1
