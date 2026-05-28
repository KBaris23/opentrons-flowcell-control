@echo off
setlocal
title Opentrons Flowcell Control Launcher

set "HERE=%~dp0"
set "APPDIR="

if exist "%HERE%main.py" if exist "%HERE%scripts\launch_gui.sh" set "APPDIR=%HERE%"
if not defined APPDIR if exist "%HERE%opentrons-flowcell-control\main.py" if exist "%HERE%opentrons-flowcell-control\scripts\launch_gui.sh" set "APPDIR=%HERE%opentrons-flowcell-control\"

if not defined APPDIR (
    echo [ERROR] Could not find the opentrons-flowcell-control app folder.
    echo.
    echo Put this launcher either:
    echo   1. inside the opentrons-flowcell-control folder, or
    echo   2. one folder above opentrons-flowcell-control.
    echo.
    pause
    exit /b 1
)

set "GIT_BASH_EXE="
set "GIT_BASH_BIN="

if exist "%ProgramFiles%\Git\git-bash.exe" set "GIT_BASH_EXE=%ProgramFiles%\Git\git-bash.exe"
if not defined GIT_BASH_EXE if exist "%LocalAppData%\Programs\Git\git-bash.exe" set "GIT_BASH_EXE=%LocalAppData%\Programs\Git\git-bash.exe"
if exist "%ProgramFiles%\Git\bin\bash.exe" set "GIT_BASH_BIN=%ProgramFiles%\Git\bin\bash.exe"
if not defined GIT_BASH_BIN if exist "%LocalAppData%\Programs\Git\bin\bash.exe" set "GIT_BASH_BIN=%LocalAppData%\Programs\Git\bin\bash.exe"
if not defined GIT_BASH_BIN if defined GIT_BASH_EXE set "GIT_BASH_BIN=%GIT_BASH_EXE:git-bash.exe=bin\bash.exe%"

if not defined GIT_BASH_BIN (
    echo [ERROR] Could not find Git Bash.
    echo.
    echo Install Git for Windows, then double-click this launcher again.
    echo.
    pause
    exit /b 1
)

start "Opentrons Flowcell Control" "%GIT_BASH_BIN%" --login -i -c "cd '%APPDIR:\=/%' && ./scripts/launch_gui.sh; ec=$?; if [ $ec -ne 0 ]; then echo; echo 'Launcher detected an error (exit' $ec '). Press Enter to close...'; read -r; fi"

exit /b 0
