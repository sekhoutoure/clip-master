@echo off
setlocal
title CLIP MASTER
cd /d "%~dp0"

echo ================================================
echo   CLIP MASTER - 9:16 clip studio
echo ================================================

rem --- First run: install.ps1 sets up venv, packages, models, third-party tools ---
if not exist ".venv\Scripts\python.exe" goto :install
if not exist ".venv\.install_ok" goto :install
goto :run

:install
echo [setup] First run: running install.ps1 (several minutes, needs internet)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
  echo [ERROR] Installation failed. Read the messages above, then run start.bat again.
  pause & exit /b 1
)
echo ok> ".venv\.install_ok"

:run
set "VPY=%~dp0.venv\Scripts\python.exe"
for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /c:"CLIP_MASTER_PORT=" ".env"`) do set "CM_PORT=%%B"
if "%CM_PORT%"=="" set "CM_PORT=7866"
echo Starting server on http://127.0.0.1:%CM_PORT% ...
start "CLIP_MASTER_SERVER" /B "%VPY%" -m clip_master_cli serve

powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 60;$i++){ try { $r=Invoke-WebRequest -UseBasicParsing http://127.0.0.1:%CM_PORT%/api/health -TimeoutSec 1; if($r.StatusCode -eq 200){ $ok=$true; break } } catch {}; Start-Sleep -Milliseconds 500 }; if($ok){ Start-Process 'http://127.0.0.1:%CM_PORT%' } else { Write-Host 'The server does not answer.' }"
echo.
echo Close this window to stop CLIP MASTER. Check your setup: .\install.ps1 -Check
pause
