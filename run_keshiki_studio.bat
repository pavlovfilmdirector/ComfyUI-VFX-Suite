@echo off
chcp 65001 >nul
title KESHIKI VFX Studio

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set TORCHDYNAMO_SUPPRESS_ERRORS=1
set TORCH_COMPILE_DISABLE=1

cd /d "%~dp0"

:: Python that runs ComfyUI: keshiki_local.json (written by install.bat) > ComfyUI portable > PATH
set "PY="
if exist "keshiki_local.json" (
  for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "(Get-Content -Raw 'keshiki_local.json' | ConvertFrom-Json).python"`) do set "PY=%%P"
)
if not defined PY if exist "%~dp0..\..\..\python_embeded\python.exe" set "PY=%~dp0..\..\..\python_embeded\python.exe"
if not defined PY set "PY=python"

echo Using Python: %PY%
"%PY%" splash_launcher.py

pause
