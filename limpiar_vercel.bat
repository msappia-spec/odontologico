@echo off
REM ---------------------------------------------------------------------------
REM  Limpia las tablas de la base PostgreSQL (Neon) que usa Vercel.
REM
REM  Vercel no guarda las tablas: la app se conecta a Neon con DATABASE_URL.
REM  El CLI de Vercel marca esa variable como Secret y no puede exportarla, por
REM  eso la cadena se guarda una vez en .env.vercel.local (ignorado por git).
REM
REM  Modos:
REM    1) Operativo: pacientes, turnos, historias, evoluciones, anamnesis,
REM       adjuntos, caja y disponibilidad especifica.
REM       Conserva doctores, obras sociales, prestaciones, aranceles, agenda
REM       semanal y menu del bot.
REM    2) Todo: las 15 tablas. Vuelve a sembrar el menu del FAQ Bot.
REM
REM  Uso:
REM    Doble clic sobre este archivo.
REM    limpiar_vercel.bat --simulacro     (solo muestra conteos, no borra)
REM
REM  La limpieza es irreversible. Hace un backup de Neon antes de usarla en
REM  produccion si esos datos te importan.
REM ---------------------------------------------------------------------------
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

set "ENV_VERCEL=.env.vercel.local"
set "SIMULACRO=0"
if /i "%~1"=="--simulacro" set "SIMULACRO=1"

echo ===========================================================================
echo   LIMPIAR TABLAS DE PRODUCCION (VERCEL / NEON)
echo ===========================================================================
echo.
echo   Esta herramienta BORRA FILAS. No borra tablas ni columnas.
echo   No se puede deshacer desde este programa.
echo.

REM --- Interprete: primero el entorno virtual, despues el del PATH ----------
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY for %%P in (python.exe) do if not "%%~$PATH:P"=="" set "PY=%%~$PATH:P"

if not defined PY (
    echo [ERROR] No se encontro Python en esta computadora.
    echo         Crea el entorno del proyecto con:
    echo             py -m venv .venv
    echo             .venv\Scripts\python -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM --- Cadena de produccion --------------------------------------------------
REM No se usa "vercel env pull": las variables Secret salen como [SENSITIVE].
if not exist "%ENV_VERCEL%" goto configurar
findstr /c:"DATABASE_URL=" "%ENV_VERCEL%" >nul 2>nul
if errorlevel 1 goto configurar
findstr /c:"[SENSITIVE]" "%ENV_VERCEL%" >nul 2>nul
if not errorlevel 1 goto configurar
goto menu

:configurar
echo   No hay una DATABASE_URL valida en %ENV_VERCEL%.
echo.
echo   Copiala desde el panel de Neon (o Vercel - Storage - Neon).
echo   Se va a pedir una sola vez y se guardara en un archivo local
echo   ignorado por git. La entrada no se muestra al escribir.
echo.
"%PY%" -m scripts.configurar_url_vercel --archivo "%ENV_VERCEL%" --forzar
if errorlevel 1 goto error_config
if not exist "%ENV_VERCEL%" goto error_config
findstr /c:"DATABASE_URL=" "%ENV_VERCEL%" >nul 2>nul
if errorlevel 1 goto error_config
echo.

:menu
echo Que queres limpiar?
echo.
echo   1. Datos de trabajo (recomendado)
echo      Pacientes, turnos, historias, evoluciones, anamnesis, adjuntos,
echo      caja y disponibilidad especifica.
echo      Conserva doctores, obras sociales, prestaciones, aranceles, agenda
echo      semanal y menu del bot.
echo.
echo   2. TODAS las tablas
echo      Las 15 tablas, incluidos doctores y catalogos.
echo      Vuelve a sembrar el menu del bot.
echo.
echo   3. Cancelar
echo.
choice /c 123 /n /m "Opcion [1-3]: "
if errorlevel 3 goto cancelar
if errorlevel 2 goto modo_todo
if errorlevel 1 goto modo_operativo
goto menu

:modo_operativo
set "MODO=operativo"
goto simular

:modo_todo
set "MODO=todo"
goto simular

:simular
echo.
echo ===========================================================================
echo   MODO: %MODO%
echo   Paso 1 de 2: simulacro (no borra nada)
echo ===========================================================================
echo.
"%PY%" -m scripts.limpiar_base --env-file "%ENV_VERCEL%" --modo "%MODO%"
set "CODIGO=%ERRORLEVEL%"
if not "%CODIGO%"=="0" goto error_limpieza
if "%SIMULACRO%"=="1" goto fin_simulacro

echo.
echo ===========================================================================
echo   CONFIRMACION
echo ===========================================================================
echo.
echo   Si continuas, se borran las filas del modo "%MODO%" en la base de
echo   produccion. Esta accion es IRREVERSIBLE.
echo.
set "CONFIRMA="
set /p "CONFIRMA=Escribi SI (mayusculas) para borrar: "
if /i not "%CONFIRMA%"=="SI" goto cancelar

echo.
echo ===========================================================================
echo   Paso 2 de 2: borrando...
echo ===========================================================================
echo.
"%PY%" -m scripts.limpiar_base --env-file "%ENV_VERCEL%" --modo "%MODO%" --ejecutar
set "CODIGO=%ERRORLEVEL%"
if not "%CODIGO%"=="0" goto error_limpieza

echo.
echo Listo: la base de produccion quedo limpia en el modo "%MODO%".
goto fin

:fin_simulacro
echo.
echo Simulacro terminado: no se borro nada.
goto fin

:error_config
echo.
echo [ERROR] No se pudo guardar la cadena de produccion.
echo         Tambien podes crear %ENV_VERCEL% a mano con:
echo             DATABASE_URL="postgresql://usuario:clave@host/db?sslmode=require"
echo.
pause
exit /b 1

:error_limpieza
echo.
echo [ERROR] La limpieza no termino bien (codigo %CODIGO%).
echo         Revisa el mensaje de arriba. Si la conexion fallo, comproba
echo         la cadena de %ENV_VERCEL% y que Neon este activo.
echo.
pause
exit /b %CODIGO%

:cancelar
echo.
echo Cancelado: no se borro nada.
goto fin

:fin
echo.
pause
exit /b 0
