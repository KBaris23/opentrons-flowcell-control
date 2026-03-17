$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Test-Path ".venv\\Scripts\\python.exe")) {
  throw "Missing .venv. Run scripts\\setup_venv.ps1 first."
}

& .venv\\Scripts\\python.exe main.py

