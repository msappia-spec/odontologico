"""Publica los cambios del proyecto: pruebas, commit, push a GitHub y Vercel.

Contexto
--------
Subir una modificación a producción son varios pasos manuales que hay que
repetir siempre en el mismo orden (probar, *commitear*, empujar al repositorio
y desplegar en Vercel). Este script los encadena y **corta al primer fallo**,
de modo que nunca se publica código sin probar ni quedan *commits* locales sin
subir por olvido.

Qué hace
--------
1. Comprueba que estamos en la raíz del repositorio y en una rama con nombre
   (si el ``HEAD`` está *detached*, avisa y no hace nada).
2. Corre las pruebas con el mismo intérprete que ejecuta el script
   (``python -m pytest``). Si alguna falla, **no se sube nada**.
3. Agrega y *commitea* los cambios (``git add -A`` + ``git commit``), listando
   los archivos incluidos.
4. Empuja la rama a GitHub (``git push origin <rama>``).
5. Despliega en producción (``vercel --prod --yes``).
6. Verifica la URL publicada (``/health``, el panel y ``/medico``) y avisa si
   el deploy quedó con *Deployment Protection* (respuesta 401/403) en lugar de
   fallar por algo que no es un problema de la app.

Si no hay cambios ni *commits* pendientes, no se *commitea* (git lo rechazaría)
ni se despliega: en ese caso se informa y termina. ``--forzar`` sirve para
desplegar igual (por ejemplo después de cambiar una variable de entorno).

Uso (desde la raíz del proyecto, con el venv activo)::

    python -m scripts.publicar                       # pruebas + commit + push + deploy
    python -m scripts.publicar -m "Arregla el panel" # mensaje de commit propio
    python -m scripts.publicar --sin-tests           # saltea las pruebas
    python -m scripts.publicar --sin-deploy          # sólo GitHub
    python -m scripts.publicar --solo-deploy         # sólo Vercel (sin git)
    python -m scripts.publicar --simulacro           # informa sin escribir nada

En Windows hay además ``publicar.bat`` (doble clic) que llama a este módulo con
el intérprete del entorno virtual.

Controles: ``tests/test_publicar.py`` (ejecutor de comandos y cliente HTTP
falsos: las pruebas no corren git, ni vercel, ni tocan la red).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

# Raíz del proyecto (este archivo vive en scripts/).
RAIZ = Path(__file__).resolve().parents[1]

# App desplegada que se verifica al final (se puede cambiar con --url).
URL_POR_DEFECTO = "https://odontologico-omega.vercel.app"

# Rutas que se consultan para confirmar que el deploy quedó sano.
RUTAS_VERIFICADAS = ("/health", "/", "/medico")

# Ramas válidas para publicar (git devuelve "HEAD" cuando está detached).
RAMAS_INVALIDAS = {"", "HEAD"}

# Subcomandos que sólo leen: se ejecutan incluso en modo simulacro.
SUBCOMANDOS_SOLO_LECTURA = {"rev-parse", "status", "rev-list", "log", "diff", "ls"}

TIEMPO_ESPERA_GIT = 120
TIEMPO_ESPERA_TESTS = 1800
TIEMPO_ESPERA_DEPLOY = 1800
TIEMPO_ESPERA_HTTP = 60

# La propagación del deploy puede tardar unos segundos: se reintenta.
REINTENTOS_PRODUCCION = 6
PAUSA_PRODUCCION = 10.0

# Máximo de archivos que se listan en el cuerpo del commit.
ARCHIVOS_EN_EL_COMMIT = 20

AYUDA_INSTALACION = {
    "git": "Instalá Git (https://git-scm.com/download/win) y volvé a intentar.",
    "vercel": (
        "Instalá la CLI con «npm install -g vercel» (requiere Node.js) y "
        "autenticate con «vercel login»."
    ),
}


class ErrorPublicacion(RuntimeError):
    """Fallo que impide publicar (se informa y se corta el proceso)."""


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
    empujado: bool = False
    desplegado: bool = False
    url: str = ""
    simulacro: bool = False
    verificaciones: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    fallas: list[str] = field(default_factory=list)


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


def asunto_automatico(ahora: Optional[datetime] = None) -> str:
    """Asunto del commit cuando no se pasa ``-m``."""
    momento = (ahora or datetime.now()).strftime("%d/%m/%Y %H:%M")
    return f"Publica cambios del {momento}"


def cuerpo_del_commit(archivos: list[str]) -> str:
    """Cuerpo del commit: la lista de archivos que entran."""
    listados = archivos[:ARCHIVOS_EN_EL_COMMIT]
    lineas = [f"- {nombre}" for nombre in listados]
    restantes = len(archivos) - len(listados)
    if restantes > 0:
        lineas.append(f"- … y {restantes} archivo(s) más")
    return f"Archivos incluidos ({len(archivos)}):\n" + "\n".join(lineas)


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
        prefijo = "  [simulacro] " if simulacro else "  ➜ "
        anunciar(prefijo + " ".join(map(str, comando)))
        if simulacro and not es_solo_lectura(comando):
            return Completado(0)
        completado = correr(comando, capturar=capturar, tiempo_espera=tiempo_espera)
        if not completado.ok:
            raise ErrorPublicacion(
                f"«{' '.join(map(str, comando))}» falló "
                f"(código {completado.codigo}){_detalle(completado.salida)}"
            )
        return completado

    anunciar(f"▸ Publicación{' (simulacro)' if simulacro else ''}")
    resultado.rama = rama or ""
    pendientes = 0

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
            if hacer_tests:
                anunciar("▸ Pruebas (python -m pytest)")
                ejecutar(
                    [sys.executable, "-m", "pytest"],
                    capturar=False,
                    tiempo_espera=TIEMPO_ESPERA_TESTS,
                )
                if not simulacro:
                    anunciar("  ✓ pruebas en verde")
            asunto = mensaje or asunto_automatico(ahora)
            ejecutar(["git", "add", "-A"])
            ejecutar(
                [
                    "git",
                    "commit",
                    "-m",
                    asunto,
                    "-m",
                    cuerpo_del_commit(resultado.archivos),
                ]
            )
            resultado.commit = asunto
        elif pendientes:
            anunciar(
                f"  Sin archivos nuevos por commitear; hay {pendientes} commit(s) "
                "local(es) pendientes de empujar."
            )
        else:
            resultado.avisos.append("No hay cambios para commitear.")

        if resultado.commit or pendientes:
            ejecutar(["git", "push", "origin", resultado.rama])
            resultado.empujado = not simulacro
            if not simulacro:
                anunciar(f"  ✓ empujado a origin/{resultado.rama}")

    hay_novedad = bool(resultado.commit or pendientes)

    if hacer_deploy:
        if hay_novedad or forzar or not hacer_git or simulacro:
            if not simulacro:
                _exigir_programa("vercel", buscar_programa)
            anunciar("▸ Deploy en producción (vercel --prod --yes)")
            ejecutar(
                ["vercel", "--prod", "--yes"],
                capturar=False,
                tiempo_espera=TIEMPO_ESPERA_DEPLOY,
            )
            resultado.desplegado = not simulacro
            if not simulacro:
                anunciar("  ✓ deploy terminado")
        else:
            resultado.avisos.append(
                "No se desplegó: no había cambios nuevos "
                "(usá «--forzar» para desplegar igual)."
            )

    if verificar and resultado.desplegado and not simulacro:
        anunciar("▸ Verificación en producción")
        informe = verificar_produccion(url, consultar=consultar, pausar=pausar)
        resultado.verificaciones = informe.mensajes
        resultado.fallas = informe.fallas
        for texto in informe.mensajes:
            anunciar(f"  {texto}")

    return resultado


def _preparar_salida() -> None:
    """Evita ``UnicodeEncodeError`` cuando la consola no entiende UTF-8.

    El mensaje de error de un ``print`` con símbolos como ``✓`` o ``⚠`` no debe
    cortar la publicación; ``publicar.bat`` además fuerza UTF-8 (``chcp 65001``
    y ``PYTHONUTF8=1``).
    """
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
    titulo = (
        "Simulacro terminado (no se escribió nada)."
        if resultado.simulacro
        else "Publicación terminada."
    )
    salida("")
    salida(titulo)
    if resultado.rama:
        salida(f"  Rama   : {resultado.rama}")
    if resultado.archivos:
        salida(f"  Cambios: {len(resultado.archivos)} archivo(s)")
    if resultado.commit:
        salida(f"  Commit : {resultado.commit}")
    if resultado.empujado:
        salida(f"  GitHub : empujado a origin/{resultado.rama}")
    if resultado.desplegado:
        salida(f"  Vercel : producción actualizada ({resultado.url})")
    for texto in resultado.verificaciones:
        salida(f"  {texto}")
    for aviso in resultado.avisos:
        salida(f"  ⚠ {aviso}")
    for falla in resultado.fallas:
        salida(f"  ✗ {falla}")
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
        help="Base de la app publicada que se verifica al final.",
    )
    args = parser.parse_args(argv)

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
        salida(f"✗ {error}")
        return 1

    _imprimir(resultado, salida)
    return 1 if resultado.fallas else 0


if __name__ == "__main__":  # pragma: no cover - ejecución manual
    raise SystemExit(main())
