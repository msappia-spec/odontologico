@echo off
REM ---------------------------------------------------------------------------
REM  Publica los cambios del proyecto en un solo paso:
REM    control de calidad (pytest) -> commit -> push a GitHub -> deploy Vercel
REM
REM  El orden lo garantiza scripts/publicar.py: si una prueba falla, no se sube
REM  ni se despliega nada, y antes de tocar git comprueba que la CLI de Vercel
REM  este instalada y que la carpeta este enlazada al proyecto.
REM
REM  Codigos de salida: 0 todo bien, 1 se aborto (no se publico) y 2 se publico
REM  pero el chequeo final de la app marco fallas.
REM
REM  Uso:
REM    Doble clic sobre este archivo, o desde la consola:
REM      publicar.bat
REM      publicar.bat -m "Arregla el panel de turnos"
REM      publicar.bat --simulacro
REM      publicar.bat --solo-deploy          (reintenta el deploy y su chequeo)
REM      publicar.bat --sin-tests --sin-verificar
REM    Todas las opciones:  python -m scripts.publicar --help
REM
REM  Con doble clic la ventana espera una tecla al final (para poder leer el
REM  resumen). Al llamarlo desde otra consola o desde un script conviene
REM  definir PUBLICAR_SIN_PAUSA=1 para que termine solo.
REM ---------------------------------------------------------------------------
setlocal
REM Guarda la pagina de codigos de la consola para devolverla al terminar.
set "CP_ANTERIOR="
for /f "tokens=2 delims=:" %%C in ('chcp') do set "CP_ANTERIOR=%%C"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

REM --- Pausa final: no se fuerza cuando lo pide el entorno -------------------
set "PAUSA=pause"
if defined PUBLICAR_SIN_PAUSA set "PAUSA="

REM --- Interprete: primero el entorno virtual, despues el del PATH ----------
set "PY="
set "PY_ARGS="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY for %%P in (python.exe) do if not "%%~$PATH:P"=="" set "PY=%%~$PATH:P"

REM Sin python.exe se usa el lanzador "py" que instala Python en Windows.
if not defined PY (
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        set "PY=py"
        set "PY_ARGS=-3"
    )
)

if not defined PY (
    echo.
    echo [ERROR] No se encontro Python en esta computadora.
    echo         Crea el entorno del proyecto con:
    echo             py -m venv .venv
    echo             .venv\Scripts\python -m pip install -r requirements.txt
    echo         y volve a intentar.
    echo.
    set "CODIGO=1"
    goto fin
)

"%PY%" %PY_ARGS% -c "import sys" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] El interprete "%PY% %PY_ARGS%" no responde.
    echo         Borra la carpeta .venv y volve a crearla: mira el README.
    echo.
    set "CODIGO=1"
    goto fin
)

echo Publicando con: %PY% %PY_ARGS%
echo.

"%PY%" %PY_ARGS% -m scripts.publicar %*
set "CODIGO=%ERRORLEVEL%"

echo.
if "%CODIGO%"=="0" goto todo_listo
if "%CODIGO%"=="2" goto publicado_con_fallas
echo La publicacion no termino bien ^(codigo %CODIGO%^).
echo Revisa el detalle de arriba. Si el resumen alcanzo a mostrar el commit o
echo el push, GitHub puede haber quedado actualizado aunque el paso siguiente
echo haya fallado. La ayuda esta en:
echo     "%PY%" %PY_ARGS% -m scripts.publicar --help
goto fin

:todo_listo
echo Terminado sin errores: revisa arriba el resumen ^(rama, commit y URL^).
echo Si esas lineas no aparecen ^(por ejemplo con --help o --simulacro^) es
echo porque no se publico nada.
goto fin

:publicado_con_fallas
echo Ojo: el codigo si quedo en GitHub y en Vercel, pero el chequeo final de
echo la app marco fallas ^(las lineas de error del resumen^).
echo Para reintentar solo el deploy y la verificacion:
echo     "%PY%" %PY_ARGS% -m scripts.publicar --solo-deploy

:fin
%PAUSA%
if defined CP_ANTERIOR chcp %CP_ANTERIOR% >nul 2>nul
exit /b %CODIGO%


