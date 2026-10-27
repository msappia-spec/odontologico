"""Migraciones aditivas de esquema para bases que ya tienen datos.

``Base.metadata.create_all`` crea las tablas que faltan, pero **no agrega
columnas** a una tabla existente. El proyecto todavía no usa Alembic, así que
una base creada con una versión anterior (por ejemplo la PostgreSQL de Neon en
producción) puede quedar con ``doctores`` sin ``tipo_pago``/``valor_pago`` y
responder HTTP 500 en cuanto el ORM intenta leer o guardar un profesional.

Este módulo centraliza esos ``ALTER TABLE ... ADD COLUMN`` y se ejecuta desde
``init_db`` en cada arranque:

* es **idempotente**: inspecciona las columnas y sólo agrega las que faltan;
* es **aditivo**: no borra, renombra ni reescribe datos;
* todas las columnas agregadas son anulables o tienen ``DEFAULT``, por lo que
  las filas existentes quedan válidas;
* funciona tanto en SQLite (desarrollo/pruebas) como en PostgreSQL (Vercel).

En PostgreSQL, ``tipo_pago`` se agrega como ``VARCHAR(15) NOT NULL DEFAULT
'DUENO'``: los profesionales que ya existían quedan como dueños, que es el
comportamiento histórico.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from sqlalchemy import Engine, inspect
from sqlalchemy.exc import OperationalError

logger = logging.getLogger("app.db")


class ColumnaNueva(NamedTuple):
    """Columna que debe existir y su DDL según el motor de base de datos."""

    tabla: str
    columna: str
    ddl_sqlite: str
    ddl_postgresql: str


#: Booleanos que agrega la ficha médica ampliada (Historia Clínica General).
_BOOLEANOS_FICHA_MEDICA: tuple[str, ...] = (
    "padre_vive",
    "madre_vive",
    "hermanos",
    "hermanos_sanos",
    "enfermedad_actual",
    "tratamiento_medico",
    "deporte",
    "deporte_malestar",
    "alergia_anestesia",
    "alergia_penicilina",
    "cicatriza_bien",
    "sangra_mucho",
    "colageno",
    "fiebre_reumatica",
    "diabetes",
    "diabetes_controlada",
    "anticoagulantes",
    "chagas",
    "problemas_renales",
    "ulcera_gastrica",
    "hepatitis",
    "problemas_hepaticos",
    "convulsiones",
    "epilepsia",
    "sifilis_gonorrea",
    "enfermedad_infectocontagiosa",
    "transfusiones",
    "operaciones",
    "problemas_respiratorios",
    "fuma",
    "otra_enfermedad",
    "consulta_previa",
    "medicacion_previa",
    "medicacion_previa_resultados",
    "dolor",
    "traumatismo_dental",
    "fractura_dental",
    "dificultad_hablar",
    "dificultad_masticar",
    "dificultad_abrir_boca",
    "dificultad_tragar",
    "labios_anormal",
    "lengua_anormal",
    "paladar_anormal",
    "piso_boca_anormal",
    "carrillos_anormal",
    "rebordes_anormal",
    "trigono_anormal",
    "retromolar_anormal",
    "lesiones_manchas",
    "lesiones_abultamiento",
    "lesiones_ulceraciones",
    "lesiones_ampollas",
    "sangrado_encias",
    "pus_boca",
    "movilidad_dental",
    "dientes_altos",
    "cara_hinchada",
    "cara_hinchada_hielo",
    "cara_hinchada_calor",
    "sarro",
    "enfermedad_periodontal",
)

#: Textos libres de la ficha médica ampliada.
_TEXTOS_FICHA_MEDICA: tuple[str, ...] = (
    "padre_patologias",
    "madre_patologias",
    "hermanos_detalle",
    "enfermedad_actual_detalle",
    "tratamiento_medico_detalle",
    "medicamentos_ultimos_5_anios",
    "alergia_otros",
    "fiebre_reumatica_proteccion",
    "diabetes_tratamiento",
    "problemas_cardiacos_detalle",
    "anticoagulantes_frecuencia",
    "chagas_tratamiento",
    "problemas_hepaticos_detalle",
    "epilepsia_medicacion",
    "enfermedad_infectocontagiosa_detalle",
    "operaciones_detalle",
    "operaciones_cuando",
    "problemas_respiratorios_detalle",
    "otra_enfermedad_detalle",
    "tratamientos_alternativos",
    "medico_clinico",
    "centro_derivacion",
    "medicacion_previa_detalle",
    "medicacion_previa_desde",
    "dolor_zona",
    "dolor_calmante",
    "traumatismo_cuando",
    "traumatismo_como",
    "fractura_cual",
    "fractura_tratamiento",
    "examen_bucal_detalle",
    "lesiones_otros",
    "sangrado_encias_cuando",
    "pus_boca_donde",
    "cara_hinchada_otros",
    "indice_placa",
    "diagnostico_presuntivo",
    "plan_tratamiento",
    "observaciones",
)

#: Opciones cortas (dolor, hepatitis, higiene bucal).
_OPCIONES_FICHA_MEDICA: tuple[str, ...] = (
    "hepatitis_tipo",
    "dolor_intensidad",
    "dolor_duracion",
    "dolor_desencadenante",
    "dolor_estimulo",
    "dolor_localizacion",
    "higiene_bucal",
)

#: Enteros opcionales (meses de embarazo, momentos de azúcar diario).
_ENTEROS_FICHA_MEDICA: tuple[str, ...] = (
    "embarazo_meses",
    "momentos_azucar",
)


# Columnas que se fueron incorporando a tablas que ya existían. El orden no es
# relevante: cada entrada se agrega sólo si la tabla existe y la columna falta.
COLUMNAS_NUEVAS: tuple[ColumnaNueva, ...] = (
    # Módulo 3: modalidad de liquidación del profesional.
    ColumnaNueva(
        "doctores",
        "tipo_pago",
        "VARCHAR(15) NOT NULL DEFAULT 'DUENO'",
        "VARCHAR(15) NOT NULL DEFAULT 'DUENO'",
    ),
    ColumnaNueva("doctores", "valor_pago", "FLOAT", "DOUBLE PRECISION"),
    # Datos administrativos del paciente (DNI y cobertura).
    ColumnaNueva("pacientes", "dni", "VARCHAR(20)", "VARCHAR(20)"),
    ColumnaNueva("pacientes", "obra_social_id", "INTEGER", "INTEGER"),
    ColumnaNueva("pacientes", "numero_afiliado", "VARCHAR(60)", "VARCHAR(60)"),
    # Datos filiatorios de la Historia Clínica General (los carga la ficha
    # médica y se sincronizan en el paciente). Aditivos y anulables.
    ColumnaNueva("pacientes", "fecha_nacimiento", "DATE", "DATE"),
    *(
        ColumnaNueva("pacientes", columna, tipo, tipo)
        for columna, tipo in (
            ("estado_civil", "VARCHAR(30)"),
            ("nacionalidad", "VARCHAR(60)"),
            ("domicilio", "VARCHAR(200)"),
            ("profesion", "VARCHAR(120)"),
            ("titular", "VARCHAR(120)"),
            ("lugar_trabajo", "VARCHAR(120)"),
            ("jerarquia", "VARCHAR(60)"),
        )
    ),
    # Correlación de la respuesta de WhatsApp con el turno.
    ColumnaNueva(
        "turnos", "whatsapp_mensaje_id", "VARCHAR(128)", "VARCHAR(128)"
    ),
    # Módulo 4: ficha médica ampliada (Historia Clínica General del consultorio).
    *(
        ColumnaNueva(
            "antecedentes_salud",
            columna,
            "BOOLEAN NOT NULL DEFAULT 0",
            "BOOLEAN NOT NULL DEFAULT FALSE",
        )
        for columna in _BOOLEANOS_FICHA_MEDICA
    ),
    *(
        ColumnaNueva("antecedentes_salud", columna, "TEXT", "TEXT")
        for columna in _TEXTOS_FICHA_MEDICA
    ),
    *(
        ColumnaNueva(
            "antecedentes_salud", columna, "VARCHAR(20)", "VARCHAR(20)"
        )
        for columna in _OPCIONES_FICHA_MEDICA
    ),
    *(
        ColumnaNueva("antecedentes_salud", columna, "INTEGER", "INTEGER")
        for columna in _ENTEROS_FICHA_MEDICA
    ),
    ColumnaNueva("antecedentes_salud", "fecha_plan", "DATE", "DATE"),
)


def _ddl_para(columna: ColumnaNueva, dialecto: str) -> str:
    """Devuelve el DDL correcto para el dialecto del engine."""
    if dialecto == "postgresql":
        return columna.ddl_postgresql
    return columna.ddl_sqlite


def _sentencia_alter(columna: ColumnaNueva, dialecto: str) -> str:
    """Arma el ``ALTER TABLE`` tolerante a arranques concurrentes.

    Vercel puede iniciar varias instancias de la Function a la vez y todas
    ejecutan ``init_db``. PostgreSQL soporta ``ADD COLUMN IF NOT EXISTS``, así
    que la segunda instancia no falla si la primera ya migró. SQLite no admite
    esa cláusula; allí el arranque es de un solo proceso y, por las dudas, el
    bucle ignora el error de columna duplicada.
    """
    if dialecto == "postgresql":
        return (
            f'ALTER TABLE "{columna.tabla}" '
            f'ADD COLUMN IF NOT EXISTS "{columna.columna}" '
            f"{_ddl_para(columna, dialecto)}"
        )
    return (
        f'ALTER TABLE "{columna.tabla}" '
        f'ADD COLUMN "{columna.columna}" '
        f"{_ddl_para(columna, dialecto)}"
    )


def aplicar_columnas_faltantes(engine: Engine) -> list[str]:
    """Agrega las columnas conocidas que falten en una base existente.

    Devuelve la lista de columnas aplicadas (vacía si el esquema ya estaba al
    día). Los errores de DDL se propagan a propósito: si la base no permite
    migrar, el arranque debe fallar con un mensaje claro en vez de seguir
    sirviendo endpoints que responderán 500.
    """
    inspector = inspect(engine)
    tablas = set(inspector.get_table_names())
    pendientes: list[ColumnaNueva] = []

    for columna in COLUMNAS_NUEVAS:
        if columna.tabla not in tablas:
            # La tabla la crea ``create_all`` con todas sus columnas.
            continue
        existentes = {
            definicion["name"]
            for definicion in inspector.get_columns(columna.tabla)
        }
        if columna.columna not in existentes:
            pendientes.append(columna)

    if not pendientes:
        logger.info("Esquema aditivo al día: no hay columnas para migrar.")
        return []

    dialecto = engine.dialect.name
    aplicadas: list[str] = []
    with engine.begin() as conexion:
        for columna in pendientes:
            try:
                conexion.exec_driver_sql(
                    _sentencia_alter(columna, dialecto)
                )
            except OperationalError as exc:
                # SQLite no tiene ``IF NOT EXISTS`` para ADD COLUMN: si otro
                # proceso la agregó entre la inspección y el ALTER, el estado
                # final ya es el correcto.
                if (
                    dialecto != "sqlite"
                    or "duplicate column name" not in str(exc).lower()
                ):
                    raise
                logger.info(
                    "La columna %s.%s ya había sido agregada por otro proceso.",
                    columna.tabla,
                    columna.columna,
                )
                continue
            aplicadas.append(f"{columna.tabla}.{columna.columna}")
            logger.info(
                "Columna agregada en %s: %s.%s",
                dialecto,
                columna.tabla,
                columna.columna,
            )

    logger.info(
        "Migración aditiva aplicada (%s columnas): %s",
        len(aplicadas),
        ", ".join(aplicadas),
    )
    return aplicadas
