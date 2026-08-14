@echo off
REM Install summation-cli (sumcli) from cmd.exe by invoking the PowerShell bootstrap.
REM Usage:
REM   powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://install.summation.com/sumcli.ps1 | iex"
REM Or download this file from https://install.summation.com/sumcli.cmd and run it.
setlocal EnableExtensions
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $here=Split-Path -Parent '%~f0'; $local=Join-Path $here 'install.ps1'; $docs=Join-Path $here 'sumcli.ps1'; if (Test-Path -LiteralPath $local) { & $local } elseif (Test-Path -LiteralPath $docs) { & $docs } else { irm https://install.summation.com/sumcli.ps1 | iex }"
exit /b %ERRORLEVEL%
