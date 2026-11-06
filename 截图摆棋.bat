@echo off
rem Screenshot-to-SGF GUI launcher (ASCII-only body, safe for any code page)
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0screenshot2board\s2b_gui.py"
) else (
    start "" python "%~dp0screenshot2board\s2b_gui.py"
)
