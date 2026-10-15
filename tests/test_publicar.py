"""Pruebas de ``scripts/publicar.py``.

Todo el flujo se ejercita con **dobles**: un ejecutor de comandos falso (anota
las órdenes en vez de correrlas) y un cliente HTTP falso. Así las pruebas no
llaman a git, ni a la CLI de Vercel, ni tocan la red ni el repositorio real.
"""

import os
import sys
from datetime import datetime
from urllib.parse import urlparse

import pytest

from scripts.publicar import (
    RESET,
    ROJO,
    SUBCOMANDOS_SOLO_LECTURA,
    URL_POR_DEFECTO,
    Completado,
    Consulta,
    ErrorPublicacion,
    _comando_legible,
    _correr,
    _nombres_del_status,
    _preparar_salida,
    _resolver_ejecutable,
    archivos_modificados,
    asunto_automatico,
    colores_activos,
    commits_pendientes,
    control_de_calidad,
    cuerpo_del_commit,
    exigir_prerrequisitos_del_deploy,
    exigir_proyecto_enlazado,
    exigir_remoto,
    fallas_de_pytest,
    main,
    momento_legible,
    mostrar_salida,
    pistas_de_push,
    proyecto_enlazado,
    publicar,
    resumen_de_pytest,
    rojo,
    url_de_produccion,
    verificar_produccion,
)

# Mismos subcomandos que el script trata como consultas de sólo lectura.
SOLO_LECTURA = SUBCOMANDOS_SOLO_LECTURA


def solo_lectura(comando: list[str]) -> bool:
    """``True`` si el comando no modifica nada (lo que se corre en simulacro)."""
    return (
        len(comando) > 1
        and comando[0] in {"git", "vercel"}
        and comando[1] in SOLO_LECTURA
    )


class EjecutorFalso:
    """Anota los comandos y responde como si fueran reales."""

    def __init__(
        self,
        status: str = "",
        rama: str = "main",
        pendientes: str = "0",
        fallar: tuple[str, ...] = (),
        salidas: dict | None = None,
        remoto: str = "https://github.com/msappia-spec/odontologico.git",
        salida_fallo: str = "un error cualquiera",
    ) -> None:
        self.comandos: list[list[str]] = []
        self.status = status
        self.rama = rama
        self.pendientes = pendientes
        self.fallar = tuple(fallar)
        self.salidas = dict(salidas or {})
        self.remoto = remoto
        self.salida_fallo = salida_fallo

    def __call__(self, comando, *, capturar=True, cwd=None, tiempo_espera=None):
        partes = [str(parte) for parte in comando]
        self.comandos.append(partes)
        union = " ".join(partes)
        for trozo, salida in self.salidas.items():
            if trozo in union:
                return Completado(0, salida)
        if any(trozo in union for trozo in self.fallar):
            return Completado(1, self.salida_fallo)
        if partes[0] == "git":
            if partes[1] == "rev-parse":
                return Completado(0, self.rama)
            if partes[1] == "status":
                return Completado(0, self.status)
            if partes[1] == "rev-list":
                return Completado(0, self.pendientes)
            if partes[1] == "remote":
                return Completado(0, self.remoto)
        return Completado(0, "")

    def secuencia(self) -> list[str]:
        """Comandos en forma legible (``git add -A``, ``vercel --prod --yes``…)."""
        return [" ".join(comando) for comando in self.comandos]

    def escrituras(self) -> list[str]:
        """Órdenes que modifican algo: lo que un simulacro no debe ejecutar."""
        return [
            comando
            for comando in self.secuencia()
            if not solo_lectura(comando.split(" "))
        ]


class ClienteFalso:
    """Responde por ruta (``/health``, ``/``, ``/medico``)."""

    def __init__(self, respuestas: dict | None = None, por_defecto: int = 200):
        self.pedidas: list[str] = []
        self.respuestas = dict(respuestas or {})
        self.por_defecto = por_defecto

    def __call__(self, url, **kwargs):
        self.pedidas.append(url)
        ruta = urlparse(url).path or "/"
        return Consulta(self.respuestas.get(ruta, self.por_defecto), "")


def publicar_con(ejecutor: EjecutorFalso, cliente=None, **kwargs):
    """Publica con los dobles y sin escribir en la consola."""
    kwargs.setdefault("salida", lambda texto: None)
    kwargs.setdefault("pausar", lambda segundos: None)
    kwargs.setdefault("buscar_programa", lambda nombre: f"C:/falso/{nombre}")
    kwargs.setdefault("consultar", cliente or ClienteFalso())
    return publicar(correr=ejecutor, **kwargs)


# --------------------------------------------------------------------------- #
# Flujo completo
# --------------------------------------------------------------------------- #


def test_publica_en_orden_pruebas_commit_push_y_deploy():
    ejecutor = EjecutorFalso(status=" M app/static/app.js\n M README.md")
    resultado = publicar_con(ejecutor, mensaje="Arregla el panel")

    orden = ejecutor.secuencia()
    pruebas = next(indice for indice, c in enumerate(orden) if "-m pytest" in c)
    assert pruebas < orden.index("git add -A")
    commit = next(comando for comando in orden if comando.startswith("git commit"))
    assert "Arregla el panel" in commit
    assert "git push origin main" in orden
    assert orden[-1] == "vercel --prod --yes"

    assert resultado.archivos == ["app/static/app.js", "README.md"]
    assert resultado.rama == "main"
    assert resultado.commit == "Arregla el panel"
    assert resultado.empujado is True
    assert resultado.desplegado is True


def test_los_archivos_van_en_el_cuerpo_del_commit():
    ejecutor = EjecutorFalso(status="?? nuevo.txt")
    publicar_con(ejecutor)

    commit = next(c for c in ejecutor.secuencia() if c.startswith("git commit"))
    assert "Archivos incluidos (1)" in commit
    assert "- nuevo.txt" in commit


def test_simulacro_no_ejecuta_nada_que_escriba():
    ejecutor = EjecutorFalso(status=" M app/main.py")
    cliente = ClienteFalso()
    resultado = publicar_con(ejecutor, cliente, simulacro=True)

    assert resultado.archivos == ["app/main.py"]  # el estado sí se consulta
    assert ejecutor.escrituras() == []  # no commitea, no empuja, no despliega
    assert cliente.pedidas == []  # no consulta producción
    assert resultado.desplegado is False
    assert resultado.simulacro is True


def test_las_pruebas_en_rojo_frenan_la_publicacion():
    ejecutor = EjecutorFalso(status=" M app/main.py", fallar=("-m pytest",))
    with pytest.raises(ErrorPublicacion) as error:
        publicar_con(ejecutor)

    assert "pytest" in str(error.value)
    assert not any(
        comando.startswith(("git add", "git commit", "git push", "vercel"))
        for comando in ejecutor.secuencia()
    )


def test_sin_cambios_no_commitea_ni_despliega():
    ejecutor = EjecutorFalso()
    resultado = publicar_con(ejecutor)

    orden = ejecutor.secuencia()
    assert not any(
        comando.startswith(("git add", "git commit", "git push", "vercel"))
        for comando in orden
    )
    assert not any("-m pytest" in comando for comando in orden)  # no prueba al pedo
    assert any("No hay cambios" in aviso for aviso in resultado.avisos)
    assert any("No se desplegó" in aviso for aviso in resultado.avisos)


def test_los_commits_locales_pendientes_se_empujan_sin_commitear():
    ejecutor = EjecutorFalso(pendientes="2")
    resultado = publicar_con(ejecutor)

    orden = ejecutor.secuencia()
    assert "git push origin main" in orden
    assert not any(comando.startswith("git commit") for comando in orden)
    assert resultado.commit == ""
    assert resultado.empujado is True
    assert resultado.desplegado is True


def test_forzar_despliega_aunque_no_haya_cambios():
    ejecutor = EjecutorFalso()
    resultado = publicar_con(ejecutor, forzar=True)

    assert "vercel --prod --yes" in ejecutor.secuencia()
    assert resultado.desplegado is True
    assert not resultado.fallas


def test_sin_deploy_publica_solo_en_github():
    ejecutor = EjecutorFalso(status=" M app/main.py")
    resultado = publicar_con(ejecutor, hacer_deploy=False)

    assert "git push origin main" in ejecutor.secuencia()
    assert not any(c.startswith("vercel") for c in ejecutor.secuencia())
    assert resultado.desplegado is False


def test_solo_deploy_no_toca_git():
    ejecutor = EjecutorFalso(status=" M app/main.py")
    resultado = publicar_con(ejecutor, hacer_git=False)

    orden = ejecutor.secuencia()
    assert not any(comando.startswith("git") for comando in orden)
    assert "vercel --prod --yes" in orden
    assert resultado.desplegado is True


def test_apagar_git_y_deploy_a_la_vez_es_un_error():
    with pytest.raises(ErrorPublicacion):
        publicar_con(EjecutorFalso(), hacer_git=False, hacer_deploy=False)


def test_el_mensaje_automatico_lleva_fecha_y_hora():
    momento = datetime(2026, 10, 13, 9, 5)
    ejecutor = EjecutorFalso(status=" M README.md")
    publicar_con(ejecutor, ahora=momento)

    commit = next(c for c in ejecutor.secuencia() if c.startswith("git commit"))
    assert asunto_automatico(momento) in commit
    assert "13/10/2026 09:05" in commit


def test_una_rama_detached_frena_la_publicacion():
    ejecutor = EjecutorFalso(rama="HEAD", status=" M app/main.py")
    with pytest.raises(ErrorPublicacion) as error:
        publicar_con(ejecutor)

    assert "detached" in str(error.value)


def test_si_git_falla_avisa_que_no_hay_repositorio():
    ejecutor = EjecutorFalso(fallar=("rev-parse",))
    with pytest.raises(ErrorPublicacion) as error:
        publicar_con(ejecutor)

    assert "repositorio" in str(error.value)


def test_si_falta_la_cli_de_vercel_da_las_instrucciones():
    ejecutor = EjecutorFalso(status=" M app/main.py")
    with pytest.raises(ErrorPublicacion) as error:
        publicar_con(ejecutor, buscar_programa=lambda nombre: None)

    assert "npm install -g vercel" in str(error.value)
    assert "vercel --prod --yes" not in ejecutor.secuencia()


def test_si_falta_algo_del_deploy_se_corta_antes_de_tocar_git():
    """El chequeo previo evita publicar a medias (GitHub al día, Vercel viejo)."""
    ejecutor = EjecutorFalso(status=" M app/main.py")
    with pytest.raises(ErrorPublicacion) as error:
        publicar_con(ejecutor, buscar_programa=lambda nombre: None)

    assert any(
        "no se commiteó ni se empujó nada" in pista for pista in error.value.pistas
    )
    assert not any(
        comando.startswith(("git add", "git commit", "git push", "vercel"))
        for comando in ejecutor.secuencia()
    )


def test_exigir_prerrequisitos_del_deploy_revisa_la_cli_y_el_enlace(monkeypatch):
    monkeypatch.setattr("scripts.publicar.proyecto_enlazado", lambda *a, **k: True)
    assert exigir_prerrequisitos_del_deploy(lambda nombre: "C:/falso/vercel") is None

    monkeypatch.setattr("scripts.publicar.proyecto_enlazado", lambda *a, **k: False)
    with pytest.raises(ErrorPublicacion) as error:
        exigir_prerrequisitos_del_deploy(lambda nombre: "C:/falso/vercel")

    assert any("vercel link" in pista for pista in error.value.pistas)
    assert any("--sin-deploy" in pista for pista in error.value.pistas)


def test_el_simulacro_informa_los_prerrequisitos_del_deploy(monkeypatch):
    monkeypatch.setattr("scripts.publicar.proyecto_enlazado", lambda *a, **k: True)
    ejecutor = EjecutorFalso(status=" M app/main.py")
    salidas: list[str] = []
    publicar_con(ejecutor, simulacro=True, salida=salidas.append)

    assert any("Prerrequisitos del deploy" in linea for linea in salidas)
    assert any("la CLI de Vercel está disponible" in linea for linea in salidas)


def test_el_simulacro_avisa_si_falta_algo_del_deploy_sin_cortar():
    """En simulacro no hay nada que romper: el faltante se informa y se sigue."""
    ejecutor = EjecutorFalso(status=" M app/main.py")
    salidas: list[str] = []
    resultado = publicar_con(
        ejecutor,
        simulacro=True,
        buscar_programa=lambda nombre: None,
        salida=salidas.append,
    )

    assert resultado.simulacro is True
    assert any("npm install -g vercel" in linea for linea in salidas)
    assert ejecutor.escrituras() == []


# --------------------------------------------------------------------------- #
# Verificación en producción
# --------------------------------------------------------------------------- #


def test_la_verificacion_consulta_health_el_panel_y_la_vista_movil():
    ejecutor = EjecutorFalso(status=" M app/main.py")
    cliente = ClienteFalso()
    resultado = publicar_con(ejecutor, cliente)

    assert len(cliente.pedidas) == 3
    assert any(url.endswith("/health") for url in cliente.pedidas)
    assert any(url.endswith("/medico") for url in cliente.pedidas)
    assert len(resultado.verificaciones) == 3
    assert not resultado.fallas


def test_deployment_protection_es_aviso_y_no_reintenta():
    ejecutor = EjecutorFalso(status=" M app/main.py")
    cliente = ClienteFalso(por_defecto=401)
    resultado = publicar_con(ejecutor, cliente)

    assert not resultado.fallas
    assert len(cliente.pedidas) == 3  # un solo intento por ruta
    assert all("Deployment Protection" in texto for texto in resultado.verificaciones)


def test_una_ruta_caida_se_reintenta_y_queda_como_falla():
    ejecutor = EjecutorFalso(status=" M app/main.py")
    cliente = ClienteFalso({"/": 500})
    resultado = publicar_con(ejecutor, cliente)

    assert len(resultado.fallas) == 1
    assert "/" in resultado.fallas[0]
    assert cliente.pedidas.count("https://odontologico-omega.vercel.app/") == 6


def test_sin_verificar_no_se_consulta_produccion():
    ejecutor = EjecutorFalso(status=" M app/main.py")
    cliente = ClienteFalso()
    resultado = publicar_con(ejecutor, cliente, verificar=False)

    assert cliente.pedidas == []
    assert resultado.verificaciones == []


def test_verificar_produccion_sin_espera_hace_un_solo_intento():
    cliente = ClienteFalso({"/health": 503})
    informe = verificar_produccion(
        "https://ejemplo.test", consultar=cliente, esperar=False
    )

    assert len(informe.fallas) == 1
    assert len(cliente.pedidas) == 3


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #


def test_el_log_de_los_comandos_es_una_sola_linea_legible():
    legible = _comando_legible(
        ["git", "commit", "-m", "asunto corto", "-m", "primera\nsegunda"]
    )

    assert legible == 'git commit -m "asunto corto" -m "primera segunda"'
    assert _comando_legible(["git", "add", "-A"]) == "git add -A"


def test_el_log_recorta_los_argumentos_larguisimos():
    legible = _comando_legible(["git", "commit", "-m", "x" * 100])

    assert legible.startswith("git commit -m ")
    assert legible.endswith("…")
    assert len(legible) < 130


def test_nombres_del_status_interpreta_el_formato_porcelain():
    salida = (
        "?? nuevo.txt\n"
        ' M "como ejecutarlo.txt"\n'
        "R  viejo.py -> nuevo.py\n"
        "D  borrado.py\n"
    )
    assert _nombres_del_status(salida) == [
        "nuevo.txt",
        "como ejecutarlo.txt",
        "nuevo.py",
        "borrado.py",
    ]


def test_cuerpo_del_commit_recorta_las_listas_largas():
    cuerpo = cuerpo_del_commit([f"archivo{indice}.py" for indice in range(25)])

    assert "Archivos incluidos (25)" in cuerpo
    assert "- archivo19.py" in cuerpo
    assert "- archivo20.py" not in cuerpo
    assert "y 5 archivo(s) más" in cuerpo


def test_commits_pendientes_ante_un_error_devuelve_cero():
    assert commits_pendientes("main", EjecutorFalso(fallar=("rev-list",))) == 0


def test_archivos_modificados_propaga_el_fallo_de_git():
    with pytest.raises(ErrorPublicacion):
        archivos_modificados(EjecutorFalso(fallar=("status",)))


def test_resolver_ejecutable_usa_la_ruta_que_encuentra_en_el_path():
    encontrado = _resolver_ejecutable(
        "vercel", buscar=lambda nombre: "C:/nodejs/vercel.CMD"
    )

    assert encontrado == "C:/nodejs/vercel.CMD"


def test_resolver_ejecutable_deja_el_nombre_si_no_lo_encuentra():
    assert _resolver_ejecutable("vercel", buscar=lambda nombre: None) == "vercel"


@pytest.mark.parametrize(
    "ruta",
    ["C:/Program Files/Git/cmd/git.exe", r"C:\tools\git.exe", "bin/tool"],
)
def test_resolver_ejecutable_no_toca_los_nombres_que_ya_son_rutas(ruta):
    assert _resolver_ejecutable(ruta, buscar=lambda nombre: None) == ruta


def test_correr_ejecuta_de_verdad_y_captura_la_salida():
    completado = _correr([sys.executable, "-c", "print('hola')"])

    assert completado.ok
    assert "hola" in completado.salida


def test_correr_avisa_cuando_falta_el_programa():
    with pytest.raises(ErrorPublicacion) as error:
        _correr(["programa-que-no-existe-jamas"])

    assert "No se encontró el programa" in str(error.value)


@pytest.mark.skipif(os.name != "nt", reason="Los .cmd son de Windows")
def test_correr_resuelve_los_cmd_que_instala_npm(tmp_path, monkeypatch):
    """Regresión: ``CreateProcess`` no aplica ``PATHEXT`` y no veía ``vercel.cmd``."""
    script = tmp_path / "saludo.cmd"
    script.write_text("@echo hola desde el cmd\r\n", encoding="ascii")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    completado = _correr(["saludo"], cwd=tmp_path)

    assert completado.ok
    assert "hola desde el cmd" in completado.salida


def test_correr_conserva_el_espacio_inicial_del_formato_porcelain():
    """Regresión: sin ese espacio, `` M app/main.py`` daba «pp/main.py»."""
    completado = _correr([sys.executable, "-c", "print(' M app/main.py')"])

    assert completado.salida == " M app/main.py"
    assert _nombres_del_status(completado.salida) == ["app/main.py"]


def test_preparar_salida_ajusta_los_flujos_que_puede(monkeypatch):
    class FlujoFalso:
        """Doble de ``sys.stdout`` que registra los ajustes pedidos."""

        def __init__(self) -> None:
            self.ajustes: dict = {}

        def reconfigure(self, **kwargs) -> None:
            self.ajustes.update(kwargs)

    flujo = FlujoFalso()
    monkeypatch.setattr("sys.stdout", flujo)
    monkeypatch.setattr("sys.stderr", object())  # sin ``reconfigure``: se ignora
    _preparar_salida()

    assert flujo.ajustes == {"errors": "replace"}


# --------------------------------------------------------------------------- #
# Línea de comandos
# --------------------------------------------------------------------------- #


def test_main_publica_y_devuelve_cero():
    salidas: list[str] = []
    codigo = main(
        ["-m", "Prueba del script"],
        correr=EjecutorFalso(status=" M app/main.py"),
        consultar=ClienteFalso(),
        buscar_programa=lambda nombre: nombre,
        pausar=lambda segundos: None,
        salida=salidas.append,
    )

    assert codigo == 0
    assert any("Prueba del script" in linea for linea in salidas)
    assert any("Publicación terminada" in linea for linea in salidas)


def test_main_devuelve_dos_cuando_produccion_no_responde():
    """Con el deploy hecho y el chequeo final en rojo, el código de salida es 2.

    ``publicar.bat`` lo usa para no decir «no se subió nada» cuando en realidad
    el código **sí** quedó en GitHub y en Vercel.
    """
    salidas: list[str] = []
    codigo = main(
        [],
        correr=EjecutorFalso(status=" M app/main.py"),
        consultar=ClienteFalso(por_defecto=500),
        buscar_programa=lambda nombre: nombre,
        pausar=lambda segundos: None,
        salida=salidas.append,
    )

    assert codigo == 2
    assert any("✗" in linea for linea in salidas)
    assert any("ya quedó en GitHub y en Vercel" in linea for linea in salidas)
    assert any("--solo-deploy" in linea for linea in salidas)


def test_main_simulacro_no_ejecuta_escrituras():
    ejecutor = EjecutorFalso(status=" M app/main.py")
    salidas: list[str] = []
    codigo = main(
        ["--simulacro"],
        correr=ejecutor,
        consultar=ClienteFalso(),
        buscar_programa=lambda nombre: nombre,
        pausar=lambda segundos: None,
        salida=salidas.append,
    )

    assert codigo == 0
    assert ejecutor.escrituras() == []
    assert any("Simulacro terminado" in linea for linea in salidas)


def test_main_con_una_opcion_inexistente_aborta_sin_publicar():
    """``argparse`` sale con 2; aquí ese código significa «se publicó con fallas»."""
    ejecutor = EjecutorFalso(status=" M app/main.py")
    codigo = main(
        ["--no-existe"],
        correr=ejecutor,
        consultar=ClienteFalso(),
        salida=lambda texto: None,
    )

    assert codigo == 1
    assert ejecutor.secuencia() == []


def test_main_con_opciones_contradictorias_devuelve_uno():
    salidas: list[str] = []
    codigo = main(
        ["--sin-deploy", "--solo-deploy"],
        correr=EjecutorFalso(),
        consultar=ClienteFalso(),
        salida=salidas.append,
    )

    assert codigo == 1
    assert any("se contradicen" in linea for linea in salidas)


# --------------------------------------------------------------------------- #
# Control de calidad (pytest): es el primer paso y frena todo
# --------------------------------------------------------------------------- #


def test_el_control_de_calidad_usa_el_mismo_interprete_y_aborta_en_rojo():
    vistas: list[list[str]] = []
    salidas: list[str] = []

    def correr(comando, *, capturar=True, tiempo_espera=None):
        vistas.append(list(comando))
        return Completado(
            1,
            "FAILED tests/test_x.py::test_uno - AssertionError: 1 != 2\n"
            "==================== 1 failed, 2 passed in 0.42s ====================",
        )

    with pytest.raises(ErrorPublicacion) as error:
        control_de_calidad(correr, salida=salidas.append)

    assert vistas == [[sys.executable, "-m", "pytest"]]
    assert error.value.rojo is True  # el mensaje se imprime en rojo
    assert "publicación abortada" in str(error.value)
    assert "código 1" in str(error.value)
    pistas = " ".join(error.value.pistas)
    assert "tests/test_x.py::test_uno" in pistas  # nombra lo que falló
    assert "No se publica código con errores" in pistas
    assert any("FAILED tests/test_x.py::test_uno" in linea for linea in salidas)


def test_el_control_de_calidad_no_imprime_nada_cuando_todo_pasa():
    salidas: list[str] = []

    def correr(comando, *, capturar=True, tiempo_espera=None):
        return Completado(0, "456 passed, 1 skipped in 34.20s")

    completado = control_de_calidad(correr, salida=salidas.append)

    assert completado.ok
    assert salidas == []
    assert resumen_de_pytest(completado.salida) == "456 passed, 1 skipped in 34.20s"


def test_las_pruebas_en_rojo_no_dejan_commits_ni_deploy():
    ejecutor = EjecutorFalso(status=" M app/main.py", fallar=("-m pytest",))
    with pytest.raises(ErrorPublicacion) as error:
        publicar_con(ejecutor, mensaje="Módulo 3")

    assert error.value.rojo is True
    assert not any(
        comando.startswith(("git add", "git commit", "git push", "vercel"))
        for comando in ejecutor.secuencia()
    )


def test_main_enmarca_el_aborto_por_pruebas_cuando_no_hay_colores():
    salidas: list[str] = []
    codigo = main(
        [],
        correr=EjecutorFalso(status=" M app/main.py", fallar=("-m pytest",)),
        consultar=ClienteFalso(),
        buscar_programa=lambda nombre: nombre,
        pausar=lambda segundos: None,
        salida=salidas.append,
    )

    assert codigo == 1
    assert salidas.count("=" * 68) == 2  # mensaje enmarcado: se ve igual sin color
    assert any(
        "no se publica código con errores" in linea.lower() for linea in salidas
    )


def test_main_imprime_en_rojo_el_aborto_por_pruebas(monkeypatch):
    # Consola interactiva de mentira: así `_preparar_salida` habilita el color.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("scripts.publicar._COLORES", False)
    monkeypatch.setattr("scripts.publicar._consola_utf8", lambda: True)
    monkeypatch.setattr(
        "scripts.publicar._consola_interactiva", lambda flujo=None: True
    )
    monkeypatch.setattr(
        "scripts.publicar._habilitar_color_en_windows", lambda: True
    )
    salidas: list[str] = []
    codigo = main(
        [],
        correr=EjecutorFalso(status=" M app/main.py", fallar=("-m pytest",)),
        consultar=ClienteFalso(),
        buscar_programa=lambda nombre: nombre,
        pausar=lambda segundos: None,
        salida=salidas.append,
    )

    assert codigo == 1
    rojas = [linea for linea in salidas if linea.startswith(ROJO)]
    assert rojas, "el aborto por pruebas tiene que salir en rojo"
    assert "publicación abortada" in rojas[0]
    assert "=" * 68 not in salidas  # con color no hace falta el marco


def test_fallas_de_pytest_lee_el_resumen():
    texto = (
        "FAILED tests/test_x.py::test_uno - AssertionError\n"
        "ERROR tests/test_y.py::test_dos - RuntimeError\n"
        "1 failed, 1 error, 3 passed in 1.00s"
    )

    assert fallas_de_pytest(texto) == [
        "tests/test_x.py::test_uno",
        "tests/test_y.py::test_dos",
    ]
    assert fallas_de_pytest("3 passed in 0.10s") == []


def test_mostrar_salida_recorta_por_arriba_y_deja_pasar_todo():
    lineas = [f"linea {indice}" for indice in range(10)]
    recortadas: list[str] = []
    todas: list[str] = []

    mostrar_salida(recortadas.append, "\n".join(lineas), maximo=3)
    mostrar_salida(todas.append, "\n".join(lineas), maximo=None)

    assert recortadas == ["  linea 7", "  linea 8", "  linea 9"]
    assert len(todas) == 10


# --------------------------------------------------------------------------- #
# GitHub: remoto, mensajes de commit y pistas de error
# --------------------------------------------------------------------------- #


def test_el_asunto_automatico_respeta_el_formato_pedido():
    momento = datetime(2026, 10, 15, 8, 30)

    assert asunto_automatico(momento) == (
        "Actualización automática - Módulos Unificados [2026-10-15]"
    )
    assert momento_legible(momento) == "15/10/2026 08:30"


def test_el_cuerpo_del_commit_lleva_la_fecha_la_hora_y_los_archivos():
    cuerpo = cuerpo_del_commit(["app/main.py"], datetime(2026, 10, 15, 8, 30))

    assert "15/10/2026 08:30" in cuerpo
    assert "Archivos incluidos (1)" in cuerpo
    assert "- app/main.py" in cuerpo


def test_el_mensaje_manual_tiene_prioridad_sobre_el_automatico():
    ejecutor = EjecutorFalso(status=" M app/main.py")
    publicar_con(ejecutor, mensaje="Módulo 3 listo")

    commit = next(c for c in ejecutor.secuencia() if c.startswith("git commit"))
    assert "Módulo 3 listo" in commit
    assert "Actualización automática" not in commit


def test_exigir_remoto_devuelve_la_url_de_origin():
    ejecutor = EjecutorFalso()

    assert exigir_remoto("main", ejecutor) == (
        "https://github.com/msappia-spec/odontologico.git"
    )


def test_sin_remoto_origin_la_publicacion_aborta_sin_commitear():
    ejecutor = EjecutorFalso(status=" M app/main.py", remoto="")
    with pytest.raises(ErrorPublicacion) as error:
        publicar_con(ejecutor)

    assert "origin" in str(error.value)
    assert "git remote add origin" in " ".join(error.value.pistas)
    assert not any(
        comando.startswith(("git add", "git commit", "git push", "vercel"))
        for comando in ejecutor.secuencia()
    )


@pytest.mark.parametrize(
    ("detalle", "esperado"),
    [
        ("! [rejected] main -> main (non-fast-forward)", "git pull --rebase"),
        (
            "fatal: Authentication failed for 'https://github.com/msappia/x'",
            "gh auth login",
        ),
        ("remote: Repository not found.", "git remote -v"),
        ("remote: error: GH006: Protected branch update failed", "protegida"),
        ("fatal: unable to access", "volvé a correr el comando"),
    ],
)
def test_las_pistas_del_push_explican_los_fallos_tipicos(detalle, esperado):
    assert any(esperado in pista for pista in pistas_de_push(detalle))


def test_un_push_rechazado_agrega_las_pistas_al_error():
    ejecutor = EjecutorFalso(
        status=" M app/main.py",
        fallar=("git push",),
        salida_fallo="! [rejected] main -> main (fetch first)",
    )
    with pytest.raises(ErrorPublicacion) as error:
        publicar_con(ejecutor)

    assert "git pull --rebase origin main" in " ".join(error.value.pistas)
    assert "git add -A" in ejecutor.secuencia()  # el commit local no se pierde


# --------------------------------------------------------------------------- #
# Vercel: enlace del proyecto, salida del deploy y dominio de producción
# --------------------------------------------------------------------------- #


def test_url_de_produccion_toma_el_ultimo_dominio_que_imprime_la_cli():
    muestra = (
        "Vercel CLI 33.0.1\n"
        "Inspect: https://vercel.com/marcozface-2235/odontologico/abc123 "
        "(copied to clipboard)\n"
        "Production: https://odontologico-abc123-marcozface-2235.vercel.app [2s]\n"
        "https://odontologico-omega.vercel.app [aliased]\n"
    )

    assert url_de_produccion(muestra) == "https://odontologico-omega.vercel.app"
    assert url_de_produccion("") == URL_POR_DEFECTO


def test_el_deploy_captura_la_salida_y_verifica_ese_dominio():
    salidas: list[str] = []
    cliente = ClienteFalso()
    ejecutor = EjecutorFalso(
        status=" M app/main.py",
        salidas={
            "vercel --prod": (
                "Production: https://odontologico-prueba.vercel.app [1s]\n"
            )
        },
    )
    resultado = publicar_con(ejecutor, cliente, salida=salidas.append)

    assert resultado.desplegado is True
    assert resultado.url == "https://odontologico-prueba.vercel.app"
    assert cliente.pedidas and all(
        url.startswith("https://odontologico-prueba.vercel.app")
        for url in cliente.pedidas
    )
    assert any("Production: https://odontologico-prueba" in c for c in salidas)
    assert any("deploy terminado" in linea for linea in salidas)


def test_proyecto_enlazado_detecta_la_carpeta_y_las_variables(tmp_path):
    assert proyecto_enlazado(tmp_path, entorno={}) is False

    (tmp_path / ".vercel").mkdir()
    (tmp_path / ".vercel" / "repo.json").write_text("{}", encoding="utf-8")

    assert proyecto_enlazado(tmp_path, entorno={}) is True
    assert (
        proyecto_enlazado(
            tmp_path / "vacio",
            entorno={"VERCEL_PROJECT_ID": "prj_x", "VERCEL_ORG_ID": "team_x"},
        )
        is True
    )


def test_sin_enlace_a_vercel_da_las_instrucciones(tmp_path):
    with pytest.raises(ErrorPublicacion) as error:
        exigir_proyecto_enlazado(tmp_path)

    assert any("vercel link" in pista for pista in error.value.pistas)


def test_el_deploy_no_corre_sin_enlace_al_proyecto(monkeypatch):
    monkeypatch.setattr("scripts.publicar.proyecto_enlazado", lambda *a, **k: False)
    ejecutor = EjecutorFalso(status=" M app/main.py")
    with pytest.raises(ErrorPublicacion) as error:
        publicar_con(ejecutor)

    assert "vercel --prod --yes" not in ejecutor.secuencia()
    assert any("vercel link" in pista for pista in error.value.pistas)


# --------------------------------------------------------------------------- #
# Consola: colores ANSI y UTF-8
# --------------------------------------------------------------------------- #


def test_rojo_respeta_los_colores_activos(monkeypatch):
    monkeypatch.setattr("scripts.publicar._COLORES", False)
    assert rojo("cuidado") == "cuidado"

    monkeypatch.setattr("scripts.publicar._COLORES", True)
    assert rojo("cuidado") == f"{ROJO}cuidado{RESET}"


def test_los_colores_se_activan_solo_en_una_consola_interactiva(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("scripts.publicar._COLORES", False)
    monkeypatch.setattr("scripts.publicar._consola_utf8", lambda: True)
    monkeypatch.setattr(
        "scripts.publicar._habilitar_color_en_windows", lambda: True
    )

    monkeypatch.setattr(
        "scripts.publicar._consola_interactiva", lambda flujo=None: False
    )
    _preparar_salida()
    assert colores_activos() is False  # salida redirigida: nada de códigos ANSI

    monkeypatch.setattr(
        "scripts.publicar._consola_interactiva", lambda flujo=None: True
    )
    _preparar_salida()
    assert colores_activos() is True


def test_no_color_apaga_los_colores(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("scripts.publicar._COLORES", False)
    monkeypatch.setattr(
        "scripts.publicar._consola_interactiva", lambda flujo=None: True
    )
    _preparar_salida()

    assert colores_activos() is False


def test_la_opcion_sin_colores_apaga_los_codigos_ansi(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("scripts.publicar._COLORES", False)
    monkeypatch.setattr("scripts.publicar._consola_utf8", lambda: True)
    monkeypatch.setattr(
        "scripts.publicar._consola_interactiva", lambda flujo=None: True
    )
    monkeypatch.setattr(
        "scripts.publicar._habilitar_color_en_windows", lambda: True
    )
    salidas: list[str] = []
    codigo = main(
        ["--simulacro", "--sin-colores"],
        correr=EjecutorFalso(status=" M app/main.py"),
        consultar=ClienteFalso(),
        buscar_programa=lambda nombre: nombre,
        pausar=lambda segundos: None,
        salida=salidas.append,
    )

    assert codigo == 0
    assert colores_activos() is False
    assert not any("\033[" in linea for linea in salidas)
    assert any("Simulacro terminado" in linea for linea in salidas)


def test_main_sin_tests_no_corre_pytest():
    ejecutor = EjecutorFalso(status=" M app/main.py")
    main(
        ["--sin-tests"],
        correr=ejecutor,
        consultar=ClienteFalso(),
        buscar_programa=lambda nombre: nombre,
        pausar=lambda segundos: None,
        salida=lambda texto: None,
    )

    assert not any("-m pytest" in comando for comando in ejecutor.secuencia())
