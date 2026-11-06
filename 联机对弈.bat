@echo off
rem Online play (netplay) GUI launcher (ASCII-only body)
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0netplay\netplay.py"
) else (
    start "" python "%~dp0netplay\netplay.py"
)
