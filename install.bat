@echo off
chcp 65001 >nul
title KESHIKI VFX Studio - install
cd /d "%~dp0"

:: Use the Python that runs ComfyUI: portable build > keshiki_local.json > PATH
set "PY="
if exist "%~dp0..\..\..\python_embeded\python.exe" set "PY=%~dp0..\..\..\python_embeded\python.exe"
if not defined PY if exist "keshiki_local.json" (
  for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "(Get-Content -Raw 'keshiki_local.json' | ConvertFrom-Json).python"`) do set "PY=%%P"
)
if not defined PY set "PY=python"

"%PY%" install.py %*
pause
