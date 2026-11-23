@echo off
setlocal
title CLIP MASTER
cd /d "%~dp0"

echo ================================================
echo   CLIP MASTER - 9:16 clip studio
echo ================================================

rem --- Python 3.10 - 3.12 ---------------------------------------------------
set "PY=python"
where py >nul 2>nul && set "PY=py -3.12"
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo [ERREUR] Python introuvable. Installe Python 3.10-3.12 : https://www.python.org/downloads/
  pause & exit /b 1
)

rem --- venv + dependances -----------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo [1/5] Creation de l'environnement virtuel...
  %PY% -m venv .venv
  if errorlevel 1 ( echo [ERREUR] Impossible de creer le venv. & pause & exit /b 1 )
)
set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist ".venv\.deps_ok" (
  echo [2/5] Installation des dependances Python ^(premiere fois : plusieurs minutes^)...
  "%VPY%" -m pip install --upgrade pip
  "%VPY%" -m pip install -r requirements.txt
  if errorlevel 1 ( echo [ERREUR] pip install a echoue. & pause & exit /b 1 )
  echo ok> ".venv\.deps_ok"
)

rem --- .env ---------------------------------------------------------------------
if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo [info] .env cree depuis .env.example : ajoute ta cle GROQ_API_KEY pour les fonctions IA ^(optionnel^).
)

rem --- FFmpeg ---------------------------------------------------------------------
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [attention] FFmpeg n'est pas dans le PATH. Installe-le ^(winget install Gyan.FFmpeg^) ou renseigne FFMPEG_PATH dans .env.
  echo             L'export video ne marchera pas sans FFmpeg.
) else (
  echo [3/5] FFmpeg detecte.
)

rem --- Node.js + Remotion (optionnel) -----------------------------------------------
where node >nul 2>nul
if errorlevel 1 (
  echo [attention] Node.js absent : le rendu Remotion est desactive ^(le reste fonctionne^). https://nodejs.org
) else (
  if not exist "remotion\node_modules" (
    echo [4/5] npm install dans remotion/ ...
    pushd remotion
    call npm install --no-audit --no-fund
    popd
  ) else (
    echo [4/5] Remotion deja installe.
  )
)

rem --- Lancement ----------------------------------------------------------------------
for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /c:"CLIP_MASTER_PORT=" ".env"`) do set "CM_PORT=%%B"
if "%CM_PORT%"=="" set "CM_PORT=7866"
echo [5/5] Demarrage du serveur sur http://127.0.0.1:%CM_PORT% ...
start "CLIP_MASTER_SERVER" /B "%VPY%" server.py

powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 60;$i++){ try { $r=Invoke-WebRequest -UseBasicParsing http://127.0.0.1:%CM_PORT%/api/health -TimeoutSec 1; if($r.StatusCode -eq 200){ $ok=$true; break } } catch {}; Start-Sleep -Milliseconds 500 }; if($ok){ Start-Process 'http://127.0.0.1:%CM_PORT%' } else { Write-Host 'Le serveur ne repond pas.' }"
echo.
echo Ferme cette fenetre pour arreter CLIP MASTER.
pause
