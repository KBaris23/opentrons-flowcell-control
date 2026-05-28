$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Import-DotEnvFile {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return }
  Get-Content -LiteralPath $Path | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $eq = $line.IndexOf("=")
    if ($eq -lt 1) { return }
    $key = $line.Substring(0, $eq).Trim()
    $val = $line.Substring($eq + 1).Trim()
    if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
      $val = $val.Substring(1, $val.Length - 2)
    }
    [Environment]::SetEnvironmentVariable($key, $val)
  }
}

Import-DotEnvFile (Join-Path $repoRoot ".env")
Import-DotEnvFile (Join-Path $repoRoot ".env.local")

$bash = Get-Command bash.exe -ErrorAction SilentlyContinue
if (-not $bash) {
  throw "Could not find bash.exe. Install Git for Windows, then run this again."
}

& $bash.Source (Join-Path $PSScriptRoot "launch_gui.sh")
