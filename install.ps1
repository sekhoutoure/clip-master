<#
.SYNOPSIS
  CLIP MASTER installer for Windows (PowerShell 5.1+). Safe to run again (idempotent).
.EXAMPLE
  .\install.ps1                  # set everything up
  .\install.ps1 -Check           # doctor: report what is missing, change nothing
  .\install.ps1 -Yes             # also install missing ffmpeg / Node.js with winget, without asking
  .\install.ps1 -WithReference   # also fetch the reference-only sources of third_party\manifest.yml
  .\install.ps1 -CpuTorch        # small CPU-only PyTorch (no GPU)
  .\install.ps1 -Python "C:\Python312\python.exe"
#>
[CmdletBinding()]
param(
  [switch]$Check,
  [switch]$Yes,
  [switch]$WithReference,
  [switch]$CpuTorch,
  [string]$Python = ""
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Say($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "warning: $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "error: $m" -ForegroundColor Red; exit 1 }
function Run($exe, [string[]]$a) {
  & $exe @a
  if ($LASTEXITCODE -ne 0) { Die "'$exe $($a -join ' ')' failed (exit $LASTEXITCODE). Fix the message above and run .\install.ps1 again (it is safe to re-run)." }
}

function Test-PyOk($exe, $strict) {
  try {
    $code = if ($strict) { "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)" } else { "import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)" }
    & $exe -c $code 2>$null
    return ($LASTEXITCODE -eq 0)
  } catch { return $false }
}

function Find-Python {
  if ($Python) { if (Test-Path $Python) { return $Python } else { Die "interpreter '$Python' not found" } }
  $cands = @()
  if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in "3.12", "3.11", "3.10") {
      try { $p = (& py "-$v" -c "import sys; print(sys.executable)" 2>$null); if ($LASTEXITCODE -eq 0 -and $p) { $cands += $p } } catch {}
    }
  }
  foreach ($n in "python3.12", "python3", "python") { $c = Get-Command $n -ErrorAction SilentlyContinue; if ($c) { $cands += $c.Source } }
  foreach ($c in $cands) { if (Test-PyOk $c $true) { return $c } }
  foreach ($c in $cands) { if (Test-PyOk $c $false) { Warn "no Python 3.10-3.12 found, using $c. Some wheels (MediaPipe, PyTorch) may be missing."; return $c } }
  Die "Python 3.10-3.12 is required. Install it: winget install Python.Python.3.12  (then re-run this script)."
}

$VenvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if ($Check) {
  if (Test-Path $VenvPy) { & $VenvPy bootstrap.py --check } else { & (Find-Python) bootstrap.py --check }
  exit $LASTEXITCODE
}

Say "1/5 Python"
$Py = Find-Python
Write-Host "using $Py ($(& $Py --version 2>&1))"

Say "2/5 System tools (ffmpeg, node)"
function Install-Winget($label, $id) {
  if (-not $Yes) { Warn "$label not found. Re-run with -Yes to install it with winget, or install it yourself."; return $false }
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Warn "winget not available: install $label manually."; return $false }
  & winget install --id $id -e --accept-package-agreements --accept-source-agreements
  return ($LASTEXITCODE -eq 0)
}
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { Write-Host "ffmpeg: found" }
else { if (-not (Install-Winget "ffmpeg" "Gyan.FFmpeg")) { Warn "continuing: the bundled imageio-ffmpeg binary will be used (install system ffmpeg for best results)" } else { Warn "ffmpeg installed: open a new terminal if it is not found yet" } }
if (Get-Command node -ErrorAction SilentlyContinue) { Write-Host "node: $(& node --version)" }
else { if (-not (Install-Winget "Node.js (optional, Remotion only)" "OpenJS.NodeJS.LTS")) { Warn "continuing without Node.js: Remotion captions stay disabled" } }

Say "3/5 Virtualenv and Python packages"
if (-not (Test-Path $VenvPy)) { Run $Py @("-m", "venv", ".venv") }
Run $VenvPy @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
if ($CpuTorch) {
  Say "CPU-only PyTorch"
  Run $VenvPy @("-m", "pip", "install", "--quiet", "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cpu")
}
Run $VenvPy @("-m", "pip", "install", "-r", "requirements.txt")
Run $VenvPy @("-m", "pip", "install", "--quiet", "-e", ".", "--no-deps")

Say "4/5 Models, Remotion, third-party tools"
$extra = @(); if ($WithReference) { $extra += "--with-reference" }
Run $VenvPy (@("bootstrap.py") + $extra)

Say "5/5 Done"
Write-Host @"

CLIP MASTER is installed.
  Start:   start.bat          (then open http://127.0.0.1:7866)
  Doctor:  .\install.ps1 -Check
  Config:  edit .env  (GROQ_API_KEY is optional: without it the app uses local Ollama)
"@
