@echo off
REM Install summation-cli (sumcli) from cmd.exe by invoking the PowerShell bootstrap.
REM Usage:
REM   powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://install.summation.com/sumcli.ps1 | iex"
REM Or download this file from https://install.summation.com/sumcli.cmd and run it.
setlocal EnableExtensions
echo Fetching https://install.summation.com/sumcli.ps1
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://install.summation.com/sumcli.ps1 | iex"
exit /b %ERRORLEVEL%
