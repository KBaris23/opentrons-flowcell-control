$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$bash = Get-Command bash.exe -ErrorAction SilentlyContinue
if (-not $bash) {
  throw "Could not find bash.exe. Install Git for Windows, then run this again."
}

& $bash.Source (Join-Path $PSScriptRoot "launch_gui.sh")
