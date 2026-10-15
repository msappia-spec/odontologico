@echo off
REM ---------------------------------------------------------------------------
REM  Publica los cambios del proyecto en un solo paso:
REM    control de calidad (pytest) -> commit -> push a GitHub -> deploy Vercel
REM
REM  El orden lo garantiza scripts/publicar.py: si una prueba falla, no se sube
REM  ni se despliega nada.
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
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

REM --- Interprete: primero el entorno virtual, despues el del PATH ----------
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY for %%P in (python.exe) do if not "%%~$PATH:P"=="" set "PY=%%~$PATH:P"

if not defined PY (
    echo.
    echo [ERROR] No se encontro Python en esta computadora.
    echo         Crea el entorno del proyecto con:
    echo             py -m venv .venv
    echo             .venv\Scripts\python -m pip install -r requirements.txt
    echo         y volve a intentar.
    echo.
    pause
    exit /b 1
)

"%PY%" -c "import sys" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] El interprete "%PY%" no responde.
    echo         Borra la carpeta .venv y volve a crearla: mira el README.
    echo.
    pause
    exit /b 1
)

echo Publicando con: %PY%
echo.

"%PY%" -m scripts.publicar %*
set "CODIGO=%ERRORLEVEL%"

echo.
if "%CODIGO%"=="0" (
    echo Listo: mira arriba el resumen con la rama, el commit y la URL.
) else (
    echo La publicacion se detuvo sin subir nada ^(codigo %CODIGO%^).
    echo Revisa el detalle de arriba; la ayuda esta en:
    echo     "%PY%" -m scripts.publicar --help
)
echo.
pause
exit /b %CODIGO%

