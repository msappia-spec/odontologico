"""Pruebas del módulo de presupuestos.

Cubren:

1. **Alta**: cabecera, renglones, totales y dirección tomada del paciente.
2. **Listado y filtros**: por paciente, nombre/DNI y código de prestación.
3. **Reemplazo**: el ``PUT`` cambia la cabecera y sustituye todos los trabajos.
4. **Validación**: paciente inexistente, lista vacía e importes negativos.
5. **Código y detalles**: el código se normaliza y la descripción conserva los
   saltos de línea para agregar aclaraciones.
6. **PDF**: replica la plantilla del consultorio, imprime el código y los
   importes con separador de miles, y agrega páginas si el presupuesto es largo.
"""

import pymupdf
from sqlalchemy import func, select

from app.models.presupuesto import Presupuesto, PresupuestoItem

API = "/api/v1"


def _payload(paciente_id: int, **cambios) -> dict:
    """Presupuesto de ejemplo con los dos trabajos del PDF de referencia."""
    datos = {
        "paciente_id": paciente_id,
        "fecha": "2026-09-17",
        "forma_pago": "Contado / transferencia",
        "items": [
            {
                "codigo": "11.02",
                "detalle": (
                    "Corona de Circonio ed 36 (porcelana ) monolítica por "
                    "tecnología Cad Cam ."
                ),
                "precio": 550000,
                "entrega": 150000,
            },
            {
                "codigo": "11.03",
                "detalle": "Corona de Resina ed 36 por  tecnología Cad Cam",
                "precio": 280000,
                "entrega": 80000,
            },
        ],
    }
    datos.update(cambios)
    return datos


def _crear(client, paciente_id: int, **cambios) -> dict:
    respuesta = client.post(
        f"{API}/presupuestos", json=_payload(paciente_id, **cambios)
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def _con_domicilio(client, paciente, domicilio: str) -> None:
    respuesta = client.patch(
        f"{API}/pacientes/{paciente.id}", json={"domicilio": domicilio}
    )
    assert respuesta.status_code == 200, respuesta.text


def test_crear_presupuesto_calcula_totales_y_usa_el_domicilio(
    client, paciente
):
    _con_domicilio(client, paciente, "Juan del Campillo 747 B Cofico")

    datos = _crear(client, paciente.id)

    assert datos["id"]
    assert datos["paciente_id"] == paciente.id
    assert datos["fecha"] == "2026-09-17"
    assert datos["direccion"] == "Juan del Campillo 747 B Cofico"
    assert datos["forma_pago"] == "Contado / transferencia"
    assert [item["detalle"] for item in datos["items"]] == [
        "Corona de Circonio ed 36 (porcelana ) monolítica por tecnología Cad Cam .",
        "Corona de Resina ed 36 por tecnología Cad Cam",
    ]
    assert [item["codigo"] for item in datos["items"]] == ["11.02", "11.03"]
    assert [item["orden"] for item in datos["items"]] == [0, 1]
    assert datos["total"] == 830000
    assert datos["total_entregas"] == 230000
    assert datos["saldo"] == 600000
    assert datos["paciente"]["nombre"] == paciente.nombre


def test_la_direccion_explicita_reemplaza_la_del_paciente(client, paciente):
    _con_domicilio(client, paciente, "Domicilio viejo 123")

    datos = _crear(client, paciente.id, direccion="Av. Colón 1234, 5º B")

    assert datos["direccion"] == "Av. Colón 1234, 5º B"


def test_listar_presupuestos_y_filtrar_por_paciente(client, paciente):
    creado = _crear(client, paciente.id)

    lista = client.get(f"{API}/presupuestos")
    assert lista.status_code == 200
    assert [p["id"] for p in lista.json()] == [creado["id"]]

    por_paciente = client.get(
        f"{API}/presupuestos", params={"paciente_id": paciente.id}
    )
    assert por_paciente.status_code == 200
    assert [p["id"] for p in por_paciente.json()] == [creado["id"]]

    por_nombre = client.get(
        f"{API}/presupuestos", params={"buscar": paciente.nombre}
    )
    assert por_nombre.status_code == 200
    assert [p["id"] for p in por_nombre.json()] == [creado["id"]]

    por_codigo = client.get(f"{API}/presupuestos", params={"buscar": "11.02"})
    assert por_codigo.status_code == 200
    assert [p["id"] for p in por_codigo.json()] == [creado["id"]]

    por_detalle = client.get(
        f"{API}/presupuestos", params={"buscar": "Resina ed 36"}
    )
    assert por_detalle.status_code == 200
    assert [p["id"] for p in por_detalle.json()] == [creado["id"]]

    sin_resultados = client.get(
        f"{API}/presupuestos", params={"buscar": "NO EXISTE"}
    )
    assert sin_resultados.status_code == 200
    assert sin_resultados.json() == []


def test_actualizar_reemplaza_los_trabajos_y_mantiene_el_id(
    client, db_session, paciente
):
    creado = _crear(client, paciente.id)

    respuesta = client.put(
        f"{API}/presupuestos/{creado['id']}",
        json=_payload(
            paciente.id,
            fecha="2026-10-01",
            forma_pago="Transferencia",
            items=[
                {
                    "codigo": "15.01",
                    "detalle": "Blanqueamiento dental",
                    "precio": 120000,
                    "entrega": None,
                }
            ],
        ),
    )

    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["id"] == creado["id"]
    assert datos["fecha"] == "2026-10-01"
    assert datos["forma_pago"] == "Transferencia"
    assert len(datos["items"]) == 1
    assert datos["items"][0]["codigo"] == "15.01"
    assert datos["items"][0]["detalle"] == "Blanqueamiento dental"
    assert datos["items"][0]["entrega"] is None
    assert datos["total"] == 120000
    assert datos["total_entregas"] == 0
    assert db_session.execute(
        select(func.count()).select_from(Presupuesto)
    ).scalar_one() == 1
    assert db_session.execute(
        select(func.count()).select_from(PresupuestoItem)
    ).scalar_one() == 1


def test_eliminar_presupuesto(client, db_session, paciente):
    creado = _crear(client, paciente.id)

    respuesta = client.delete(f"{API}/presupuestos/{creado['id']}")
    assert respuesta.status_code == 204
    assert respuesta.content == b""

    assert client.get(f"{API}/presupuestos/{creado['id']}").status_code == 404
    assert db_session.execute(
        select(func.count()).select_from(Presupuesto)
    ).scalar_one() == 0
    assert db_session.execute(
        select(func.count()).select_from(PresupuestoItem)
    ).scalar_one() == 0


def test_validaciones_y_referencias_inexistentes(client, paciente):
    assert client.post(
        f"{API}/presupuestos",
        json=_payload(999999),
    ).status_code == 404

    vacio = client.post(
        f"{API}/presupuestos",
        json=_payload(paciente.id, items=[]),
    )
    assert vacio.status_code == 422

    negativo = client.post(
        f"{API}/presupuestos",
        json=_payload(
            paciente.id,
            items=[{"detalle": "Prueba", "precio": -1}],
        ),
    )
    assert negativo.status_code == 422

    detalle_vacio = client.post(
        f"{API}/presupuestos",
        json=_payload(
            paciente.id,
            items=[{"detalle": "   ", "precio": 1000}],
        ),
    )
    assert detalle_vacio.status_code == 422

    codigo_largo = client.post(
        f"{API}/presupuestos",
        json=_payload(
            paciente.id,
            items=[
                {
                    "codigo": "X" * 31,
                    "detalle": "Prueba",
                    "precio": 1000,
                }
            ],
        ),
    )
    assert codigo_largo.status_code == 422

    detalle_largo = client.post(
        f"{API}/presupuestos",
        json=_payload(
            paciente.id,
            items=[{"detalle": "X" * 1001, "precio": 1000}],
        ),
    )
    assert detalle_largo.status_code == 422

    assert client.get(f"{API}/presupuestos/999999").status_code == 404
    assert client.put(
        f"{API}/presupuestos/999999", json=_payload(paciente.id)
    ).status_code == 404
    assert client.delete(f"{API}/presupuestos/999999").status_code == 404
    assert client.get(f"{API}/presupuestos/999999/pdf").status_code == 404


def test_codigo_normalizado_y_descripcion_multilinea(client, paciente):
    """El código se guarda en mayúsculas y la descripción conserva sus líneas."""
    creado = _crear(
        client,
        paciente.id,
        items=[
            {
                "codigo": " 11.02 ",
                "detalle": "Corona de Circonio\nColor: A2\nControl oclusal",
                "precio": 100000,
                "entrega": None,
            }
        ],
    )

    item = creado["items"][0]
    assert item["codigo"] == "11.02"
    assert item["detalle"] == "Corona de Circonio\nColor: A2\nControl oclusal"

    sin_codigo = _crear(
        client,
        paciente.id,
        items=[
            {
                "codigo": "   ",
                "detalle": "Práctica sin código",
                "precio": 1000,
                "entrega": None,
            }
        ],
    )
    assert sin_codigo["items"][0]["codigo"] is None

    respuesta = client.get(f"{API}/presupuestos/{creado['id']}/pdf")
    assert respuesta.status_code == 200, respuesta.text
    documento = pymupdf.open("pdf", respuesta.content)
    texto = "\n".join(pagina.get_text() for pagina in documento)
    assert "Cód. 11.02" in texto
    assert "Corona de Circonio" in texto
    assert "Color: A2" in texto
    assert "Control oclusal" in texto


def test_el_pdf_replica_el_documento_y_los_importes(client, paciente):
    respuesta_paciente = client.patch(
        f"{API}/pacientes/{paciente.id}",
        json={
            "nombre": "FRUCCIO NATALIA",
            "domicilio": "Juan del Campillo 747 B Cofico",
        },
    )
    assert respuesta_paciente.status_code == 200, respuesta_paciente.text
    creado = _crear(client, paciente.id)

    respuesta = client.get(f"{API}/presupuestos/{creado['id']}/pdf")

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.headers["content-type"] == "application/pdf"
    assert "inline;" in respuesta.headers["content-disposition"]
    assert respuesta.content.startswith(b"%PDF")

    documento = pymupdf.open("pdf", respuesta.content)
    assert documento.page_count == 1
    pagina = documento[0]
    assert round(pagina.rect.width, 2) == 595.28
    assert round(pagina.rect.height, 2) == 841.89

    texto = pagina.get_text()
    assert "NOMBRE DEL PACIENTE: FRUCCIO NATALIA" in texto
    assert "Juan del Campillo 747 B Cofico" in texto
    assert "Septiembre 2026" in texto
    assert "Contado / transferencia" in texto
    assert "PRESUPUESTO" in texto
    assert "DETALLE DE LOS" in texto
    assert "TRABAJOS" in texto
    assert "PRECIO" in texto
    assert "Cód. 11.02" in texto
    assert "Cód. 11.03" in texto
    assert "Corona de Circonio ed 36" in texto
    assert "tecnología Cad Cam" in texto
    assert "Corona de Resina ed 36" in texto
    assert "Cam" in texto
    assert "$550.000" in texto
    assert "$280.000" in texto
    assert "Forma de Pago : Entrega: $150.000" in texto
    assert "Forma de Pago : Entrega: $80.000" in texto

    # El logo del consultorio viaja embebido y la banda conserva el color.
    assert len(pagina.get_images(full=True)) == 1
    bandas = [
        dibujo
        for dibujo in pagina.get_drawings()
        if dibujo.get("fill")
        and abs(dibujo["fill"][0] - 0.82739) < 0.002
    ]
    assert bandas, "No se encontró la banda color arena del presupuesto."

    # El título usa el rojo apagado del original y el precio queda a la derecha.
    spans = [
        span
        for bloque in pagina.get_text("dict")["blocks"]
        for linea in bloque.get("lines", [])
        for span in linea.get("spans", [])
    ]
    titulo = next(span for span in spans if span["text"] == "PRESUPUESTO")
    assert titulo["color"] == 12278876
    precio = next(span for span in spans if span["text"] == "$550.000")
    assert abs(precio["bbox"][2] - 540.2) < 1.5


def test_un_presupuesto_largo_continua_en_otra_pagina(client, paciente):
    _con_domicilio(client, paciente, "Córdoba 123")
    items = [
        {
            "detalle": (
                f"Trabajo {numero}: corona de circonio monolítica por "
                "tecnología Cad Cam con control y ajuste oclusal."
            ),
            "precio": 100000 + numero,
            "entrega": 10000,
        }
        for numero in range(1, 21)
    ]
    creado = _crear(client, paciente.id, items=items)

    respuesta = client.get(f"{API}/presupuestos/{creado['id']}/pdf")

    assert respuesta.status_code == 200, respuesta.text
    documento = pymupdf.open("pdf", respuesta.content)
    assert documento.page_count >= 2
    texto = "\n".join(pagina.get_text() for pagina in documento)
    assert "Trabajo 20" in texto
    assert texto.count("NOMBRE DEL PACIENTE:") == documento.page_count
    assert texto.count("PRESUPUESTO") >= documento.page_count
