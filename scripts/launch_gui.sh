#!/usr/bin/env bash
set -euo pipefail

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_DIM=$'\033[2m'
  C_CYAN=$'\033[36m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'
else
  C_RESET=""
  C_BOLD=""
  C_DIM=""
  C_CYAN=""
  C_GREEN=""
  C_YELLOW=""
  C_RED=""
fi

log_line() { printf '%b\n' "$1"; }
log_header() { log_line "${C_BOLD}${C_CYAN}==> $1${C_RESET}"; }
log_ok() { log_line "${C_GREEN}[OK]${C_RESET} $1"; }
log_warn() { log_line "${C_YELLOW}[WARN]${C_RESET} $1"; }
log_err() { log_line "${C_RED}[ERROR]${C_RESET} $1" >&2; }
log_info() { log_line "${C_DIM}$1${C_RESET}"; }

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
cd "$repo_root"

# Load optional local environment config so Slack/ngrok settings work
# when launching by double-clicking the .cmd file.
for env_file in ".env" ".env.local"; do
  if [[ -f "$env_file" ]]; then
    log_info "Loading environment from $env_file"
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  fi
done

venv_python=""

find_venv_python() {
  if [[ -x ".venv/Scripts/python.exe" ]]; then
    printf '%s\n' ".venv/Scripts/python.exe"
  elif [[ -x ".venv/bin/python" ]]; then
    printf '%s\n' ".venv/bin/python"
  fi
}

create_venv() {
  log_header "Creating Python environment"

  if [[ -n "${EA_PYTHON:-}" && -x "$EA_PYTHON" ]]; then
    "$EA_PYTHON" -m venv .venv
  elif command -v py >/dev/null 2>&1; then
    py -3 -m venv .venv
  elif command -v python >/dev/null 2>&1; then
    python -m venv .venv
  elif command -v python3 >/dev/null 2>&1; then
    python3 -m venv .venv
  else
    log_err "Could not find Python. Install Python 3.10 or newer, or set EA_PYTHON to python.exe."
    return 1
  fi
  log_ok "Environment created at .venv"
}

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" | awk '{print $NF}'
  else
    log_err "Could not find sha256sum, shasum, or openssl to check requirements.txt."
    return 1
  fi
}

venv_python="$(find_venv_python || true)"

if [[ -z "$venv_python" ]]; then
  create_venv
  venv_python="$(find_venv_python || true)"
fi

if [[ -z "$venv_python" ]]; then
  log_err "The Python environment was not created correctly. Missing .venv Python executable."
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
  log_header "Installing/updating Python packages"
  "$venv_python" -m pip install --upgrade pip
  "$venv_python" -m pip install -r requirements.txt
  printf '%s\n' "$requirements_hash" > "$stamp_file"
  log_ok "Python packages are up to date"
elif [[ -n "$requirements_hash" ]]; then
  log_ok "Python packages already match requirements.txt"
else
  log_warn "No requirements.txt found; skipping dependency install"
fi

log_header "Starting Opentrons Flowcell Control"
log_info "Using Python: $venv_python"

# Ensure Tcl/Tk is discoverable for tkinter on Windows Git Bash launches.
if [[ -z "${TCL_LIBRARY:-}" || -z "${TK_LIBRARY:-}" ]]; then
  py_base="$("$venv_python" -c 'import sys; print(sys.base_prefix)' 2>/dev/null || true)"
  if [[ -n "$py_base" ]]; then
    py_base_posix="$(printf '%s' "$py_base" | sed 's#\\#/#g')"
    if [[ -z "${TCL_LIBRARY:-}" ]]; then
      for cand in "$py_base_posix/tcl/tcl8.6" "$py_base_posix/tcl/tcl8.7"; do
        if [[ -f "$cand/init.tcl" ]]; then
          export TCL_LIBRARY="$cand"
          break
        fi
      done
    fi
    if [[ -z "${TK_LIBRARY:-}" ]]; then
      for cand in "$py_base_posix/tcl/tk8.6" "$py_base_posix/tcl/tk8.7"; do
        if [[ -f "$cand/tk.tcl" ]]; then
          export TK_LIBRARY="$cand"
          break
        fi
      done
    fi
  fi
fi

"$venv_python" main.py
