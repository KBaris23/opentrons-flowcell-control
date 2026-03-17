$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Resolve-PythonExe {
  if ($env:EA_PYTHON -and (Test-Path $env:EA_PYTHON)) { return $env:EA_PYTHON }

  $candidates = @(
    (Join-Path $env:LocalAppData "Programs\\Python\\Python310\\python.exe"),
    (Join-Path $env:LocalAppData "Programs\\Python\\Python311\\python.exe"),
    (Join-Path $env:LocalAppData "Programs\\Python\\Python312\\python.exe")
  )
  foreach ($c in $candidates) {
    if (Test-Path $c) { return $c }
  }

  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }

  throw "Could not find a usable Python. Set EA_PYTHON to a python.exe path."
}

$python = Resolve-PythonExe

if (-not (Test-Path ".venv")) {
  & $python -m venv .venv
}

& .venv\\Scripts\\python.exe -m pip install --upgrade pip
& .venv\\Scripts\\python.exe -m pip install -r requirements.txt

Write-Host "Done. Activate with: .\\.venv\\Scripts\\Activate.ps1"

