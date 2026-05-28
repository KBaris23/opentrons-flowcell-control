@echo off
setlocal EnableDelayedExpansion

set "HERE=%~dp0"
set "APPDIR="

if exist "%HERE%main.py" if exist "%HERE%scripts\launch_gui.sh" set "APPDIR=%HERE%"
if not defined APPDIR if exist "%HERE%opentrons-flowcell-control\main.py" if exist "%HERE%opentrons-flowcell-control\scripts\launch_gui.sh" set "APPDIR=%HERE%opentrons-flowcell-control\"

if not defined APPDIR (
    echo Could not find the opentrons-flowcell-control app folder.
    echo.
    echo Put this launcher either:
    echo   1. inside the opentrons-flowcell-control folder, or
    echo   2. one folder above opentrons-flowcell-control.
    echo.
    pause
    exit /b 1
)

set "BASH_EXE="
set "BASH_MODE=git"

if exist "%ProgramFiles%\Git\bin\bash.exe" set "BASH_EXE=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH_EXE if exist "%ProgramFiles%\Git\usr\bin\bash.exe" set "BASH_EXE=%ProgramFiles%\Git\usr\bin\bash.exe"
if not defined BASH_EXE if exist "%LocalAppData%\Programs\Git\bin\bash.exe" set "BASH_EXE=%LocalAppData%\Programs\Git\bin\bash.exe"

if not defined BASH_EXE (
    where bash.exe >nul 2>nul
    if "%ERRORLEVEL%"=="0" for /f "delims=" %%B in ('where bash.exe') do if not defined BASH_EXE set "BASH_EXE=%%B"
)

echo %BASH_EXE% | findstr /I "\\Windows\\System32\\bash.exe" >nul
if "%ERRORLEVEL%"=="0" set "BASH_MODE=wsl"

if not defined BASH_EXE (
    echo Could not find bash.exe.
    echo.
    echo Install Git for Windows, then double-click this launcher again.
    echo.
    pause
    exit /b 1
)

set "LAUNCH_SCRIPT=%APPDIR%scripts\launch_gui.sh"
set "LAUNCH_SCRIPT=%LAUNCH_SCRIPT:\=/%"

if "%BASH_MODE%"=="wsl" (
    set "DRIVE=!LAUNCH_SCRIPT:~0,1!"
    set "PATH_AFTER_DRIVE=!LAUNCH_SCRIPT:~2!"
    if /I "!DRIVE!"=="C" set "DRIVE=c"
    if /I "!DRIVE!"=="D" set "DRIVE=d"
    set "LAUNCH_SCRIPT=/mnt/!DRIVE!!PATH_AFTER_DRIVE!"
)

"%BASH_EXE%" "%LAUNCH_SCRIPT%"
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo The app closed with an error code: %EXITCODE%
    echo Leave this window open and send the message above to the developer.
    echo.
    pause
)

exit /b %EXITCODE%
