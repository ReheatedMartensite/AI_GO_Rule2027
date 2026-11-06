@echo off
rem Live board watch launcher (ASCII-only body)
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0boardwatch\boardwatch.py"
) else (
    start "" python "%~dp0boardwatch\boardwatch.py"
)
