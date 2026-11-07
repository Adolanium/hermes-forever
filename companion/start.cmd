@echo off
rem Double-click to run the HermesWoW companion in its own window.
start "HermesWoW companion" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
