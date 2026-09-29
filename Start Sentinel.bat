@echo off
setlocal
cd /d "%~dp0"
rem Keep Python packages in a short local path to avoid Windows MAX_PATH errors
set "SENTINEL_HOME=%LOCALAPPDATA%\SentinelFraudDetection"
set "VENV_DIR=%SENTINEL_HOME%\venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"

if not exist "%SENTINEL_HOME%" mkdir "%SENTINEL_HOME%"

if not exist "%PYTHON_EXE%" (
  echo Creating the project environment...
  py -3 -m venv "%VENV_DIR%"
  if errorlevel 1 goto setup_failed
)

if not exist "%VENV_DIR%\Lib\site-packages\fastapi\__init__.py" goto install_deps
if not exist "%VENV_DIR%\Lib\site-packages\uvicorn\__init__.py" goto install_deps
if not exist "%VENV_DIR%\Lib\site-packages\sklearn\__init__.py" goto install_deps
if not exist "%VENV_DIR%\Lib\site-packages\scipy\__init__.py" goto install_deps
goto start_server

:install_deps
echo Installing the website dependencies. This can take a few minutes...
"%PYTHON_EXE%" -m pip install -r "%CD%\requirements-dev.txt"
if errorlevel 1 goto setup_failed

:start_server
echo Starting Sentinel. Keep the server window open while using the site.
start "Sentinel server" /D "%CD%" "%PYTHON_EXE%" -m uvicorn sentinel.main:app --host 127.0.0.1 --port 8000
echo Waiting for the dashboard to be ready...
powershell -NoProfile -Command "$ready=$false; for($i=0;$i -lt 300;$i++){try{$h=Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 2; if($h.status -eq 'ok'){$ready=$true; break}}catch{}; Start-Sleep -Seconds 1}; if(-not $ready){exit 1}"
if errorlevel 1 goto server_failed
start "" "http://127.0.0.1:8000/"
echo Dashboard opened in your browser.
exit /b 0

:setup_failed
echo.
echo Setup did not finish. Check the message above. Python 3 and internet access are required for first-time setup.
pause
exit /b 1

:server_failed
echo.
echo The server did not become ready. Check the Sentinel server window for the error, then share that message for help.
pause
exit /b 1
