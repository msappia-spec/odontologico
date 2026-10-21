"""Pruebas del Módulo 4: ficha pública de salud (anamnesis).

Cubren el camino completo del enlace que recibe el paciente por WhatsApp:

1. **Token HMAC**: firma determinística, ids confiables y rechazo de cualquier
   alteración (payload, firma, versión o formato).
2. **GET público**: contexto del turno, ficha pendiente, token inválido (400) y
   turno inexistente (404). La lectura no crea la historia clínica.
3. **POST público**: crea la historia si hace falta, guarda la declaración,
   calcula la alerta médica y permite corregirla sin duplicar filas.
4. **Integración con el odontólogo**: el detalle de la historia expone la ficha
   y sus motivos de alerta; el recordatorio de WhatsApp agrega el enlace sólo
   mientras la ficha esté pendiente.
"""

import base64
import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.anamnesis_token import (
    TokenAnamnesisInvalidoError,
    construir_enlace_anamnesis,
    firmar_token_anamnesis,
    validar_token_anamnesis,
)
from app.core.config import settings
from app.core.datetime_utils import utcnow
from app.models.antecedentes_salud import AntecedentesSalud
from app.models.historia_clinica import HistoriaClinica
from app.models.turno import Turno
from app.schemas.antecedentes_salud import DeclaracionSaludCreate
from app.services import anamnesis_service, whatsapp_service

API = "/api/v1"


def _crear_turno(db_session, doctor, paciente, *, dias=2, hora=10):
    """Turno futuro del paciente, con relaciones recargables desde la sesión."""
    inicio = (utcnow() + timedelta(days=dias)).replace(
        hour=hora, minute=0, second=0, microsecond=0
    )
    turno = Turno(
        paciente_id=paciente.id,
        doctor_id=doctor.id,
        fecha_hora_inicio=inicio,
        fecha_hora_fin=inicio + timedelta(minutes=30),
    )
    db_session.add(turno)
    db_session.commit()
    db_session.refresh(turno)
    return turno


def _b64(valor: bytes) -> str:
    return base64.urlsafe_b64encode(valor).decode("ascii").rstrip("=")


def _firmar_payload(payload) -> str:
    """Firma un payload arbitrario con el secreto efectivo (para casos límite)."""
    crudo = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    firma = hmac.new(
        settings.anamnesis_secreto_efectivo.encode("utf-8"),
        crudo,
        hashlib.sha256,
    ).digest()
    return f"{_b64(crudo)}.{_b64(firma)}"


def _decodificar_payload(token: str) -> dict:
    parte = token.split(".", 1)[0]
    relleno = "=" * (-len(parte) % 4)
    return json.loads(base64.urlsafe_b64decode(parte + relleno).decode("utf-8"))


def _payload_completo():
    return {
        "alergias_medicamentos": "Penicilina",
        "presion_alta": True,
        "problemas_cardiacos": False,
        "medicacion_actual": "Enalapril 10 mg",
        "embarazo": False,
    }


# ---------------------------------------------------------------------------
# 1) Token firmado
# ---------------------------------------------------------------------------


def test_token_firmado_es_deterministico_y_devuelve_los_ids():
    token = firmar_token_anamnesis(12, 34)
    assert token == firmar_token_anamnesis(12, 34)
    assert token.count(".") == 1

    datos = validar_token_anamnesis(token)
    assert datos.turno_id == 12
    assert datos.paciente_id == 34

    payload = _decodificar_payload(token)
    assert payload == {"paciente_id": 34, "turno_id": 12, "v": 1}


def test_token_con_payload_alterado_se_rechaza():
    token = firmar_token_anamnesis(12, 34)
    _, firma = token.split(".", 1)
    payload = _decodificar_payload(token)
    payload["turno_id"] = 999
    crudo = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    alterado = f"{_b64(crudo)}.{firma}"

    with pytest.raises(TokenAnamnesisInvalidoError):
        validar_token_anamnesis(alterado)


def test_token_con_firma_alterada_se_rechaza():
    token = firmar_token_anamnesis(12, 34)
    payload, firma = token.split(".", 1)
    firma_rota = ("A" if firma[0] != "A" else "B") + firma[1:]

    with pytest.raises(TokenAnamnesisInvalidoError):
        validar_token_anamnesis(f"{payload}.{firma_rota}")


def test_token_con_version_o_ids_invalidos_se_rechaza():
    casos = [
        _firmar_payload({"v": 99, "turno_id": 1, "paciente_id": 1}),
        _firmar_payload({"v": 1, "turno_id": 0, "paciente_id": 1}),
        _firmar_payload({"v": 1, "turno_id": 1, "paciente_id": -5}),
        _firmar_payload({"v": 1, "turno_id": "no-numero", "paciente_id": 1}),
        _firmar_payload({"v": 1, "turno_id": 1}),
        _firmar_payload([1, 2]),
    ]
    for token in casos:
        with pytest.raises(TokenAnamnesisInvalidoError):
            validar_token_anamnesis(token)


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "sin-punto",
        "a.b.c",
        "a.b",
        "x" * 513 + ".firma",
        "..",
    ],
)
def test_token_con_formato_invalido_se_rechaza(token):
    with pytest.raises(TokenAnamnesisInvalidoError):
        validar_token_anamnesis(token)


def test_construir_enlace_usa_la_url_publica():
    enlace = construir_enlace_anamnesis(5, 8)
    assert enlace == f"{settings.url_publica}/anamnesis/{firmar_token_anamnesis(5, 8)}"
    assert validar_token_anamnesis(enlace.rsplit("/", 1)[1]).turno_id == 5


# ---------------------------------------------------------------------------
# 2) GET público
# ---------------------------------------------------------------------------


def test_get_publico_muestra_contexto_y_no_crea_historia(
    client, db_session, doctor, paciente
):
    turno = _crear_turno(db_session, doctor, paciente)
    token = firmar_token_anamnesis(turno.id, paciente.id)

    r = client.get(f"{API}/anamnesis/{token}")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["turno_id"] == turno.id
    assert body["paciente_id"] == paciente.id
    assert body["paciente_nombre"] == paciente.nombre
    assert body["doctor_nombre"] == doctor.nombre
    assert body["doctor_especialidad"] == doctor.especialidad
    assert body["fecha_hora_inicio"].startswith(
        turno.fecha_hora_inicio.strftime("%Y-%m-%dT%H:%M")
    )
    assert body["completado"] is False
    assert body["antecedentes_salud"] is None

    # El enlace es una credencial: no se devuelve ni se filtran datos de contacto.
    assert token not in r.text
    assert paciente.telefono not in r.text
    assert "telefono" not in body

    # La consulta de sólo lectura no crea la ficha clínica.
    assert db_session.scalar(select(func.count(HistoriaClinica.id))) == 0
    assert db_session.scalar(select(func.count(AntecedentesSalud.id))) == 0


def test_get_publico_rechaza_token_invalido(client):
    r = client.get(f"{API}/anamnesis/no-es-un-token")
    assert r.status_code == 400
    assert "no es válido" in r.json()["detail"].lower()


def test_get_publico_responde_404_si_el_turno_no_existe(client, paciente):
    token = firmar_token_anamnesis(99999, paciente.id)
    r = client.get(f"{API}/anamnesis/{token}")
    assert r.status_code == 404
    assert "turno" in r.json()["detail"].lower()


def test_get_publico_rechaza_token_de_otro_paciente(
    client, db_session, doctor, paciente
):
    turno = _crear_turno(db_session, doctor, paciente)
    token = firmar_token_anamnesis(turno.id, paciente.id + 1)

    r = client.get(f"{API}/anamnesis/{token}")
    assert r.status_code == 400


def test_pagina_publica_se_sirve_sin_cache_ni_referrer(client):
    token = firmar_token_anamnesis(1, 1)
    r = client.get(f"/anamnesis/{token}")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "no-store" in r.headers["cache-control"]
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-robots-tag"] == "noindex, nofollow"
    assert "/static/anamnesis.js" in r.text
    assert "/static/anamnesis.css" in r.text

    # La hoja y el script realmente se sirven desde el mount de estáticos.
    assert client.get("/static/anamnesis.css").status_code == 200
    assert client.get("/static/anamnesis.js").status_code == 200


# ---------------------------------------------------------------------------
# 3) POST público: alta y corrección de la ficha
# ---------------------------------------------------------------------------


def test_post_crea_historia_y_completa_la_ficha_con_alerta(
    client, db_session, doctor, paciente
):
    turno = _crear_turno(db_session, doctor, paciente)
    token = firmar_token_anamnesis(turno.id, paciente.id)

    r = client.post(f"{API}/anamnesis/{token}", json=_payload_completo())
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["completado"] is True
    assert body["mensaje"].startswith("¡Gracias!")
    a = body["antecedentes_salud"]
    assert a["alergias_medicamentos"] == "Penicilina"
    assert a["presion_alta"] is True
    assert a["problemas_cardiacos"] is False
    assert a["medicacion_actual"] == "Enalapril 10 mg"
    assert a["embarazo"] is False
    assert a["completado"] is True
    assert a["fecha_completado"]
    assert a["alerta_medica"] is True
    assert a["motivos_alerta"] == [
        "Alergias a medicamentos",
        "Presión arterial alta",
        "Medicación actual",
    ]
    assert token not in r.text

    db_session.expire_all()
    historia = db_session.execute(select(HistoriaClinica)).scalars().one()
    assert historia.paciente_id == paciente.id
    assert historia.numero_ficha == f"HC-{paciente.id:06d}"

    antecedentes = db_session.execute(select(AntecedentesSalud)).scalars().one()
    assert antecedentes.historia_clinica_id == historia.id
    assert antecedentes.completado is True
    assert antecedentes.fecha_completado is not None
    assert antecedentes.alerta_medica is True


def test_post_normaliza_textos_y_permite_ficha_sin_riesgos(
    client, db_session, doctor, paciente
):
    turno = _crear_turno(db_session, doctor, paciente)
    token = firmar_token_anamnesis(turno.id, paciente.id)

    r = client.post(
        f"{API}/anamnesis/{token}",
        json={
            "alergias_medicamentos": "   Penicilina   ",
            "medicacion_actual": "   ",
            "presion_alta": False,
            "problemas_cardiacos": False,
            "embarazo": False,
        },
    )
    assert r.status_code == 200, r.text
    a = r.json()["antecedentes_salud"]

    assert a["alergias_medicamentos"] == "Penicilina"
    assert a["medicacion_actual"] is None
    assert a["alerta_medica"] is True
    assert a["motivos_alerta"] == ["Alergias a medicamentos"]


def test_post_sin_antecedentes_deja_la_alerta_apagada(
    client, db_session, doctor, paciente
):
    turno = _crear_turno(db_session, doctor, paciente)
    token = firmar_token_anamnesis(turno.id, paciente.id)

    r = client.post(f"{API}/anamnesis/{token}", json={})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["completado"] is True
    a = body["antecedentes_salud"]
    assert a["completado"] is True
    assert a["alergias_medicamentos"] is None
    assert a["medicacion_actual"] is None
    assert a["alerta_medica"] is False
    assert a["motivos_alerta"] == []


def test_post_rechaza_textos_demasiado_largos(client, db_session, doctor, paciente):
    turno = _crear_turno(db_session, doctor, paciente)
    token = firmar_token_anamnesis(turno.id, paciente.id)

    r = client.post(
        f"{API}/anamnesis/{token}",
        json={"alergias_medicamentos": "x" * 1001},
    )
    assert r.status_code == 422
    assert db_session.scalar(select(func.count(HistoriaClinica.id))) == 0


def test_reenviar_corrige_la_misma_ficha_sin_duplicarla(
    client, db_session, doctor, paciente
):
    turno = _crear_turno(db_session, doctor, paciente)
    token = firmar_token_anamnesis(turno.id, paciente.id)

    primero = client.post(f"{API}/anamnesis/{token}", json=_payload_completo())
    assert primero.status_code == 200, primero.text
    id_antecedentes = primero.json()["antecedentes_salud"]["id"]

    segundo = client.post(
        f"{API}/anamnesis/{token}",
        json={
            "alergias_medicamentos": "",
            "presion_alta": False,
            "problemas_cardiacos": False,
            "medicacion_actual": "",
            "embarazo": False,
        },
    )
    assert segundo.status_code == 200, segundo.text
    body = segundo.json()

    assert body["antecedentes_salud"]["id"] == id_antecedentes
    assert body["antecedentes_salud"]["alergias_medicamentos"] is None
    assert body["antecedentes_salud"]["alerta_medica"] is False
    assert body["antecedentes_salud"]["motivos_alerta"] == []

    db_session.expire_all()
    assert db_session.scalar(select(func.count(HistoriaClinica.id))) == 1
    assert db_session.scalar(select(func.count(AntecedentesSalud.id))) == 1


def test_get_posterior_devuelve_la_ficha_ya_completada(
    client, db_session, doctor, paciente
):
    turno = _crear_turno(db_session, doctor, paciente)
    token = firmar_token_anamnesis(turno.id, paciente.id)
    assert (
        client.post(f"{API}/anamnesis/{token}", json=_payload_completo()).status_code
        == 200
    )

    r = client.get(f"{API}/anamnesis/{token}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["completado"] is True
    assert body["antecedentes_salud"]["completado"] is True
    assert body["antecedentes_salud"]["presion_alta"] is True


def test_post_reintenta_una_vez_si_la_fila_se_crea_en_paralelo(
    db_session, doctor, paciente, monkeypatch
):
    """Dos envíos simultáneos: el segundo commit choca y se reintenta."""
    turno = _crear_turno(db_session, doctor, paciente)
    token = firmar_token_anamnesis(turno.id, paciente.id)

    commit_original = db_session.commit
    llamadas = {"n": 0}

    def commit_con_colision():
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise IntegrityError(
                "INSERT INTO antecedentes_salud ...",
                {},
                Exception("UNIQUE constraint failed"),
            )
        return commit_original()

    monkeypatch.setattr(db_session, "commit", commit_con_colision)

    resultado = anamnesis_service.guardar_declaracion_publica(
        db_session,
        token,
        DeclaracionSaludCreate(**_payload_completo()),
    )

    assert llamadas["n"] == 2
    assert resultado.completado is True
    assert resultado.antecedentes_salud is not None
    assert resultado.antecedentes_salud.alerta_medica is True


# ---------------------------------------------------------------------------
# 4) Integración con la historia del odontólogo y el recordatorio
# ---------------------------------------------------------------------------


def test_detalle_de_historia_expone_la_ficha_y_la_alerta(
    client, db_session, doctor, paciente
):
    turno = _crear_turno(db_session, doctor, paciente)
    token = firmar_token_anamnesis(turno.id, paciente.id)
    assert client.post(f"{API}/anamnesis/{token}", json=_payload_completo()).status_code == 200

    r = client.get(f"{API}/pacientes/{paciente.id}/historia-clinica")
    assert r.status_code == 200, r.text
    detalle = r.json()

    assert detalle["id"] is not None
    assert detalle["antecedentes_salud"]["alergias_medicamentos"] == "Penicilina"
    assert detalle["alerta_medica"] is True
    assert detalle["motivos_alerta"] == [
        "Alergias a medicamentos",
        "Presión arterial alta",
        "Medicación actual",
    ]


def test_detalle_sin_ficha_de_salud_queda_pendiente_sin_alerta(client, paciente):
    r = client.get(f"{API}/pacientes/{paciente.id}/historia-clinica")
    assert r.status_code == 200, r.text
    detalle = r.json()

    assert detalle["antecedentes_salud"] is None
    assert detalle["alerta_medica"] is False
    assert detalle["motivos_alerta"] == []


def test_recordatorio_agrega_el_enlace_solo_mientras_falta_la_ficha(
    client, db_session, doctor, paciente
):
    turno = _crear_turno(db_session, doctor, paciente)

    pendiente = whatsapp_service.construir_mensaje_recordatorio(turno)
    assert "Antes de la consulta, completá tu ficha de salud" in pendiente
    enlace = pendiente.split("enlace: ", 1)[1].strip()
    assert enlace.startswith(f"{settings.url_publica}/anamnesis/")
    datos = validar_token_anamnesis(enlace.rsplit("/", 1)[1])
    assert (datos.turno_id, datos.paciente_id) == (turno.id, paciente.id)

    # El payload interactivo de la Cloud API también lleva el enlace.
    payload = whatsapp_service.construir_payload_recordatorio(turno)
    assert "/anamnesis/" in payload["interactive"]["body"]["text"]

    token = firmar_token_anamnesis(turno.id, paciente.id)
    assert client.post(f"{API}/anamnesis/{token}", json={}).status_code == 200

    db_session.expire_all()
    turno_recargado = db_session.get(Turno, turno.id)
    completo = whatsapp_service.construir_mensaje_recordatorio(turno_recargado)
    assert "/anamnesis/" not in completo
    assert completo.endswith("¡Muchas gracias!")
