"""Publica los cambios del proyecto: pruebas, commit, push a GitHub y Vercel.

Contexto
--------
Subir una modificación a producción son varios pasos manuales que hay que
repetir siempre en el mismo orden (probar, *commitear*, empujar al repositorio
y desplegar en Vercel). Este script los encadena y **corta al primer fallo**,
de modo que nunca se publica código sin probar ni quedan *commits* locales sin
subir por olvido.

Qué hace (siempre en este orden)
--------------------------------
1. **Control de calidad**: corre las pruebas con el mismo intérprete que ejecuta
   el script (``python -m pytest``). Si **al menos un test falla** (código de
   salida distinto de 0) se aborta **de inmediato**: no se toca git ni Vercel y
   el motivo se imprime en **rojo** («no se publica código con errores»), junto
   con las pruebas que fallaron.
2. **Control de versiones**: comprueba que estamos en la raíz del repositorio,
   que la rama tiene nombre (si el ``HEAD`` está *detached*, avisa y no hace
   nada) y que existe el remoto ``origin``. Después agrega todo (``git add -A``,
   equivalente a ``git add .`` desde la raíz e incluye borrados), *commitea*
   (con el mensaje de ``-m`` o uno automático con la fecha) y empuja la rama
   (``git push origin <rama>``) para respaldar el código en GitHub.
3. **Prerrequisitos del deploy**: antes de tocar git se comprueba que la CLI de
   Vercel esté instalada y que la carpeta esté enlazada al proyecto. Un problema
   de Vercel no puede dejar la publicación **a medias** (GitHub al día y Vercel
   con código viejo), así que se corta antes de *commitear*. En ``--simulacro``
   sólo se informa.
4. **Despliegue**: corre la CLI de Vercel (``vercel --prod --yes``) **capturando
   su salida**, la muestra al terminar, extrae el dominio de producción que
   informa la propia CLI y lo usa para verificar la app publicada (``/health``,
   el panel y ``/medico``). Un ``401``/``403`` se informa como *aviso* (es la
   *Deployment Protection* de Vercel, no un error de la app).

Si no hay cambios ni *commits* pendientes, no se *commitea* (git lo rechazaría)
ni se despliega: en ese caso se informa y termina. ``--forzar`` sirve para
desplegar igual (por ejemplo después de cambiar una variable de entorno).

Códigos de salida
-----------------
``0`` salió todo bien, ``1`` se abortó (no se publicó nada) y ``2`` se publicó
pero el chequeo final de la app marcó fallas. ``publicar.bat`` escribe su mensaje
final según ese código, por eso la diferencia importa: con ``2`` el código **sí**
quedó en GitHub y en Vercel. Para reintentar sólo el deploy y la verificación::

    python -m scripts.publicar --solo-deploy

La consola de Windows se prepara sola (página de códigos UTF-8 y secuencias ANSI)
para que los acentos, los ✓ y los mensajes en rojo se vean limpios; con
``NO_COLOR`` o ``--sin-colores`` los mensajes van sin códigos de color (útil para
redirigir la salida a un archivo o para terminales viejas).

Uso (desde la raíz del proyecto, con el venv activo)::

    python -m scripts.publicar                       # pytest + commit + push + deploy
    python -m scripts.publicar -m "Arregla el panel" # mensaje de commit propio
    python -m scripts.publicar --sin-tests           # saltea las pruebas
    python -m scripts.publicar --sin-deploy          # sólo GitHub
    python -m scripts.publicar --solo-deploy         # sólo Vercel (sin git)
    python -m scripts.publicar --simulacro           # informa sin escribir nada
    python -m scripts.publicar --sin-colores         # logs sin colores ANSI

En Windows hay además ``publicar.bat`` (doble clic) que llama a este módulo con
el intérprete del entorno virtual.

Controles: ``tests/test_publicar.py`` (ejecutor de comandos y cliente HTTP
falsos: las pruebas no corren git, ni vercel, ni tocan la red).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

# Raíz del proyecto (este archivo vive en scripts/).
RAIZ = Path(__file__).resolve().parents[1]

# App desplegada que se verifica al final (se puede cambiar con --url).
URL_POR_DEFECTO = "https://odontologico-omega.vercel.app"

# Rutas que se consultan para confirmar que el deploy quedó sano.
RUTAS_VERIFICADAS = ("/health", "/", "/medico")

# Ramas válidas para publicar (git devuelve "HEAD" cuando está detached).
RAMAS_INVALIDAS = {"", "HEAD"}

# Remoto al que se empuja (el que se usa en los ejemplos de la documentación).
REMOTO = "origin"

# Carpeta donde la CLI de Vercel guarda el enlace con el proyecto.
CARPETA_VERCEL = ".vercel"

# Nombres del archivo de enlace (`repo.json` lo crea `vercel link --repo`).
ARCHIVOS_DE_ENLACE = ("project.json", "repo.json")

# Variables que enlazan el proyecto sin depender de la carpeta .vercel (CI).
VARIABLES_DE_ENLACE = ("VERCEL_PROJECT_ID", "VERCEL_ORG_ID")

# Subcomandos que sólo leen: se ejecutan incluso en modo simulacro.
SUBCOMANDOS_SOLO_LECTURA = {
    "rev-parse",
    "status",
    "rev-list",
    "log",
    "diff",
    "ls",
    "remote",
}

# Asunto del commit automático (cuando no se pasa ``-m``).
MENSAJE_AUTOMATICO = "Actualización automática - Módulos Unificados"

# Dominio de producción que imprime la CLI de Vercel al terminar el deploy.
PATRON_URL_VERCEL = re.compile(r"https?://[\w.-]+\.vercel\.app[^\s\"'<>)\]]*")

# Líneas del resumen de pytest que nombran las pruebas que fallaron.
PATRON_FALLA_PYTEST = re.compile(r"^(FAILED|ERROR)\s+(?P<prueba>\S+)")

# Códigos ANSI (se apagan solos cuando la consola no los soporta).
ROJO = "\033[1;31m"
VERDE = "\033[1;32m"
AMARILLO = "\033[1;33m"
RESET = "\033[0m"

TIEMPO_ESPERA_GIT = 120
TIEMPO_ESPERA_TESTS = 1800
TIEMPO_ESPERA_DEPLOY = 1800
TIEMPO_ESPERA_HTTP = 60

# La propagación del deploy puede tardar unos segundos: se reintenta.
REINTENTOS_PRODUCCION = 6
PAUSA_PRODUCCION = 10.0

# Códigos de salida de `main` (publicar.bat escribe el mensaje final según esto).
SALIDA_OK = 0
SALIDA_ABORTADA = 1
SALIDA_CON_FALLAS = 2

# Máximo de archivos que se listan en el cuerpo del commit.
ARCHIVOS_EN_EL_COMMIT = 20

# Máximo de líneas de una salida ajena que se muestran cuando algo falla.
LINEAS_DE_SALIDA_AL_FALLAR = 40

# ¿Conviene escribir códigos ANSI? Lo decide `_preparar_salida`.
_COLORES = False

AYUDA_INSTALACION = {
    "git": "Instalá Git (https://git-scm.com/download/win) y volvé a intentar.",
    "vercel": (
        "Instalá la CLI con «npm install -g vercel» (requiere Node.js) y "
        "autenticate con «vercel login»."
    ),
}


class ErrorPublicacion(RuntimeError):
    """Fallo que impide publicar (se informa y se corta el proceso).

    ``rojo`` marca los fallos que además son de *control de calidad* (pruebas en
    rojo): ``main`` los imprime en rojo para que se lean de un vistazo.
    ``pistas`` agrega líneas de ayuda debajo del mensaje (qué hacer para
    destrabar la publicación).
    """

    def __init__(
        self,
        mensaje: str,
        *,
        rojo: bool = False,
        pistas: Sequence[str] = (),
    ) -> None:
        super().__init__(mensaje)
        self.rojo = rojo
        self.pistas = tuple(pistas)


@dataclass
class Completado:
    """Resultado de un comando externo."""

    codigo: int
    salida: str = ""

    @property
    def ok(self) -> bool:
        """``True`` si el comando terminó sin error."""
        return self.codigo == 0


@dataclass
class Consulta:
    """Resultado de una petición HTTP contra la app publicada."""

    codigo: int
    cuerpo: str = ""

    @property
    def ok(self) -> bool:
        """``True`` si la respuesta fue 2xx."""
        return 200 <= self.codigo < 300


@dataclass
class Informe:
    """Resultado de la verificación posterior al deploy."""

    mensajes: list[str] = field(default_factory=list)
    fallas: list[str] = field(default_factory=list)


@dataclass
class Resultado:
    """Resumen de la publicación (lo imprime ``main`` y lo usan las pruebas)."""

    rama: str = ""
    archivos: list[str] = field(default_factory=list)
    commit: str = ""
    pruebas: str = ""
    empujado: bool = False
    desplegado: bool = False
    url: str = ""
    simulacro: bool = False
    verificaciones: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    fallas: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Consola: colores ANSI y UTF-8 (Windows incluido)
# --------------------------------------------------------------------------- #


def colores_activos() -> bool:
    """``True`` si la salida admite códigos ANSI (lo decide ``_preparar_salida``)."""
    return _COLORES


def _pintar(texto: str, codigo: str) -> str:
    """Envuelve ``texto`` con ``codigo``; si no hay colores, lo devuelve igual."""
    if not _COLORES:
        return texto
    return f"{codigo}{texto}{RESET}"


def rojo(texto: str) -> str:
    """Pinta el texto en rojo (para los errores que frenan la publicación)."""
    return _pintar(texto, ROJO)


def verde(texto: str) -> str:
    """Pinta el texto en verde (para los pasos que salieron bien)."""
    return _pintar(texto, VERDE)


def amarillo(texto: str) -> str:
    """Pinta el texto en amarillo (para los avisos)."""
    return _pintar(texto, AMARILLO)


def apagar_colores() -> None:
    """Desactiva los colores (opción ``--sin-colores``)."""
    global _COLORES
    _COLORES = False


def _consola_interactiva(flujo=None) -> bool:
    """``True`` si ``flujo`` (por defecto ``sys.stdout``) es una consola de verdad."""
    flujo = sys.stdout if flujo is None else flujo
    es_terminal = getattr(flujo, "isatty", None)
    if es_terminal is None:
        return False
    try:
        return bool(es_terminal())
    except (ValueError, OSError):  # pragma: no cover - flujo ya cerrado
        return False


def _consola_utf8() -> bool:
    """Pone la consola de Windows en UTF-8 (para los ✓, los ⚠ y los acentos)."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        return bool(kernel32.SetConsoleOutputCP(65001))
    except (AttributeError, ImportError, OSError):  # pragma: no cover - sin consola
        return False


def _habilitar_color_en_windows() -> bool:
    """Activa las secuencias ANSI (*Virtual Terminal*) en la consola de Windows.

    La consola clásica necesita ``ENABLE_VIRTUAL_TERMINAL_PROCESSING``; Windows
    Terminal, VS Code y los sistemas que no son Windows ya vienen listos. Si algo
    falla se devuelve ``False`` y los mensajes salen sin códigos de color.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        STD_OUTPUT_HANDLE, STD_ERROR_HANDLE = -11, -12
        habilitado = False
        for identificador in (STD_OUTPUT_HANDLE, STD_ERROR_HANDLE):
            manejador = kernel32.GetStdHandle(identificador)
            modo = wintypes.DWORD()
            if not kernel32.GetConsoleMode(manejador, ctypes.byref(modo)):
                continue
            nuevo = modo.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            if kernel32.SetConsoleMode(manejador, nuevo):
                habilitado = True
        return habilitado
    except (AttributeError, ImportError, OSError):  # pragma: no cover - sin consola
        return False


def _resolver_ejecutable(
    nombre: str,
    buscar: Callable[[str], Optional[str]] = shutil.which,
) -> str:
    """Devuelve la ruta real del ejecutable (``vercel`` → ``vercel.cmd``).

    En Windows ``subprocess``/``CreateProcess`` **no** aplica ``PATHEXT``: al
    recibir ``vercel`` a secas no ve el ``.cmd`` que instala npm y falla con
    *"El sistema no puede encontrar el archivo especificado"*. Resolver la ruta
    con ``shutil.which`` (que sí mira ``PATHEXT``) evita ese falso "no está
    instalado". Los nombres que ya son rutas se dejan como están.
    """
    if os.sep in nombre or (os.altsep and os.altsep in nombre):
        return nombre
    return buscar(nombre) or nombre


def _correr(
    comando: list[str],
    *,
    capturar: bool = True,
    cwd: Path = RAIZ,
    tiempo_espera: int = TIEMPO_ESPERA_GIT,
) -> Completado:
    """Ejecuta un comando externo (git, vercel, pytest) y devuelve su salida."""
    partes = [str(parte) for parte in comando]
    partes[0] = _resolver_ejecutable(partes[0])
    try:
        proceso = subprocess.run(
            partes,
            cwd=str(cwd),
            capture_output=capturar,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=tiempo_espera,
        )
    except FileNotFoundError as error:
        raise ErrorPublicacion(
            f"No se encontró el programa «{comando[0]}». "
            f"{AYUDA_INSTALACION.get(comando[0], '')}".strip()
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ErrorPublicacion(
            f"«{' '.join(comando)}» superó el tiempo máximo de espera "
            f"({tiempo_espera} s)."
        ) from error
    salida = ""
    if capturar:
        # Ojo: ``rstrip`` y no ``strip``: en ``git status --porcelain`` el
        # espacio inicial de « M ruta» es parte del formato (indica "sin
        # cambios en el índice"), y recortarlo correría un carácter el nombre.
        salida = f"{proceso.stdout or ''}{proceso.stderr or ''}".rstrip()
    return Completado(proceso.returncode, salida)


def _consultar(url: str, *, tiempo_espera: int = TIEMPO_ESPERA_HTTP) -> Consulta:
    """Hace ``GET`` a ``url`` y devuelve el código (también si es un error HTTP)."""
    peticion = urllib.request.Request(
        url, headers={"User-Agent": "odontologico-publicar/1.0"}
    )
    try:
        with urllib.request.urlopen(peticion, timeout=tiempo_espera) as respuesta:
            cuerpo = respuesta.read().decode("utf-8", "replace")
            return Consulta(respuesta.status, cuerpo)
    except urllib.error.HTTPError as error:
        return Consulta(error.code, error.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return Consulta(0, f"{error}")


# --------------------------------------------------------------------------- #
# Lectura del estado del repositorio
# --------------------------------------------------------------------------- #


def _comando_legible(comando: Sequence[str], ancho: int = 60) -> str:
    """Formatea un comando para el log: una sola línea, con comillas si hace falta.

    Los ``git commit`` llevan el asunto y el cuerpo completos: se colapsan los
    espacios y se recorta cada argumento largo, así la consola queda prolija.
    """
    partes: list[str] = []
    for argumento in map(str, comando):
        texto = " ".join(argumento.split())
        if len(texto) > ancho:
            texto = texto[: ancho - 1] + "…"
        partes.append(f'"{texto}"' if " " in texto else texto)
    return " ".join(partes)


def _detalle(salida: str, maximo: int = 6) -> str:
    """Últimas líneas útiles de un comando fallido (para el mensaje de error)."""
    lineas = [linea for linea in salida.splitlines() if linea.strip()]
    if not lineas:
        return ""
    return ":\n  " + "\n  ".join(lineas[-maximo:])


def _nombres_del_status(salida: str) -> list[str]:
    """Extrae los nombres de archivo de ``git status --porcelain``.

    El formato es ``XY ruta`` (dos columnas de estado y **un espacio**) y los
    renombres llegan como ``R  viejo -> nuevo``; los nombres con caracteres
    especiales vienen entre comillas dobles.

    Espera la salida **sin recortar**: el primer carácter de `` M ruta`` es un
    espacio del propio formato, así que ``strip()`` sobre la salida correría un
    carácter el nombre (para eso ``_correr`` usa ``rstrip``).
    """

    nombres: list[str] = []
    for linea in salida.splitlines():
        if len(linea) < 4:
            continue
        ruta = linea[3:].strip()
        if " -> " in ruta:
            ruta = ruta.split(" -> ", 1)[1]
        ruta = ruta.strip().strip('"')
        if ruta:
            nombres.append(ruta)
    return nombres


def rama_actual(correr: Callable[..., Completado] = _correr) -> str:
    """Devuelve la rama actual; falla si el repositorio no es utilizable."""
    resultado = correr(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if not resultado.ok:
        raise ErrorPublicacion(
            "No se pudo leer la rama actual: ¿estamos dentro del repositorio git? "
            f"({resultado.salida or 'git rev-parse falló'})"
        )
    rama = resultado.salida.strip()
    if rama in RAMAS_INVALIDAS:
        raise ErrorPublicacion(
            "La rama está en estado «detached HEAD»: cambiá a una rama "
            "(«git switch main») y volvé a intentar."
        )
    return rama


def archivos_modificados(correr: Callable[..., Completado] = _correr) -> list[str]:
    """Archivos modificados, agregados o borrados que todavía no están commiteados."""
    resultado = correr(["git", "status", "--porcelain"])
    if not resultado.ok:
        raise ErrorPublicacion(
            f"«git status» falló (código {resultado.codigo})"
            f"{_detalle(resultado.salida)}"
        )
    return _nombres_del_status(resultado.salida)


def commits_pendientes(rama: str, correr: Callable[..., Completado] = _correr) -> int:
    """Cuántos commits locales todavía no están en ``origin/<rama>``."""
    resultado = correr(["git", "rev-list", "--count", f"origin/{rama}..{rama}"])
    if not resultado.ok or not resultado.salida.strip().isdigit():
        return 0
    return int(resultado.salida.strip())


def _exigir_programa(
    nombre: str,
    buscar: Callable[[str], Optional[str]] = shutil.which,
) -> str:
    """Comprueba que un ejecutable externo esté instalado."""
    ruta = buscar(nombre)
    if not ruta:
        ayuda = AYUDA_INSTALACION.get(nombre, "")
        raise ErrorPublicacion(
            f"No se encontró «{nombre}» en el PATH. {ayuda}".strip()
        )
    return ruta


# --------------------------------------------------------------------------- #
# Mensajes del commit y verificación del deploy
# --------------------------------------------------------------------------- #


def momento_legible(ahora: Optional[datetime] = None) -> str:
    """Fecha y hora en formato corto (``13/10/2026 09:05``)."""
    return (ahora or datetime.now()).strftime("%d/%m/%Y %H:%M")


def asunto_automatico(ahora: Optional[datetime] = None) -> str:
    """Asunto del commit cuando no se pasa ``-m``.

    Ejemplo: ``Actualización automática - Módulos Unificados [2026-10-15]``.
    """
    fecha = (ahora or datetime.now()).strftime("%Y-%m-%d")
    return f"{MENSAJE_AUTOMATICO} [{fecha}]"


def cuerpo_del_commit(archivos: list[str], ahora: Optional[datetime] = None) -> str:
    """Cuerpo del commit: el momento exacto y la lista de archivos que entran.

    Va aparte del asunto para que ``git log --oneline`` quede corto pero el
    *commit* completo conserve la fecha **y la hora** de la publicación.
    """
    listados = archivos[:ARCHIVOS_EN_EL_COMMIT]
    lineas = [f"- {nombre}" for nombre in listados]
    restantes = len(archivos) - len(listados)
    if restantes > 0:
        lineas.append(f"- … y {restantes} archivo(s) más")
    return (
        f"Publicado automáticamente por scripts/publicar.py el "
        f"{momento_legible(ahora)}.\n\n"
        f"Archivos incluidos ({len(archivos)}):\n" + "\n".join(lineas)
    )


def url_de_produccion(salida: str, por_defecto: str = URL_POR_DEFECTO) -> str:
    """Dominio de producción que informó la CLI de Vercel (o ``por_defecto``)."""
    encontradas = PATRON_URL_VERCEL.findall(salida or "")
    return encontradas[-1].rstrip(".,") if encontradas else por_defecto


def fallas_de_pytest(salida: str) -> list[str]:
    """Pruebas que fallaron, según el resumen de pytest."""
    fallas: list[str] = []
    for linea in (salida or "").splitlines():
        coincidencia = PATRON_FALLA_PYTEST.match(linea.strip())
        if not coincidencia:
            continue
        nombre = coincidencia.group("prueba")
        if nombre not in fallas:
            fallas.append(nombre)
    return fallas


def resumen_de_pytest(salida: str) -> str:
    """Última línea de pytest (por ejemplo ``461 passed, 1 skipped in 34s``)."""
    lineas = [linea.strip() for linea in (salida or "").splitlines() if linea.strip()]
    return lineas[-1] if lineas else "sin salida"


def mostrar_salida(
    salida: Callable[[str], None],
    texto: str,
    *,
    maximo: Optional[int] = LINEAS_DE_SALIDA_AL_FALLAR,
) -> None:
    """Muestra la salida de un comando ajeno (indentada y sin líneas vacías).

    ``maximo=None`` muestra todas las líneas (el deploy de Vercel se ve entero).
    """
    lineas = [linea.rstrip() for linea in (texto or "").splitlines() if linea.strip()]
    visibles = lineas if maximo is None else lineas[-maximo:]
    for linea in visibles:
        salida(f"  {linea}")


def pistas_de_push(detalle: str) -> tuple[str, ...]:
    """Explica los fallos típicos de ``git push`` (qué hacer para destrabarlo)."""
    texto = (detalle or "").lower()
    pistas: list[str] = []
    if any(
        marca in texto
        for marca in ("non-fast-forward", "fetch first", "[rejected]", "behind")
    ):
        pistas.append(
            "El remoto tiene commits que no están en tu copia: corré "
            "«git pull --rebase origin main» y volvé a publicar."
        )
    if any(
        marca in texto
        for marca in (
            "authentication failed",
            "could not read username",
            "permission denied",
            "403",
            "support for password authentication",
        )
    ):
        pistas.append(
            "GitHub rechazó las credenciales: iniciá sesión («gh auth login») o "
            "revisá el token de acceso personal configurado en git."
        )
    if any(
        marca in texto
        for marca in ("repository not found", "does not appear to be a git repository")
    ):
        pistas.append(
            "El remoto no es accesible: revisá la URL («git remote -v») y que tu "
            "cuenta tenga permiso sobre el repositorio."
        )
    if any(marca in texto for marca in ("protected branch", "pre-receive hook")):
        pistas.append(
            "La rama está protegida en GitHub: revisá las reglas del repositorio "
            "o publicá desde una rama permitida."
        )
    if not pistas:
        pistas.append(
            "Resolvé el error de arriba y volvé a correr el comando: el commit "
            "local queda intacto, sólo falta empujarlo."
        )
    return tuple(pistas)


def proyecto_enlazado(raiz: Path = RAIZ, entorno: Optional[dict] = None) -> bool:
    """``True`` si la carpeta está enlazada a un proyecto de Vercel.

    La CLI guarda el enlace en ``.vercel/project.json`` (o ``repo.json`` cuando
    se usó ``vercel link --repo``); en integración continua alcanza con las
    variables ``VERCEL_PROJECT_ID`` y ``VERCEL_ORG_ID``.
    """
    variables = os.environ if entorno is None else entorno
    if all(variables.get(nombre) for nombre in VARIABLES_DE_ENLACE):
        return True
    carpeta = Path(raiz) / CARPETA_VERCEL
    return any((carpeta / nombre).is_file() for nombre in ARCHIVOS_DE_ENLACE)


def exigir_proyecto_enlazado(raiz: Path = RAIZ) -> None:
    """Aborta si la carpeta no está enlazada (Vercel crearía otro proyecto)."""
    if proyecto_enlazado(raiz):
        return
    raise ErrorPublicacion(
        "Esta carpeta no está enlazada a un proyecto de Vercel "
        f"(no hay {CARPETA_VERCEL}/).",
        pistas=(
            "Corré «vercel link» una sola vez (elegí tu equipo y el proyecto "
            "odontologico), o definí VERCEL_PROJECT_ID y VERCEL_ORG_ID.",
        ),
    )


def exigir_prerrequisitos_del_deploy(
    buscar: Callable[[str], Optional[str]] = shutil.which,
) -> None:
    """Comprueba, **sin escribir nada**, que el deploy tenga con qué correr.

    Se llama antes de la parte de git: si la CLI de Vercel no está instalada (o
    la carpeta no está enlazada al proyecto) la publicación se corta **antes**
    de *commitear* y empujar. Si no, quedaría a medias: GitHub con el código
    nuevo y Vercel mostrando el viejo, que es la peor forma de fallar.
    """

    try:
        _exigir_programa("vercel", buscar)
        exigir_proyecto_enlazado()
    except ErrorPublicacion as error:
        raise ErrorPublicacion(
            str(error),
            pistas=tuple(error.pistas)
            + (
                "Se revisó antes de tocar git: no se commiteó ni se empujó nada.",
                "Si querés subir sólo a GitHub: "
                "python -m scripts.publicar --sin-deploy",
            ),
        ) from error


def exigir_remoto(
    rama: str,
    correr: Callable[..., Completado] = _correr,
) -> str:
    """Comprueba que exista el remoto ``origin`` y devuelve su URL."""
    resultado = correr(["git", "remote", "get-url", REMOTO])
    url = resultado.salida.strip() if resultado.ok else ""
    if not url:
        raise ErrorPublicacion(
            f"El repositorio no tiene el remoto «{REMOTO}» configurado: "
            f"«git push {REMOTO} {rama}» fallaría.",
            pistas=(
                "Agregalo con «git remote add origin "
                "https://github.com/msappia-spec/odontologico.git» y volvé a "
                "publicar.",
            ),
        )
    return url


def control_de_calidad(
    correr: Callable[..., Completado],
    *,
    salida: Callable[[str], None] = print,
    tiempo_espera: int = TIEMPO_ESPERA_TESTS,
) -> Completado:
    """Corre ``pytest`` y **aborta la publicación** si algún test falla.

    Es el primer paso del flujo y usa el mismo intérprete que ejecuta el script
    (``python -m pytest``), así ve las mismas dependencias que la app. Con un
    código de salida distinto de 0 no se agrega, no se *commitea*, no se empuja
    ni se despliega nada.
    """
    completado = correr(
        [sys.executable, "-m", "pytest"],
        capturar=True,
        tiempo_espera=tiempo_espera,
    )
    if completado.ok:
        return completado

    mostrar_salida(salida, completado.salida, maximo=LINEAS_DE_SALIDA_AL_FALLAR)
    fallas = fallas_de_pytest(completado.salida)
    pistas = [
        "No se publica código con errores: no se tocó git ni Vercel.",
        "Arreglá las pruebas que fallan y volvé a correr el comando.",
    ]
    if fallas:
        pistas.insert(0, f"Fallaron {len(fallas)}: {', '.join(fallas[:5])}")
    raise ErrorPublicacion(
        "Hay pruebas en rojo (pytest salió con código "
        f"{completado.codigo}): publicación abortada.",
        rojo=True,
        pistas=tuple(pistas),
    )


def verificar_produccion(
    url: str,
    *,
    consultar: Callable[..., Consulta] = _consultar,
    rutas: tuple[str, ...] = RUTAS_VERIFICADAS,
    esperar: bool = True,
    pausar: Callable[[float], None] = time.sleep,
) -> Informe:
    """Consulta la app publicada y separa lo verificado de lo que falló.

    La propagación de un deploy nuevo puede demorar unos segundos, por eso se
    reintenta; un ``401``/``403`` se informa como *aviso* (es la protección de
    Vercel, no un error de la aplicación).
    """

    informe = Informe()
    for ruta in rutas:
        completa = url.rstrip("/") + ruta
        intentos = REINTENTOS_PRODUCCION if esperar else 1
        respuesta = Consulta(0, "sin respuesta")
        for intento in range(intentos):
            respuesta = consultar(completa)
            if respuesta.ok or respuesta.codigo in (401, 403):
                break
            if intento + 1 < intentos:
                pausar(PAUSA_PRODUCCION)
        if respuesta.ok:
            informe.mensajes.append(f"✓ {ruta} responde {respuesta.codigo}")
        elif respuesta.codigo in (401, 403):
            informe.mensajes.append(
                f"⚠ {ruta} responde {respuesta.codigo}: el deploy quedó detrás de "
                "Deployment Protection (Vercel Authentication); comprobalo desde el "
                "navegador con tu cuenta."
            )
        else:
            detalle = (
                f"código {respuesta.codigo}" if respuesta.codigo else "sin respuesta"
            )
            informe.fallas.append(f"{ruta} no responde correctamente ({detalle})")
    return informe


# --------------------------------------------------------------------------- #
# Publicación
# --------------------------------------------------------------------------- #


def publicar(
    mensaje: Optional[str] = None,
    *,
    hacer_tests: bool = True,
    hacer_git: bool = True,
    hacer_deploy: bool = True,
    verificar: bool = True,
    forzar: bool = False,
    simulacro: bool = False,
    rama: Optional[str] = None,
    url: str = URL_POR_DEFECTO,
    correr: Optional[Callable[..., Completado]] = None,
    consultar: Optional[Callable[..., Consulta]] = None,
    buscar_programa: Optional[Callable[[str], Optional[str]]] = None,
    pausar: Callable[[float], None] = time.sleep,
    salida: Callable[[str], None] = print,
    ahora: Optional[datetime] = None,
) -> Resultado:
    """Prueba, *commitea*, empuja a GitHub, despliega en Vercel y verifica.

    Los colaboradores (``correr``, ``consultar``, ``buscar_programa``,
    ``pausar``, ``salida``) existen para poder probar el flujo sin ejecutar
    git, vercel ni tocar la red.
    """

    if not hacer_git and not hacer_deploy:
        raise ErrorPublicacion(
            "No hay nada que hacer: «--sin-deploy» y «--solo-deploy» a la vez "
            "se contradicen."
        )

    correr = correr or _correr
    consultar = consultar or _consultar
    buscar_programa = buscar_programa or shutil.which
    resultado = Resultado(simulacro=simulacro, url=url)

    def anunciar(texto: str = "") -> None:
        salida(texto)

    def es_solo_lectura(comando: list[str]) -> bool:
        """Los comandos de consulta sí se ejecutan en modo simulacro."""
        return (
            len(comando) > 1
            and comando[0] in {"git", "vercel"}
            and comando[1] in SUBCOMANDOS_SOLO_LECTURA
        )

    def ejecutar(
        comando: list[str],
        *,
        capturar: bool = True,
        tiempo_espera: int = TIEMPO_ESPERA_GIT,
    ) -> Completado:
        """Corre el comando (en simulacro, sólo lo muestra)."""
        legible = _comando_legible(comando)
        prefijo = "  [simulacro] " if simulacro else "  ➜ "
        anunciar(prefijo + legible)
        if simulacro and not es_solo_lectura(comando):
            return Completado(0)
        completado = correr(comando, capturar=capturar, tiempo_espera=tiempo_espera)
        if not completado.ok:
            raise ErrorPublicacion(
                f"«{legible}» falló "
                f"(código {completado.codigo}){_detalle(completado.salida)}"
            )
        return completado

    anunciar(f"▸ Publicación{' (simulacro)' if simulacro else ''}")
    resultado.rama = rama or ""
    pendientes = 0
    momento = ahora or datetime.now()

    if hacer_git:
        resultado.rama = resultado.rama or rama_actual(correr)
        resultado.archivos = archivos_modificados(correr)
        pendientes = commits_pendientes(resultado.rama, correr)
        if resultado.archivos:
            anunciar(f"▸ {len(resultado.archivos)} archivo(s) con cambios:")
            for nombre in resultado.archivos[:ARCHIVOS_EN_EL_COMMIT]:
                anunciar(f"  · {nombre}")
            sobran = len(resultado.archivos) - ARCHIVOS_EN_EL_COMMIT
            if sobran > 0:
                anunciar(f"  · … y {sobran} archivo(s) más")
        elif pendientes:
            anunciar(
                f"  Sin archivos nuevos por commitear; hay {pendientes} commit(s) "
                "local(es) pendientes de empujar."
            )
        else:
            resultado.avisos.append("No hay cambios para commitear.")

    # --- Paso 1: control de calidad (pytest) -------------------------------- #
    # Se corre antes de tocar el repositorio: con pruebas en rojo se corta acá.
    va_a_publicar = (
        bool(resultado.archivos) or pendientes > 0 or forzar or not hacer_git
    )
    # El deploy se hace cuando hay algo nuevo que publicar, cuando se fuerza
    # (por ejemplo tras cambiar una variable de entorno) o cuando sólo se
    # despliega (--solo-deploy, sin git).
    va_a_desplegar = hacer_deploy and va_a_publicar
    if hacer_tests and va_a_publicar:
        anunciar("▸ Control de calidad (python -m pytest)")
        if simulacro:
            anunciar("  [simulacro] python -m pytest")
        else:
            completado = control_de_calidad(
                correr, salida=salida, tiempo_espera=TIEMPO_ESPERA_TESTS
            )
            resultado.pruebas = resumen_de_pytest(completado.salida)
            anunciar(f"  ✓ pruebas en verde ({resultado.pruebas})")

    # --- Paso 1.5: prerrequisitos del deploy -------------------------------- #
    # Se comprueban *antes* de escribir en git: así un problema de Vercel no
    # deja la publicación a medias (GitHub al día y Vercel con código viejo).
    # En simulacro no se corta: se informa qué faltaría.
    if va_a_desplegar:
        anunciar("▸ Prerrequisitos del deploy (CLI de Vercel y proyecto enlazado)")
        try:
            exigir_prerrequisitos_del_deploy(buscar_programa)
        except ErrorPublicacion as error:
            if not simulacro:
                raise
            anunciar(f"  {amarillo('⚠')} {error}")
            for pista in error.pistas:
                anunciar(f"  {pista}")
        else:
            anunciar("  ✓ la CLI de Vercel está disponible y el proyecto enlazado")

    # --- Paso 2: control de versiones (git) --------------------------------- #
    if hacer_git:
        if resultado.archivos or pendientes:
            exigir_remoto(resultado.rama, correr)
        if resultado.archivos:
            asunto = mensaje or asunto_automatico(momento)
            # `git add -A` equivale a `git add .` desde la raíz e incluye borrados.
            ejecutar(["git", "add", "-A"])
            ejecutar(
                [
                    "git",
                    "commit",
                    "-m",
                    asunto,
                    "-m",
                    cuerpo_del_commit(resultado.archivos, momento),
                ]
            )
            resultado.commit = asunto
        if resultado.commit or pendientes:
            try:
                ejecutar(["git", "push", REMOTO, resultado.rama])
            except ErrorPublicacion as error:
                raise ErrorPublicacion(
                    str(error), pistas=pistas_de_push(str(error))
                ) from error
            resultado.empujado = not simulacro
            if not simulacro:
                anunciar(f"  ✓ empujado a {REMOTO}/{resultado.rama}")

    # --- Paso 3: despliegue en la nube (Vercel) ----------------------------- #
    if hacer_deploy:
        if va_a_desplegar or simulacro:
            anunciar("▸ Deploy en producción (vercel --prod --yes)")
            if simulacro:
                anunciar("  [simulacro] vercel --prod --yes")
            else:
                anunciar("  (la salida de la CLI se muestra al terminar)")
                completado = ejecutar(
                    ["vercel", "--prod", "--yes"],
                    capturar=True,
                    tiempo_espera=TIEMPO_ESPERA_DEPLOY,
                )
                mostrar_salida(anunciar, completado.salida, maximo=None)
                resultado.url = url_de_produccion(completado.salida, url)
                resultado.desplegado = True
                anunciar(f"  ✓ deploy terminado ({resultado.url})")
        else:
            resultado.avisos.append(
                "No se desplegó: no había cambios nuevos "
                "(usá «--forzar» para desplegar igual)."
            )

    if verificar and resultado.desplegado and not simulacro:
        anunciar("▸ Verificación en producción")
        informe = verificar_produccion(
            resultado.url, consultar=consultar, pausar=pausar
        )
        resultado.verificaciones = informe.mensajes
        resultado.fallas = informe.fallas
        for texto in informe.mensajes:
            anunciar(f"  {texto}")

    return resultado


def _preparar_salida() -> None:
    """Deja la consola lista: UTF-8, colores ANSI y sin ``UnicodeEncodeError``.

    En Windows se pone la página de códigos en UTF-8 y se habilitan las
    secuencias ANSI (*Virtual Terminal*), pero **sólo** cuando la salida es una
    consola interactiva: si la salida está redirigida (o no hay consola) los
    mensajes van sin códigos de color y un ``print`` con ``✓`` o ``⚠`` nunca
    corta la publicación. ``publicar.bat`` además fuerza UTF-8 (``chcp 65001`` y
    ``PYTHONUTF8=1``).
    """
    global _COLORES
    if not os.environ.get("NO_COLOR") and _consola_interactiva():
        _consola_utf8()
        _COLORES = _habilitar_color_en_windows()
    else:
        _COLORES = False
    for flujo in (sys.stdout, sys.stderr):
        ajustar = getattr(flujo, "reconfigure", None)
        if ajustar is None:
            continue
        try:
            ajustar(errors="replace")
        except (ValueError, OSError):  # pragma: no cover - salida redirigida
            pass


def _imprimir(resultado: Resultado, salida: Callable[[str], None] = print) -> None:
    """Reporta el resumen de la publicación."""
    if resultado.simulacro:
        titulo = amarillo("Simulacro terminado (no se escribió nada).")
    elif resultado.fallas:
        titulo = amarillo("Publicación terminada con fallas en la verificación.")
    else:
        titulo = verde("Publicación terminada.")
    salida("")
    salida(titulo)
    if resultado.rama:
        salida(f"  Rama   : {resultado.rama}")
    if resultado.archivos:
        salida(f"  Cambios: {len(resultado.archivos)} archivo(s)")
    if resultado.pruebas:
        salida(f"  Tests  : {resultado.pruebas}")
    if resultado.commit:
        salida(f"  Commit : {resultado.commit}")
    if resultado.empujado:
        salida(f"  GitHub : empujado a {REMOTO}/{resultado.rama}")
    if resultado.desplegado:
        salida(f"  Vercel : producción actualizada ({resultado.url})")
    for texto in resultado.verificaciones:
        salida(f"  {texto}")
    for aviso in resultado.avisos:
        salida(f"  {amarillo('⚠')} {aviso}")
    for falla in resultado.fallas:
        salida(f"  {rojo('✗')} {falla}")
    if resultado.fallas:
        salida(
            "  El código ya quedó en GitHub y en Vercel: lo que falló es el "
            "chequeo final de la app."
        )
        salida(
            "  Reintentá el deploy y la verificación con: python -m "
            "scripts.publicar --solo-deploy"
        )
    if resultado.simulacro:
        salida("  Repetí el comando sin --simulacro para publicar de verdad.")


def main(
    argv: list[str] | None = None,
    *,
    correr: Optional[Callable[..., Completado]] = None,
    consultar: Optional[Callable[..., Consulta]] = None,
    buscar_programa: Optional[Callable[[str], Optional[str]]] = None,
    pausar: Optional[Callable[[float], None]] = None,
    salida: Callable[[str], None] = print,
) -> int:
    """Punto de entrada del script; devuelve el código de salida."""
    _preparar_salida()
    parser = argparse.ArgumentParser(
        description=(
            "Corre las pruebas, commitea, empuja a GitHub y despliega en Vercel "
            "los cambios del proyecto; al final verifica la URL publicada."
        )
    )
    parser.add_argument(
        "-m",
        "--mensaje",
        help="Mensaje del commit (si no se pasa, se arma uno con la fecha y hora).",
    )
    parser.add_argument(
        "--sin-tests",
        action="store_true",
        help="No corre pytest antes de subir (no recomendado).",
    )
    parser.add_argument(
        "--sin-deploy",
        action="store_true",
        help="Sólo GitHub: commitea y empuja, sin desplegar en Vercel.",
    )
    parser.add_argument(
        "--solo-deploy",
        action="store_true",
        help="Sólo Vercel: despliega sin tocar git.",
    )
    parser.add_argument(
        "--forzar",
        action="store_true",
        help="Despliega aunque no haya cambios nuevos.",
    )
    parser.add_argument(
        "--simulacro",
        action="store_true",
        help="Muestra qué haría (y con qué archivos) sin ejecutar nada que escriba.",
    )
    parser.add_argument(
        "--sin-colores",
        action="store_true",
        help="No escribe códigos de color ANSI (para terminales viejas o logs).",
    )
    parser.add_argument(
        "--sin-verificar",
        action="store_true",
        help="No consulta la URL publicada al terminar el deploy.",
    )
    parser.add_argument(
        "--rama",
        help="Rama a empujar (por defecto, la que está activa).",
    )
    parser.add_argument(
        "--url",
        default=URL_POR_DEFECTO,
        help=(
            "Base de la app publicada que se verifica al final (si la CLI de "
            "Vercel informa otro dominio, se usa ese)."
        ),
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as salida_de_argparse:
        # `argparse` sale con 2 cuando una opción está mal escrita, pero ese
        # código acá significa «se publicó con fallas»: se traduce a «se abortó»
        # (no se publicó nada) para que publicar.bat no mienta.
        return SALIDA_ABORTADA if salida_de_argparse.code else SALIDA_OK
    if args.sin_colores:
        apagar_colores()

    try:
        resultado = publicar(
            args.mensaje,
            hacer_tests=not args.sin_tests,
            hacer_git=not args.solo_deploy,
            hacer_deploy=not args.sin_deploy,
            verificar=not args.sin_verificar,
            forzar=args.forzar,
            simulacro=args.simulacro,
            rama=args.rama,
            url=args.url,
            correr=correr or _correr,
            consultar=consultar or _consultar,
            buscar_programa=buscar_programa or shutil.which,
            pausar=pausar or time.sleep,
            salida=salida,
        )
    except ErrorPublicacion as error:
        # Con colores apagados (log redirigido o consola vieja) el mensaje de
        # control de calidad se enmarca, así se ve de un vistazo igual.
        enmarcar = error.rojo and not colores_activos()
        separador = "=" * 68
        if enmarcar:
            salida(separador)
        salida(rojo(f"✗ {error}"))
        for pista in error.pistas:
            salida(f"  {pista}")
        if enmarcar:
            salida(separador)
        return SALIDA_ABORTADA

    _imprimir(resultado, salida)
    return SALIDA_CON_FALLAS if resultado.fallas else SALIDA_OK


if __name__ == "__main__":  # pragma: no cover - ejecución manual
    raise SystemExit(main())
