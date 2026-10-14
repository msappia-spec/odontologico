@echo off
REM ---------------------------------------------------------------------------
REM  Publica los cambios del proyecto en un solo paso:
REM    pruebas (pytest) -> commit -> push a GitHub -> deploy en Vercel
REM
REM  Uso:
REM    Doble clic sobre este archivo, o desde la consola:
REM      publicar.bat
REM      publicar.bat -m "Arregla el panel de turnos"
REM      publicar.bat --simulacro
REM      publicar.bat --sin-tests --sin-verificar
REM
REM  Documentacion:  python -m scripts.publicar --help
REM ---------------------------------------------------------------------------
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

"%PY%" -m scripts.publicar %*
set "CODIGO=%ERRORLEVEL%"

echo.
if "%CODIGO%"=="0" (
    echo Listo.
) else (
    echo La publicacion termino con errores ^(codigo %CODIGO%^).
)
pause
exit /b %CODIGO%
