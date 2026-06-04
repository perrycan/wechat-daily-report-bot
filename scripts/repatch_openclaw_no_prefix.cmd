@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0repatch_openclaw_no_prefix.ps1" %*
set "_ec=%errorlevel%"
if not "%_ec%"=="0" (
  echo.
  echo Repatch failed with exit code %_ec%.
  exit /b %_ec%
)
echo.
echo Repatch completed.
exit /b 0

