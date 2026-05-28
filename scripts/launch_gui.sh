#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
cd "$repo_root"

venv_python=""

find_venv_python() {
  if [[ -x ".venv/Scripts/python.exe" ]]; then
    printf '%s\n' ".venv/Scripts/python.exe"
  elif [[ -x ".venv/bin/python" ]]; then
    printf '%s\n' ".venv/bin/python"
  fi
}

create_venv() {
  echo "Creating Python environment..."

  if [[ -n "${EA_PYTHON:-}" && -x "$EA_PYTHON" ]]; then
    "$EA_PYTHON" -m venv .venv
  elif command -v py >/dev/null 2>&1; then
    py -3 -m venv .venv
  elif command -v python >/dev/null 2>&1; then
    python -m venv .venv
  elif command -v python3 >/dev/null 2>&1; then
    python3 -m venv .venv
  else
    echo "Could not find Python. Install Python 3.10 or newer, or set EA_PYTHON to python.exe." >&2
    return 1
  fi
}

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" | awk '{print $NF}'
  else
    echo "Could not find sha256sum, shasum, or openssl to check requirements.txt." >&2
    return 1
  fi
}

venv_python="$(find_venv_python || true)"

if [[ -z "$venv_python" ]]; then
  create_venv
  venv_python="$(find_venv_python || true)"
fi

if [[ -z "$venv_python" ]]; then
  echo "The Python environment was not created correctly. Missing .venv Python executable." >&2
  exit 1
fi

requirements_hash=""
if [[ -f "requirements.txt" ]]; then
  requirements_hash="$(hash_file requirements.txt)"
fi

stamp_file=".venv/.requirements.sha256"
installed_hash=""
if [[ -f "$stamp_file" ]]; then
  installed_hash="$(tr -d '[:space:]' < "$stamp_file")"
fi

if [[ -n "$requirements_hash" && "$requirements_hash" != "$installed_hash" ]]; then
  echo "Installing/updating Python packages..."
  "$venv_python" -m pip install --upgrade pip
  "$venv_python" -m pip install -r requirements.txt
  printf '%s\n' "$requirements_hash" > "$stamp_file"
fi

echo "Starting Opentrons Flowcell Control..."
"$venv_python" main.py
