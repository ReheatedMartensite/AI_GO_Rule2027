@echo off
rem Replaced by BoardWatch bridge (2026-11-01): forwards the ReadBoard
rem sync port to BoardWatch. Original launcher: readboard.bat.orig
rem Original engine: readboard.exe (renamed to readboard.exe.bak)
set BWDIR=%~dp0..\boardwatch
if not exist "%BWDIR%\tmp" mkdir "%BWDIR%\tmp"
> "%BWDIR%\tmp\lizzie_port.txt" echo %7
