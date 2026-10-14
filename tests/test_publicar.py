"""Pruebas de ``scripts/publicar.py``.

Todo el flujo se ejercita con **dobles**: un ejecutor de comandos falso (anota
las órdenes en vez de correrlas) y un cliente HTTP falso. Así las pruebas no
llaman a git, ni a la CLI de Vercel, ni tocan la red ni el repositorio real.
"""

from datetime import datetime
from urllib.parse import urlparse

import pytest

from scripts.publicar import (
    Completado,
    Consulta,
    ErrorPublicacion,
    _nombres_del_status,
    _preparar_salida,
    archivos_modificados,
    asunto_automatico,
    commits_pendientes,
    cuerpo_del_commit,
    main,
    publicar,
    verificar_produccion,
)

# Mismos subcomandos que el script trata como consultas de sólo lectura.
SOLO_LECTURA = ("rev-parse", "status", "rev-list", "log", "diff", "ls")


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
    ) -> None:
        self.comandos: list[list[str]] = []
        self.status = status
        self.rama = rama
        self.pendientes = pendientes
        self.fallar = tuple(fallar)

    def __call__(self, comando, *, capturar=True, cwd=None, tiempo_espera=None):
        partes = [str(parte) for parte in comando]
        self.comandos.append(partes)
        union = " ".join(partes)
        if any(trozo in union for trozo in self.fallar):
            return Completado(1, "un error cualquiera")
        if partes[0] == "git":
            if partes[1] == "rev-parse":
                return Completado(0, self.rama)
            if partes[1] == "status":
                return Completado(0, self.status)
            if partes[1] == "rev-list":
                return Completado(0, self.pendientes)
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


def test_main_devuelve_uno_cuando_produccion_no_responde():
    salidas: list[str] = []
    codigo = main(
        [],
        correr=EjecutorFalso(status=" M app/main.py"),
        consultar=ClienteFalso(por_defecto=500),
        buscar_programa=lambda nombre: nombre,
        pausar=lambda segundos: None,
        salida=salidas.append,
    )

    assert codigo == 1
    assert any("✗" in linea for linea in salidas)


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
