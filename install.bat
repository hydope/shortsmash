@echo off
setlocal enabledelayedexpansion
title ShortSmash Setup
cd /d "%~dp0"

echo.
echo  ============================================
echo   ShortSmash one-click setup
echo  ============================================
echo.

:: ---------- Python ----------
where python >nul 2>&1
if %errorlevel% neq 0 (
  echo  [X] Python not found.
  echo.
  echo  Install Python 3.11, 3.12 or 3.13 from:
  echo  https://www.python.org/downloads/
  echo.
  echo  IMPORTANT: on the first install screen, tick
  echo  "Add python.exe to PATH" before clicking Install.
  echo.
  pause
  exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYV=%%v
echo  [OK] Python %PYV%

:: torch has no wheels for 3.14+ yet -> skip vision extra there
set SKIP_VISION=0
for /f "tokens=1,2 delims=." %%a in ("%PYV%") do ( set PYMAJ=%%a & set PYMIN=%%b )
if %PYMAJ% GTR 3 set SKIP_VISION=1
if %PYMAJ% EQU 3 if %PYMIN% GEQ 14 set SKIP_VISION=1
if %SKIP_VISION% EQU 1 (
  echo  [X] Python %PYV% cannot build a working venv here.
  echo      Install Python 3.11, 3.12 or 3.13 from python.org,
  echo      then run install.bat again.
  pause
  exit /b 1
)

:: ---------- ffmpeg ----------
where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
  echo  [X] ffmpeg not found.
  echo.
  echo  Install it with this command in PowerShell:
  echo  winget install ffmpeg
  echo.
  echo  Then close this window and run install.bat again.
  echo.
  pause
  exit /b 1
)
echo  [OK] ffmpeg found

:: ---------- venv ----------
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo  [*] Creating virtual environment...
  python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo  [X] Could not create the virtual environment.
    pause
    exit /b 1
  )
)
if not exist ".venv\Scripts\pip.exe" (
  echo  [X] Virtual environment has no pip. Use Python 3.11, 3.12 or 3.13.
  pause
  exit /b 1
)
echo  [OK] Virtual environment ready

:: ---------- dependencies ----------
echo.
echo  [*] Installing ShortSmash - this is the long step, one time only.
echo      Go grab a coffee, this takes 5-15 minutes.
echo.
".venv\Scripts\python" -m pip install --upgrade pip --quiet
set EXTRAS=ai,voice,web,mcp,trends,bluesky,threads,youtube,x,schedule,captions
if %SKIP_VISION% EQU 0 set EXTRAS=%EXTRAS%,vision
".venv\Scripts\python" -m pip install -e ".[%EXTRAS%]"
if %errorlevel% neq 0 (
  echo  [!] Full install hit a problem - retrying without the vision extra...
  ".venv\Scripts\python" -m pip install -e ".[ai,voice,web,mcp,trends,bluesky,threads,youtube,x,schedule,captions]"
  if !errorlevel! neq 0 (
    echo  [X] Install failed. Scroll up for the red error text.
    pause
    exit /b 1
  )
  set SKIP_VISION=1
)
echo  [OK] Dependencies installed

:: ---------- CLIP model ----------
if %SKIP_VISION% EQU 0 (
  echo.
  echo  [*] Downloading footage-check model (~600MB, one time)...
  ".venv\Scripts\python" -c "from shortsmash.vision_local import ensure_model; ensure_model()"
  echo  [OK] Footage check ready
) else (
  echo  [!] Skipped footage-check model (needs torch).
)

:: ---------- voices ----------
echo.
echo  [*] Downloading voices (English + Urdu + Hindi + Chinese, one time)...
".venv\Scripts\python" -c "
import os, urllib.request
os.makedirs('voices', exist_ok=True)
files = [
  ('en_US-amy-medium.onnx', 'en/en_US/amy/medium/en_US-amy-medium.onnx'),
  ('en_US-amy-medium.onnx.json', 'en/en_US/amy/medium/en_US-amy-medium.onnx.json'),
  ('ur_PK-aegis_female-medium.onnx', 'ur/ur_PK/aegis_female/medium/ur_PK-aegis_female-medium.onnx'),
  ('ur_PK-aegis_female-medium.onnx.json', 'ur/ur_PK/aegis_female/medium/ur_PK-aegis_female-medium.onnx.json'),
  ('hi_IN-priyamvada-medium.onnx', 'hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium.onnx'),
  ('hi_IN-priyamvada-medium.onnx.json', 'hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium.onnx.json'),
  ('zh_CN-huayan-medium.onnx', 'zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx'),
  ('zh_CN-huayan-medium.onnx.json', 'zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json'),
]
base = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/'
for name, path in files:
    dest = os.path.join('voices', name)
    if not os.path.exists(dest):
        print('  downloading', name)
        try:
            urllib.request.urlretrieve(base + path, dest)
        except Exception as e:
            print('  ! failed:', name)
print('done')
"
echo  [OK] Voices ready

:: ---------- font ----------
echo.
echo  [*] Installing Urdu caption font...
if not exist "%LOCALAPPDATA%\Microsoft\Windows\Fonts" mkdir "%LOCALAPPDATA%\Microsoft\Windows\Fonts"
copy /y "fonts\NotoNastaliqUrdu.ttf" "%LOCALAPPDATA%\Microsoft\Windows\Fonts\" >nul 2>&1
echo  [OK] Font ready

:: ---------- config + music ----------
echo.
echo  [*] Setting up config and starter music...
".venv\Scripts\python" -m shortsmash.cli init >nul 2>&1
".venv\Scripts\python" -c "from shortsmash.music import ensure_library; ensure_library()" >nul 2>&1
echo  [OK] Config ready

:: ---------- health check ----------
echo.
echo  [*] Health check...
".venv\Scripts\python" -m shortsmash.cli doctor

echo.
echo  ============================================
echo   ShortSmash is ready!
echo  ============================================
echo.
set /p RUNNOW="  Start the Studio server now? [Y/n]: "
if /i "%RUNNOW%"=="n" goto :skipserver
if /i "%RUNNOW%"=="no" goto :skipserver
echo.
echo  [*] Starting ShortSmash Studio...
echo      Open http://127.0.0.1:8000 in your browser.
echo      Press Ctrl+C in this window to stop the server.
echo.
".venv\Scripts\shortsmash" serve
goto :end

:skipserver
echo.
echo   When you're ready, run:
echo.
echo     .venv\Scripts\activate
echo     shortsmash serve
echo.
echo   Then open http://127.0.0.1:8000 in your browser.
echo.
echo   Make a video from the Studio dashboard, then use
echo   the Publish tab to post it everywhere in one click.
echo.
pause

:end
