@echo off
REM ---------------------------------------------------------------------------
REM  Levanta la aplicacion (FastAPI + panel web) en esta computadora.
REM
REM  Levanta Uvicorn con el Python del .venv, abierto a la red local para que
REM  tambien se pueda entrar desde el celular. La ventana queda ocupada
REM  mostrando los registros: mientras este abierta, la app esta andando.
REM
REM  Para detenerla: Ctrl+C en esta ventana.
REM
REM  Uso:
REM    Doble clic sobre este archivo, o desde la consola:
REM      ejecutar.bat                 host 0.0.0.0 y puerto 8000
REM      ejecutar.bat 8080            otro puerto (1-65535; "08000" se lee 8000)
REM      ejecutar.bat --local         solo desde esta PC (127.0.0.1)
REM      ejecutar.bat --reload        recarga al guardar cambios (desarrollo)
REM      ejecutar.bat --abrir         abre el panel en el navegador al arrancar
REM      ejecutar.bat --help          muestra esta ayuda
REM
REM  Antes de arrancar verifica el interprete, las dependencias y que el puerto
REM  este libre; si ya hay algo escuchando consulta /health para distinguir si
REM  es esta app (y ofrece abrir el panel) o si lo ocupa otro programa. Tambien
REM  avisa si el .env no define DATABASE_URL.
REM
REM  Documentacion: README.md ("Puesta en marcha") y "como ejecutarlo.txt".
REM ---------------------------------------------------------------------------
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

set "PUERTO=8000"
set "HOST=0.0.0.0"
set "RELOAD=0"
set "ABRIR=0"

REM --- Opciones de la linea de comandos --------------------------------------
:leer_args
if "%~1"=="" goto args_listos
if /i "%~1"=="--help" goto ayuda
if /i "%~1"=="-h" goto ayuda
if /i "%~1"=="/?" goto ayuda
if /i "%~1"=="--reload" (set "RELOAD=1" & shift & goto leer_args)
if /i "%~1"=="--abrir" (set "ABRIR=1" & shift & goto leer_args)
if /i "%~1"=="--local" (set "HOST=127.0.0.1" & shift & goto leer_args)
echo %~1| findstr /r "^[0-9][0-9]*$" >nul
if errorlevel 1 (
    echo.
    echo [ERROR] Opcion desconocida: %~1
    goto ayuda
)
set "PUERTO=%~1"
call :normalizar_puerto
if "%PUERTO_MAL%"=="1" goto puerto_invalido
shift
goto leer_args

:ayuda
echo.
echo   Uso:  ejecutar.bat [puerto] [opciones]
echo.
echo     puerto        puerto donde escucha la app, 1-65535 (por defecto 8000)
echo     --local       escucha solo en 127.0.0.1 (no queda abierta a la red)
echo     --reload      recarga el codigo al guardar cambios (desarrollo)
echo     --abrir       abre el panel en el navegador al arrancar
echo     --help        muestra esta ayuda
echo.
echo   Ejemplos:
echo     ejecutar.bat
echo     ejecutar.bat 8080 --abrir
echo     ejecutar.bat --local --reload
echo.
pause
exit /b 1

:args_listos

REM --- Interprete: primero el entorno virtual, despues el del PATH ----------
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY for %%P in (python.exe) do if not "%%~$PATH:P"=="" set "PY=%%~$PATH:P"

if not defined PY goto error_python
"%PY%" -c "import sys" >nul 2>nul
if errorlevel 1 goto error_python

REM --- Archivos y dependencias del proyecto ---------------------------------
if not exist "app\main.py" goto error_carpeta
"%PY%" -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 goto error_dependencias

REM --- Aviso de base de datos -----------------------------------------------
REM El .env local apunta a PostgreSQL. Sin .env (o sin DATABASE_URL) la app cae
REM en el SQLite por defecto y el panel aparece vacio; conviene avisarlo antes
REM de arrancar en lugar de descubrirlo con la secretaria esperando datos que
REM estan en otra base.
set "AVISO_DB="
if not exist ".env" (
    set "AVISO_DB=1"
) else (
    findstr /b /c:"DATABASE_URL=" ".env" >nul 2>nul
    if errorlevel 1 set "AVISO_DB=1"
)

REM --- El puerto tiene que estar libre --------------------------------------
REM Se prueba con un socket: no depende del idioma de Windows ni de netstat.
REM Se usa create_connection y no connect_ex sobre un socket con timeout:
REM connect_ex puede devolver WSAEWOULDBLOCK (10035) mientras la conexion esta
REM "en curso", un valor que no distingue "no hay nadie escuchando" de
REM "todavia no se sabe". create_connection espera la respuesta real y falla
REM con "conexion rechazada" cuando el puerto esta libre.
"%PY%" -c "import socket; socket.create_connection(('127.0.0.1', %PUERTO%), 0.5).close()" >nul 2>nul
if not errorlevel 1 goto puerto_ocupado

REM --- Arranque --------------------------------------------------------------
:arrancar
echo ===========================================================================
echo   FRANCIOSI ODONTOLOGIA - SERVIDOR LOCAL
echo ===========================================================================
echo.
echo   Panel (secretaria) ...  http://127.0.0.1:%PUERTO%/
echo   Agenda movil .........  http://127.0.0.1:%PUERTO%/medico
echo   Documentacion API ....  http://127.0.0.1:%PUERTO%/docs   (incluye /api/v1/egresos)
echo   Estado del servicio ..  http://127.0.0.1:%PUERTO%/health
echo.
if /i "%HOST%"=="0.0.0.0" (
    echo   Desde el celular u otra PC de la misma red Wi-Fi:
    for /f "tokens=2 delims=:" %%I in ('ipconfig ^| findstr /c:"IPv4"') do (
        for /f "tokens=1" %%J in ("%%I") do echo       http://%%J:%PUERTO%/medico
    )
    echo.
    echo   Si el telefono no carga la pagina, el Firewall de Windows esta
    echo   bloqueando el puerto %PUERTO%: miralo en "como ejecutarlo.txt".
    echo.
)
if defined DATABASE_URL (
    echo   [AVISO] DATABASE_URL esta definida en este entorno: la app usara esa
    echo           base y no la del archivo .env. Ver README.md.
    echo.
)
if defined AVISO_DB (
    echo   [AVISO] No hay DATABASE_URL en .env: la app usara el SQLite por
    echo           defecto y el panel puede quedar sin datos. Copia .env.example
    echo           a .env y configura la conexion ^(ver README.md^).
    echo.
)
echo   Interprete: %PY%
echo   Escuchando: %HOST%:%PUERTO%   (reload=%RELOAD%)
echo ---------------------------------------------------------------------------
echo   La ventana queda ocupada: para detener la app, Ctrl+C.
echo ---------------------------------------------------------------------------
echo.

if "%ABRIR%"=="1" start "" /min cmd /c "timeout /t 5 >nul & rundll32 url.dll,FileProtocolHandler http://127.0.0.1:%PUERTO%/"

set "ARGS=app.main:app --host %HOST% --port %PUERTO%"
if "%RELOAD%"=="1" set "ARGS=%ARGS% --reload"
"%PY%" -m uvicorn %ARGS%
set "CODIGO=%ERRORLEVEL%"

echo.
if not "%CODIGO%"=="0" goto error_servidor
echo La aplicacion se detuvo. Para volver a levantarla, ejecuta este archivo otra vez.
goto fin

:error_servidor
echo [ERROR] El servidor termino con codigo %CODIGO%.
echo         Revisa el mensaje de arriba. Si el puerto quedo tomado, proba:
echo             ejecutar.bat 8080
echo         Si el error menciona la base de datos, revisa DATABASE_URL en .env
echo         y que PostgreSQL este iniciado ^(el .env local usa PostgreSQL^).
goto fin

REM --- Errores de preparacion ----------------------------------------------
:error_python
echo.
echo [ERROR] No se encontro un Python utilizable.
echo         Si existe la carpeta .venv, borrala y volve a crearla:
echo             py -m venv .venv
echo             .venv\Scripts\python -m pip install -r requirements.txt
echo.
pause
exit /b 1

:error_carpeta
echo.
echo [ERROR] No se encontro app\main.py.
echo         Ejecuta este archivo desde la carpeta del proyecto.
echo.
pause
exit /b 1

:error_dependencias
echo.
echo [ERROR] Faltan dependencias en "%PY%".
echo         Instalalas con:
echo             "%PY%" -m pip install -r requirements.txt
echo.
pause
exit /b 1

:puerto_invalido
echo.
echo [ERROR] Puerto invalido.
echo         Usa un numero entero entre 1 y 65535, por ejemplo:
echo             ejecutar.bat 8080
echo.
pause
exit /b 1

:puerto_ocupado
REM Antes de ofrecer abrir el panel se consulta /health en ese puerto: si lo
REM que escucha es otro programa, abrir el navegador a ciegas mostraba una
REM pagina ajena y parecia que el panel estaba roto.
set "RESPONDE=0"
"%PY%" -c "import json,sys,urllib.request as u; sys.exit(0 if json.load(u.urlopen('http://127.0.0.1:%PUERTO%/health', timeout=3)).get('status')=='ok' else 1)" >nul 2>nul
if not errorlevel 1 set "RESPONDE=1"

echo.
echo ===========================================================================
echo   EL PUERTO %PUERTO% YA ESTA EN USO
echo ===========================================================================
echo.
if "%RESPONDE%"=="1" (
    echo   La aplicacion ya esta andando en otra ventana:
    echo       http://127.0.0.1:%PUERTO%/
) else (
    echo   En ese puerto no responde esta app: lo ocupa otro programa o una
    echo   instancia que todavia esta arrancando ^(init_db puede tardar^).
)
echo.
echo   1. Abrir el panel en el navegador
echo   2. Cancelar y levantar en otro puerto:  ejecutar.bat 8080
echo.
set "CODIGO=1"
choice /c 12 /n /m "Opcion [1-2]: "
if errorlevel 2 goto fin
start "" "http://127.0.0.1:%PUERTO%/"
set "CODIGO=0"
goto fin

:fin
echo.
pause
exit /b %CODIGO%

REM --- Validacion del puerto --------------------------------------------------
REM ``findstr`` acepta cualquier secuencia de digitos, incluidos "08000" (ceros
REM a la izquierda) y valores fuera de rango como "70000". Los ceros se recortan
REM porque el literal "08000" es un SyntaxError en Python: el chequeo del puerto
REM terminaba en "libre" sin haber probado nada y uvicorn lo interpretaba como
REM 8000. Fuera de 1-65535 ni siquiera se puede hacer bind: el servidor
REM arrancaba, tocaba la base y moria con un traceback largo y confuso.
:normalizar_puerto
setlocal EnableDelayedExpansion
set "P=%PUERTO%"
set "MAL=0"
:quitar_ceros
if not "!P:~0,1!"=="0" goto rango
set "P=!P:~1!"
goto quitar_ceros
:rango
if "!P!"=="" set "MAL=1"
if not "!P:~5!"=="" set "MAL=1"
if "!MAL!"=="0" if !P! LSS 1 set "MAL=1"
if "!MAL!"=="0" if !P! GTR 65535 set "MAL=1"
endlocal & set "PUERTO=%P%" & set "PUERTO_MAL=%MAL%"
exit /b 0
