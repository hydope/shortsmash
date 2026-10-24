<# ShortSmash one-click installer (PowerShell).
   Run from an elevated-or-not PowerShell with:
     & ([scriptblock]::Create((irm "https://raw.githubusercontent.com/hydope/shortsmash/master/install.ps1")))
   Does everything: Python, FFmpeg, Git, repo, venv, voices, fonts,
   Desktop + Start Menu shortcuts. Then launches the Studio. #>
$ErrorActionPreference = "Stop"
$InstallDir = Join-Path $HOME "ShortSmash"
$RepoUrl = "https://github.com/hydope/shortsmash.git"

function Say($m) { Write-Host "  $m" }
function Ok($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!!] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "  [X] $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host "   ShortSmash one-click setup" -ForegroundColor Cyan
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host ""

# ---------- winget ----------
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
  Fail "winget not found. Install 'App Installer' from the Microsoft Store, then re-run."
}
Ok "winget present"

# ---------- Python ----------
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
  Say "Installing Python 3.12 via winget (this takes a minute)..."
  winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements | Out-Null
  $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
  $py = Get-Command python -ErrorAction SilentlyContinue
}
if (-not $py) { Fail "Python install failed. Install manually from python.org (tick 'Add to PATH'), then re-run." }
$pyv = (python --version 2>&1) -replace "Python ",""
Ok "Python $pyv"
$major = [int]($pyv.Split(".")[0]); $minor = [int]($pyv.Split(".")[1])
$SkipVision = ($major -gt 3) -or ($major -eq 3 -and $minor -ge 14)
# Python 3.14+ cannot build a working venv (ensurepip fails, so pip.exe
# never appears) - and the README requires 3.11-3.13. If the machine's
# python is 3.14+, install 3.13 side by side and use that instead.
$PythonExe = "python"
if ($SkipVision) {
  Say "Python $pyv cannot build a working venv - installing Python 3.13 side by side..."
  winget install -e --id Python.Python.3.13 --silent --accept-package-agreements --accept-source-agreements | Out-Null
  $py13 = $null
  if (Get-Command py -ErrorAction SilentlyContinue) {
    try { $py13 = (py -3.13 -c "import sys; print(sys.executable)" 2>$null) | Select-Object -First 1 } catch {}
  }
  if (-not $py13) {
    foreach ($cand in @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
                        "C:\Program Files\Python313\python.exe")) {
      if (Test-Path $cand) { $py13 = $cand; break }
    }
  }
  if (-not $py13 -or -not (Test-Path $py13)) {
    Fail "Could not find Python 3.13 after installing it. Install Python 3.13 from python.org (tick 'Add to PATH'), then re-run."
  }
  $PythonExe = $py13
  $pyv = (& $py13 --version 2>&1) -replace "Python ",""
  $SkipVision = $false
  Ok "Using Python $pyv at $py13"
}

# ---------- FFmpeg ----------
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Say "Installing FFmpeg via winget..."
  winget install -e --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements | Out-Null
  $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Fail "FFmpeg install failed. Run: winget install Gyan.FFmpeg" }
Ok "FFmpeg $(ffmpeg -version 2>&1 | Select-Object -First 1 | ForEach-Object { ($_ -split ' ')[2] })"

# ---------- Git ----------
$haveGit = Get-Command git -ErrorAction SilentlyContinue
if (-not $haveGit) {
  Say "Installing Git via winget..."
  winget install -e --id Git.Git --silent --accept-package-agreements --accept-source-agreements | Out-Null
  $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
  $haveGit = Get-Command git -ErrorAction SilentlyContinue
}
if ($haveGit) { Ok "Git present" }

# ---------- Repo ----------
if (Test-Path (Join-Path $InstallDir ".git")) {
  Say "Updating existing install at $InstallDir..."
  git -C $InstallDir pull --ff-only | Out-Null
  if ($LASTEXITCODE -ne 0) { Warn "Could not update the existing install - using the copy already on disk." }
} elseif ($haveGit) {
  Say "Cloning ShortSmash to $InstallDir (code only - no demo videos)..."
  git clone --depth 1 --filter=blob:none --sparse $RepoUrl $InstallDir
  if ($LASTEXITCODE -ne 0) {
    # A clone can report success yet leave a partial tree (e.g. one file
    # locked by antivirus during checkout). Never claim [OK] on that:
    # retry with git's own restore, and fail loudly if it still fails.
    Warn "Checkout hit a snag - retrying the file restore..."
    git -C $InstallDir restore --source=HEAD -- .
    if ($LASTEXITCODE -ne 0) {
      Fail "Clone/checkout failed. Delete $InstallDir and re-run, or repair with: git -C $InstallDir restore --source=HEAD -- ."
    }
  }
  # Demo videos are ~200MB and the app never reads them at runtime, so
  # keep them out of the install: code + scripts + fonts only.
  git -C $InstallDir sparse-checkout set shortsmash scripts fonts
  if ($LASTEXITCODE -ne 0) {
    Warn "Sparse checkout unsupported by this git - falling back to the full tree."
    git -C $InstallDir sparse-checkout disable
  }
  if (-not (Test-Path (Join-Path $InstallDir "pyproject.toml"))) {
    Fail "ShortSmash code did not check out completely (pyproject.toml missing). Delete $InstallDir and re-run."
  }
} else {
  Say "Downloading ShortSmash zip..."
  $zip = Join-Path $env:TEMP "shortsmash.zip"
  Invoke-WebRequest -Uri "https://github.com/hydope/shortsmash/archive/refs/heads/master.zip" -OutFile $zip
  Expand-Archive -Path $zip -DestinationPath $env:TEMP -Force
  if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
  Move-Item (Join-Path $env:TEMP "shortsmash-master") $InstallDir
  foreach ($heavy in @("demo", "launch\fb-posts")) {
    $p = Join-Path $InstallDir $heavy
    if (Test-Path $p) { Remove-Item -Recurse -Force $p }
  }
}
Ok "ShortSmash code ready"

# ---------- venv + package ----------
$venvPy = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
  Say "Creating virtual environment..."
  & $PythonExe -m venv (Join-Path $InstallDir ".venv")
}
$venvPip = Join-Path $InstallDir ".venv\Scripts\pip.exe"
if (-not (Test-Path $venvPip)) {
  Fail "Virtual environment has no pip (ensurepip failed). This happens on Python 3.14 - use Python 3.11, 3.12 or 3.13, then re-run."
}
Say "Installing ShortSmash (this takes a few minutes)..."
& $venvPip install -q --upgrade pip | Out-Null
$BaseExtras = "ai,voice,web,mcp,trends,bluesky,threads,youtube,x,schedule,captions"
$Extras = $BaseExtras
if (-not $SkipVision) { $Extras = "$BaseExtras,vision" }
& $venvPip install -q -e ".[$Extras]" 2>$null
if ($LASTEXITCODE -ne 0) {
  Warn "Full install failed - retrying without the vision extra (torch)."
  & $venvPip install -q -e ".[$BaseExtras]"
  if ($LASTEXITCODE -ne 0) { Fail "pip install failed. Re-run the installer, or paste the error on the ShortSmash repo." }
  $SkipVision = $true
}
Ok "ShortSmash installed"

# ---------- Voices + fonts (Python one-shot) ----------
Say "Downloading neural voices (one time, ~250MB)..."
& $venvPy -c @"
import urllib.request, pathlib
voices = pathlib.Path(r'$InstallDir') / 'voices'; voices.mkdir(exist_ok=True)
base = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/'
files = [
 ('en_US-amy-medium.onnx','en/en_US/amy/medium/en_US-amy-medium.onnx'),
 ('en_US-amy-medium.onnx.json','en/en_US/amy/medium/en_US-amy-medium.onnx.json'),
 ('ur_PK-aegis_female-medium.onnx','ur/ur_PK/aegis_female/medium/ur_PK-aegis_female-medium.onnx'),
 ('ur_PK-aegis_female-medium.onnx.json','ur/ur_PK/aegis_female/medium/ur_PK-aegis_female-medium.onnx.json'),
 ('ur_PK-fasih-medium.onnx','ur/ur_PK/fasih/medium/ur_PK-fasih-medium.onnx'),
 ('ur_PK-fasih-medium.onnx.json','ur/ur_PK/fasih/medium/ur_PK-fasih-medium.onnx.json'),
 ('hi_IN-priyamvada-medium.onnx','hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium.onnx'),
 ('hi_IN-priyamvada-medium.onnx.json','hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium.onnx.json'),
 ('hi_IN-rohan-medium.onnx','hi/hi_IN/rohan/medium/hi_IN-rohan-medium.onnx'),
 ('hi_IN-rohan-medium.onnx.json','hi/hi_IN/rohan/medium/hi_IN-rohan-medium.onnx.json'),
 ('zh_CN-huayan-medium.onnx','zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx'),
 ('zh_CN-huayan-medium.onnx.json','zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json'),
]
for name, rel in files:
    p = voices / name
    if not p.exists() or p.stat().st_size < 1000:
        urllib.request.urlretrieve(base + rel, p)
print('voices ok')
import shutil
font_src = pathlib.Path(r'$InstallDir') / 'fonts' / 'NotoNastaliqUrdu.ttf'
font_dst = pathlib.Path.home() / 'AppData/Local/Microsoft/Windows/Fonts/NotoNastaliqUrdu.ttf'
if font_src.exists() and not font_dst.exists():
    font_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(font_src, font_dst)
    print('font ok')
"@
Ok "Voices + Urdu font ready"

# ---------- First-run init ----------
Say "First-run setup..."
& $venvPy -m shortsmash.cli init 2>$null | Out-Null
Ok "Config initialized"

# ---------- Shortcuts (Desktop + Start Menu) ----------
$launcher = Join-Path $InstallDir "scripts\launch-studio.ps1"
$Wsh = New-Object -ComObject WScript.Shell
foreach ($dir in @([IO.Path]::Combine($HOME,"Desktop"),
                   [IO.Path]::Combine($env:APPDATA,"Microsoft\Windows\Start Menu\Programs"))) {
  $lnk = $Wsh.CreateShortcut((Join-Path $dir "ShortSmash Studio.lnk"))
  $lnk.TargetPath = "powershell.exe"
  $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`""
  $lnk.WorkingDirectory = $InstallDir
  $lnk.Description = "ShortSmash Studio - prompt in, viral short out"
  $lnk.Save()
}
Ok "Shortcuts created (Desktop + Start Menu)"

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Green
Write-Host "   Done! ShortSmash Studio is installed." -ForegroundColor Green
Write-Host "  ============================================" -ForegroundColor Green
Write-Host ""
Say "Launch it anytime from the Desktop or Start Menu: 'ShortSmash Studio'"
Say "Or right now:"
$launch = Read-Host "  Start the Studio now? [Y/n]"
if ($launch -notmatch '^[nN]') {
  & $launcher
}
