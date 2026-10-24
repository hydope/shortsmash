@echo off
setlocal enabledelayedexpansion
title ShortSmash One-Click Setup
:: ShortSmash one-click installer for Windows CMD.
:: One-line install from CMD:
::   curl -L -o "%TEMP%\rf-install.cmd" "https://raw.githubusercontent.com/hydope/shortsmash/master/install.cmd" && "%TEMP%\rf-install.cmd"
:: Does everything: Python, FFmpeg, repo, venv, voices, fonts, shortcuts.

echo.
echo  ============================================
echo   ShortSmash one-click setup (CMD)
echo  ============================================
echo.

set "INSTALLDIR=%USERPROFILE%\ShortSmash"
set "REPO=https://github.com/hydope/shortsmash.git"

:: ---------- winget check ----------
where winget >nul 2>&1
if %errorlevel% neq 0 (
  echo  [X] winget not found. Install "App Installer" from the Microsoft Store, then re-run.
  pause & exit /b 1
)
echo  [OK] winget present

:: ---------- Python ----------
where python >nul 2>&1
if %errorlevel% neq 0 (
  echo  [*] Installing Python 3.12 via winget...
  winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
  :: refresh PATH for this session
  for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "MACHINEPATH=%%b"
  for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USERPATH=%%b"
  set "PATH=%MACHINEPATH%;%USERPATH%"
)
where python >nul 2>&1
if %errorlevel% neq 0 (
  echo  [X] Python install failed. Install from python.org and tick Add python.exe to PATH, then re-run.
  pause & exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYV=%%v
echo  [OK] Python %PYV%
set SKIP_VISION=0
for /f "tokens=1,2 delims=." %%a in ("%PYV%") do (
  if %%a GTR 3 set SKIP_VISION=1
  if %%a EQU 3 if %%b GEQ 14 set SKIP_VISION=1
)
:: Python 3.14+ cannot build a working venv (ensurepip fails, so pip.exe
:: never appears) - and this project requires 3.11-3.13. If the machine's
:: python is 3.14+, install 3.12 side by side and use that instead.
set "PYEXE=python"
if %SKIP_VISION%==1 (
  echo  [!] Python %PYV% cannot build a working venv, installing Python 3.12 side by side...
  winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
  set "PYEXE="
  for /f "delims=" %%p in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%p"
  if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  if not defined PYEXE if exist "C:\Program Files\Python312\python.exe" set "PYEXE=C:\Program Files\Python312\python.exe"
  if not defined PYEXE (
    echo  [X] Could not find Python 3.12 after installing it. Install Python 3.12 from python.org and re-run.
    pause & exit /b 1
  )
  echo  [OK] Using Python 3.12 at !PYEXE!
  set SKIP_VISION=0
)

:: ---------- FFmpeg ----------
where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
  echo  [*] Installing FFmpeg via winget...
  winget install -e --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
  for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "MACHINEPATH=%%b"
  for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USERPATH=%%b"
  set "PATH=%MACHINEPATH%;%USERPATH%"
)
where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
  echo  [X] FFmpeg install failed. Run: winget install Gyan.FFmpeg
  pause & exit /b 1
)
echo  [OK] FFmpeg present

:: ---------- Repo ----------
where git >nul 2>&1
if %errorlevel%==0 (
  if exist "%INSTALLDIR%\.git" (
    echo  [*] Updating existing install...
    git -C "%INSTALLDIR%" pull --ff-only >nul 2>&1
  ) else (
    echo  [*] Cloning ShortSmash (code only, no demo videos)...
    git clone --depth 1 --filter=blob:none --sparse "%REPO%" "%INSTALLDIR%"
    if !errorlevel! neq 0 (
      echo  [X] Clone failed. Delete "%INSTALLDIR%" and re-run, or check your connection.
      pause & exit /b 1
    )
    git -C "%INSTALLDIR%" sparse-checkout set shortsmash scripts fonts
    if !errorlevel! neq 0 (
      echo  [!] Sparse checkout unsupported by this git - falling back to full tree.
      git -C "%INSTALLDIR%" sparse-checkout disable
    )
  )
) else (
  echo  [*] Downloading ShortSmash zip (no git found)...
  curl -L -o "%TEMP%\shortsmash.zip" "https://github.com/hydope/shortsmash/archive/refs/heads/master.zip"
  powershell -NoProfile -Command "Expand-Archive -Path '%TEMP%\shortsmash.zip' -DestinationPath '%TEMP%' -Force"
  if exist "%INSTALLDIR%" rmdir /s /q "%INSTALLDIR%"
  move "%TEMP%\shortsmash-master" "%INSTALLDIR%" >nul
  if exist "%INSTALLDIR%\demo" rmdir /s /q "%INSTALLDIR%\demo"
  if exist "%INSTALLDIR%\launch\fb-posts" rmdir /s /q "%INSTALLDIR%\launch\fb-posts"
)
if not exist "%INSTALLDIR%\pyproject.toml" (
  echo  [X] ShortSmash code did not arrive completely. Delete "%INSTALLDIR%" and re-run.
  pause & exit /b 1
)
echo  [OK] ShortSmash code ready

:: ---------- venv + install ----------
cd /d "%INSTALLDIR%"
if not exist ".venv\Scripts\python.exe" (
  echo  [*] Creating virtual environment...
  "%PYEXE%" -m venv .venv
)
if not exist ".venv\Scripts\pip.exe" (
  echo  [X] Virtual environment has no pip. This happens on Python 3.14 - use Python 3.12 or 3.13.
  pause & exit /b 1
)
echo  [*] Installing ShortSmash (a few minutes)...
.venv\Scripts\python.exe -m pip install -q --upgrade pip >nul 2>&1
set "EXTRAS=ai,voice,web,mcp,trends,bluesky,threads,youtube,x,schedule,captions"
if %SKIP_VISION%==0 set "EXTRAS=%EXTRAS%,vision"
.venv\Scripts\pip.exe install -q -e ".[%EXTRAS%]" >nul 2>&1
if %errorlevel% neq 0 (
  echo  [!!] Retrying without vision extra...
  .venv\Scripts\pip.exe install -q -e ".[ai,voice,web,mcp,trends,bluesky,threads,youtube,x,schedule,captions]"
  if %errorlevel% neq 0 (
    echo  [X] pip install failed. Re-run, or paste the error on the ShortSmash repo.
    pause & exit /b 1
  )
)
echo  [OK] ShortSmash installed

:: ---------- Voices + font ----------
echo  [*] Downloading neural voices (one time)...
.venv\Scripts\python.exe -c "import urllib.request,pathlib; v=pathlib.Path(r'%INSTALLDIR%')/'voices'; v.mkdir(exist_ok=True); b='https://huggingface.co/rhasspy/piper-voices/resolve/main/'; fs=[('en_US-amy-medium.onnx','en/en_US/amy/medium/en_US-amy-medium.onnx'),('en_US-amy-medium.onnx.json','en/en_US/amy/medium/en_US-amy-medium.onnx.json'),('ur_PK-aegis_female-medium.onnx','ur/ur_PK/aegis_female/medium/ur_PK-aegis_female-medium.onnx'),('ur_PK-aegis_female-medium.onnx.json','ur/ur_PK/aegis_female/medium/ur_PK-aegis_female-medium.onnx.json'),('ur_PK-fasih-medium.onnx','ur/ur_PK/fasih/medium/ur_PK-fasih-medium.onnx'),('ur_PK-fasih-medium.onnx.json','ur/ur_PK/fasih/medium/ur_PK-fasih-medium.onnx.json'),('hi_IN-priyamvada-medium.onnx','hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium.onnx'),('hi_IN-priyamvada-medium.onnx.json','hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium.onnx.json'),('hi_IN-rohan-medium.onnx','hi/hi_IN/rohan/medium/hi_IN-rohan-medium.onnx'),('hi_IN-rohan-medium.onnx.json','hi/hi_IN/rohan/medium/hi_IN-rohan-medium.onnx.json'),('zh_CN-huayan-medium.onnx','zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx'),('zh_CN-huayan-medium.onnx.json','zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json')]; [urllib.request.urlretrieve(b+r, v/n) for n,r in fs if not (v/n).exists() or (v/n).stat().st_size<1000]; print('voices ok')"
echo  [OK] Voices ready

:: ---------- Shortcuts ----------
echo  [*] Creating shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; foreach($d in @(\"$env:USERPROFILE\Desktop\", \"$env:APPDATA\Microsoft\Windows\Start Menu\Programs\")) { $l=$ws.CreateShortcut(\"$d\ShortSmash Studio.lnk\"); $l.TargetPath='powershell.exe'; $l.Arguments='-NoProfile -ExecutionPolicy Bypass -File \"%INSTALLDIR%\scripts\launch-studio.ps1\"'; $l.WorkingDirectory='%INSTALLDIR%'; $l.Description='ShortSmash Studio'; $l.Save() }"
echo  [OK] Shortcuts created (Desktop + Start Menu)

echo.
echo  ============================================
echo   Done! Launch "ShortSmash Studio" from
echo   your Desktop or Start Menu.
echo  ============================================
echo.
set /p LAUNCH="  Start the Studio now? [Y/n] "
if /i not "%LAUNCH%"=="n" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%INSTALLDIR%\scripts\launch-studio.ps1"
)
