@echo off
rem Daily fetch entry - called by Windows Task Scheduler (game-deal-radar-daily).
rem
rem Keep this file ASCII-only. cmd.exe parses .cmd in the OEM codepage, and the
rem previous version had Chinese comments that got mangled into invalid commands
rem (task exit code 9009, data stale since 2026-11-15). Any non-ASCII byte here
rem is a landmine - put explanations in README.md instead.
rem
rem Why python is an absolute path: the scheduled task runs with a different
rem environment than an interactive shell, and bare "python" was not found there.
chcp 65001 >nul
cd /d "%~dp0"
if not exist data mkdir data

rem --once --rounds 1: run one pass then exit (the program itself decides
rem whether today is a Monday full refresh or a weekday light refresh).
rem Use %LOCALAPPDATA% instead of a hardcoded C:\Users\<name> path so the repo
rem stays free of local account names; fall back to PATH if that copy is gone.
set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m radar.cli daemon --once --rounds 1 >> "data\daily.log" 2>&1
exit /b %errorlevel%
