$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment not found at .venv. Create it first with: python -m venv .venv"
}

Push-Location $ProjectRoot
try {
    & $VenvPython "ui.py"
}
finally {
    Pop-Location
}
