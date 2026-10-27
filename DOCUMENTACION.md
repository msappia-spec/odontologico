# Documentación Técnica — Franciosi Odontología

> **Documento de arquitectura, módulos y base de datos**
>
> Relevamiento: **26/10/2026** · Versión de la aplicación: **1.0.0**
>
> Alcance: código fuente completo del repositorio `c:\Sistema\Odontologico`
> (backend FastAPI, frontend estático, scripts, migraciones y pruebas).
>
> Fuente de verdad: los modelos SQLAlchemy, los servicios, los routers y
> `app/db/migraciones.py`. Cuando una descripción del README y el código
> difieren, este documento sigue el código.

---

## 0. Resumen ejecutivo

**Franciosi Odontología** es un sistema de gestión para un consultorio
odontológico. Cubre cuatro módulos funcionales:

| Módulo | Función |
|---|---|
| **1. Turnos y WhatsApp** | Doctores, pacientes, agenda semanal, turnos, recordatorios, webhook y FAQ Bot. |
| **2. Historia clínica** | Ficha única por paciente, evoluciones inmutables y archivos adjuntos. |
| **3. Facturación y caja** | Prestaciones, aranceles por obra social, cierre de visitas, caja, liquidaciones y PDF. |
| **4. Anamnesis** | Ficha pública de salud previa a la consulta, con token HMAC. |

**Stack:** Python 3.14, FastAPI 0.143, SQLAlchemy 2.1, Pydantic 2.14,
SQLite (desarrollo) / PostgreSQL (producción), frontend HTML + CSS + JavaScript
vanilla, despliegue en Vercel.

**Base de datos:** 17 tablas, 17 entidades ORM principales y 6 enumeraciones de
dominio. No hay tablas de usuarios, auditoría, pagos ni archivos binarios.

**Pruebas:** 31 archivos `test_*.py` (más `conftest.py`) y **704 casos** (suite completa verificada
al generar este documento).

**Conclusión de arquitectura:** es un **monolito modular por capas**, no un
sistema de microservicios ni un monolito desordenado. La separación de
responsabilidades es buena para el tamaño actual; el principal límite es que los
módulos comparten proceso, base y sesión, y que el frontend principal concentra
mucha lógica en un solo archivo.

---

## 1. Evaluación de estructura y modularidad

### 1.1. Veredicto

**Monolito modular organizado por capas técnicas, con cuatro módulos de negocio
y un frontend desacoplado por HTTP.**

No es:

- **microservicios**: hay un único proceso/despliegue, una sola base y un solo
  `FastAPI()`;
- **arquitectura hexagonal/DDD completa**: no hay puertos/adaptadores formales,
  repositorios ni unidades de trabajo;
- **monolito de un solo archivo**: las rutas son delgadas, la lógica está en
  servicios y los contratos están en schemas.

Sí es:

- una aplicación **monolítica en despliegue**;
- **modular en código**, con un archivo de servicio por agregado;
- **estratificada** en `api → schemas → services → models → db`;
- con **cuatro contextos funcionales** reconocibles (turnos, clínica,
  facturación, anamnesis) que comparten el mismo mecanismo de persistencia.

### 1.2. Evidencia concreta

| Dimensión | Observación en el código | Conclusión |
|---|---|---|
| Punto de entrada | Un único `app/main.py` crea `FastAPI`, monta estáticos y un `api_router`. | Monolito de despliegue. |
| Routers | 14 archivos en `app/api/v1/endpoints/`, agrupados por recurso. | Separación por dominio en la capa HTTP. |
| Servicios | 17 archivos en `app/services/`, uno por agregado o caso de uso. | Lógica de negocio fuera de las rutas. |
| Modelos | 17 tablas en 17 archivos, todas registradas en un único `Base.metadata`. | Modelo de datos compartido. |
| Schemas | 13 archivos Pydantic, con contratos de entrada/salida separados. | Contratos explícitos y validación centralizada. |
| Acceso a datos | Los servicios usan `Session` y `select()` directamente; no hay repositorios. | Acoplamiento servicio–ORM. |
| Transacciones | Cada servicio hace `commit()`; no hay Unit of Work explícita. | Transacciones locales por operación. |
| Frontend | Panel de 6.527 líneas en `app.js`, más `calendar.js`; vista móvil, ficha médica, odontograma y PDF de la ficha compartidos. | UI desacoplada por API, pero panel poco granular. |
| Despliegue | `vercel.json` declara `framework: fastapi`; un solo deploy. | Escalado y fallos acoplados. |
| Pruebas | SQLite en memoria + `TestClient`, 31 archivos `test_*.py`. | Buena testabilidad de la capa HTTP y servicios. |

### 1.3. Fortalezas

1. **Rutas delgadas.** Los endpoints validan el contrato y delegan; por ejemplo
   `crear_turno()` llama a `turno_service.crear_turno()`. La lógica no vive en
   los decoradores HTTP.
2. **Servicios con responsabilidad identificable.** `facturacion_service`,
   `disponibilidad_service`, `whatsapp_service`, etc. tienen un límite temático
   claro y docstrings extensos.
3. **Contratos Pydantic separados.** `Create`, `Update`, `Replace` y `Read` se
   distinguen; los campos calculados (`monto_total`, `alerta_medica`) se derivan
   en el schema.
4. **Errores de dominio centralizados.** `AppError` y sus subclases se traducen
   a HTTP en `main.py`; los servicios no repiten `try/except` de presentación.
5. **Reglas de integridad en varias capas.** Por ejemplo, el DNI único se
   valida en el servicio y se defiende con un índice único; la inmutabilidad de
   las evoluciones se defiende en rutas, servicio y eventos ORM.
6. **Soporte dual SQLite/PostgreSQL.** `UTCDateTime` normaliza la zona horaria y
   las migraciones aditivas funcionan en ambos motores.
7. **Documentación y pruebas alineadas.** Los tests cubren contratos HTTP,
   migraciones, esquema, flujos completos y reportes.

### 1.4. Acoplamientos y límites reales

1. **Acoplamiento servicio–servicio.** `facturacion_service` importa
   `arancel_service`, `obra_social_service` y `prestacion_service`;
   `liquidacion_service` importa `facturacion_service`; `turno_service` importa
   `disponibilidad_service` y `doctor_service`. Es un DAG razonable, pero no hay
   interfaces: cambiar la firma de un servicio impacta a sus consumidores.
2. **Acoplamiento servicio–ORM.** No existe una capa de repositorio. Los
   servicios conocen columnas, relaciones y `select()` de SQLAlchemy. Esto es
   simple y productivo hoy, pero dificulta sustituir la persistencia o testear
   sin base.
3. **Unidad de trabajo incompleta.** Varias operaciones hacen `commit()` dentro
   del servicio. Las operaciones compuestas (por ejemplo, guardar un convenio en
   lote) sí resuelven todo antes de un único commit, pero no hay un patrón
   uniforme de transacción para todos los casos.
4. **Modelo compartido por todos los módulos.** Una sola `Base` y un solo
   engine significan que cualquier cambio de esquema afecta a todo el sistema.
5. **Frontend principal monolítico.** `app.js` contiene estado, render, fetch,
   modales, facturación, disponibilidad, historia y anamnesis. `calendar.js` usa
   el puente `window.OdontoApp`; esa indirección evita duplicar lógica, pero
   aumenta el tamaño y el riesgo de regresiones.
6. **Contrato HTTP repetido en el navegador.** Los estados y etiquetas
   (`PENDIENTE`, `CONFIRMADO`, etc.) se replican en `app.js`, `medico.js`,
   `whatsapp_bot_service` y los reportes PDF. Un cambio de estado exige tocar
   varios lugares.
7. **Seguridad no modular.** No hay una capa de autenticación/autorización que
   envuelva los routers; la API administrativa está abierta. Es el límite más
   importante para usar el sistema con datos reales en Internet.

### 1.5. Madurez relativa

| Atributo | Nivel | Motivo |
|---|---|---|
| Separación de conceptos | **Alto** | API, schemas, servicios, modelos y core están separados. |
| Cohesión por dominio | **Medio-alto** | Carpetas y archivos por recurso; no hay paquetes de feature autocontenidos. |
| Encapsulamiento de datos | **Medio** | ORM expuesto en servicios; sin repositorios. |
| Testabilidad | **Alto** | `TestClient`, SQLite en memoria, dependencia `get_db` sustituible. |
| Independencia de despliegue | **Bajo** | Un solo proceso y una sola base. |
| Evolución del esquema | **Medio-bajo** | `create_all` + migraciones aditivas manuales; sin Alembic. |
| Seguridad | **Bajo** | Sin autenticación de panel/API; webhook POST sin firma. |
| Modularidad del frontend | **Medio-bajo** | `app.js` concentra demasiadas vistas. |

### 1.6. Recomendación de evolución

No hace falta partir el sistema en microservicios. Para el tamaño actual conviene
**mantener el monolito modular** y mejorar sus límites internos:

1. agregar autenticación y autorización por rol (secretaría, odontólogo, admin);
2. introducir Alembic para migraciones versionadas y reversibles;
3. separar `app.js` en módulos por vista (`turnos`, `facturacion`, `catalogos`,
   `whatsapp`, `historia`) sin cambiar el contrato HTTP;
4. encapsular el acceso a datos con repositorios sólo donde aporte valor
   (facturación y turnos), no como ceremonia universal;
5. reemplazar `Float` por `Numeric(12,2)` o mantener `Decimal` en la frontera de
   persistencia;
6. agregar una cola/outbox para envíos de WhatsApp y trabajos programados;
7. documentar y automatizar el scheduler de recordatorios (hoy es un endpoint).

---

## 2. Estructura del proyecto

### 2.1. Árbol de directorios

```
Odontologico/
├── app/
│   ├── __init__.py                  # Paquete y versión (1.0.0)
│   ├── main.py                      # FastAPI, lifespan, CORS, errores, /health
│   ├── api/v1/
│   │   ├── router.py                # Agrega los 13 routers de endpoints
│   │   └── endpoints/
│   │       ├── doctores.py          # CRUD y baja lógica de profesionales
│   │       ├── disponibilidad.py    # Agenda semanal, excepciones y bloques
│   │       ├── obras_sociales.py    # Catálogo de coberturas
│   │       ├── pacientes.py         # Alta, búsqueda y edición
│   │       ├── turnos.py            # Alta, listado y PATCH de turnos
│   │       ├── historias_clinicas.py# Ficha y evoluciones inmutables
│   │       ├── anamnesis.py         # Ficha pública y panel de salud
│   │       ├── prestaciones.py      # Catálogo de prácticas facturables
│   │       ├── aranceles.py         # Aranceles, convenios e importador
│   │       ├── facturacion.py       # Caja, presentaciones y liquidaciones
│   │       ├── tareas.py            # Disparo de recordatorios
│   │       ├── whatsapp.py          # Webhook y recordatorios Click-to-Chat
│   │       └── whatsapp_menu.py     # CRUD del menú del FAQ Bot
│   ├── core/
│   │   ├── config.py                # Settings (pydantic-settings/.env)
│   │   ├── datetime_utils.py        # utcnow, UTC naive, rangos de día
│   │   ├── exceptions.py            # Errores de dominio → HTTP
│   │   ├── logging_config.py        # Logging a stdout
│   │   ├── money.py                 # Redondeo/suma con Decimal
│   │   ├── pagos.py                 # Validación de modalidad de pago
│   │   ├── anamnesis_token.py       # Token HMAC de la ficha pública
│   │   ├── pdf.py                   # Generador PDF sin dependencias
│   │   └── planilla_aranceles.py    # CSV/XLSX sin openpyxl
│   ├── db/
│   │   ├── base_class.py            # DeclarativeBase
│   │   ├── base.py                  # Importa todos los modelos (metadata)
│   │   ├── session.py               # engine, SessionLocal, get_db
│   │   ├── types.py                 # UTCDateTime (TIMESTAMPTZ ↔ UTC naive)
│   │   ├── init_db.py               # create_all + migración + seed
│   │   └── migraciones.py           # ALTER TABLE aditivos e idempotentes
│   ├── models/
│   │   ├── enums.py                 # Estados y tipos del dominio
│   │   ├── doctor.py                # doctores
│   │   ├── paciente.py              # pacientes
│   │   ├── obra_social.py           # obras_sociales
│   │   ├── disponibilidad.py        # disponibilidad_doctores
│   │   ├── disponibilidad_especifica.py # excepciones por fecha
│   │   ├── turno.py                 # turnos
│   │   ├── historia_clinica.py      # historias_clinicas
│   │   ├── evolucion_medica.py      # evoluciones_medicas (inmutable)
│   │   ├── archivo_adjunto.py       # archivos_adjuntos
│   │   ├── prestacion.py            # prestaciones
│   │   ├── arancel_obra_social.py   # aranceles_obra_social
│   │   ├── movimiento_caja.py       # movimientos_caja + detalles
│   │   ├── opcion_menu_whatsapp.py  # opciones_menu_whatsapp
│   │   └── antecedentes_salud.py    # antecedentes_salud
│   ├── schemas/                     # Contratos Pydantic
│   │   ├── doctor.py, paciente.py, turno.py, disponibilidad.py
│   │   ├── obra_social.py, prestacion.py, arancel.py
│   │   ├── historia_clinica.py, antecedentes_salud.py
│   │   ├── facturacion.py, whatsapp.py
│   │   └── opcion_menu_whatsapp.py
│   ├── services/
│   │   ├── doctor_service.py
│   │   ├── paciente_service.py
│   │   ├── obra_social_service.py
│   │   ├── prestacion_service.py
│   │   ├── disponibilidad_service.py
│   │   ├── turno_service.py
│   │   ├── historia_clinica_service.py
│   │   ├── anamnesis_service.py
│   │   ├── ficha_pdf_service.py
│   │   ├── arancel_service.py
│   │   ├── facturacion_service.py
│   │   ├── liquidacion_service.py
│   │   ├── reporte_pdf_service.py
│   │   ├── whatsapp_menu_service.py
│   │   ├── whatsapp_bot_service.py
│   │   └── whatsapp_service.py
│   ├── web/
│   │   └── router.py                # Sirve /, /medico y /anamnesis/{token}
│   ├── resources/
│   │   └── historia_clinica_general.pdf # Plantilla de papel del PDF de la ficha
│   └── static/
│       ├── index.html               # Panel de secretaría
│       ├── styles.css               # Estilos del panel
│       ├── app.js                   # Estado, fetch, vistas y facturación
│       ├── calendar.js              # Calendario mensual y disponibilidad
│       ├── medico.html              # Vista móvil del odontólogo
│       ├── medico.css
│       ├── medico.js                # Agenda táctil, historia y evolución
│       ├── anamnesis.html           # Ficha pública del paciente
│       ├── anamnesis.css
│       ├── anamnesis.js
│       └── img/                     # Marca, logo y favicon SVG locales
├── scripts/
│   ├── seed.py                      # Datos demo idempotentes
│   ├── seed_nomenclador_cba.py      # Nomenclador de Córdoba
│   ├── migrar_*.py                  # Migraciones legacy de SQLite
│   ├── migrar_sqlite_a_postgres.py  # Copia local → PostgreSQL
│   ├── replicar_datos_api.py        # Copia local → app desplegada vía HTTP
│   ├── enviar_ficha.py              # Enlace de la ficha de salud por API
│   ├── limpiar_base.py              # Limpieza transaccional de tablas
│   ├── configurar_url_vercel.py     # Guarda DATABASE_URL local de producción
│   └── publicar.py                  # pytest + git + Vercel + verificación
├── tests/                           # 31 archivos test_*.py, 704 casos
├── .env.example                     # Variables documentadas
├── requirements.txt                 # Dependencias fijas de producción
├── requirements-dev.txt             # pytest + esprima
├── pytest.ini                       # pythonpath, testpaths, warnings
├── vercel.json                      # framework: fastapi
├── publicar.bat                     # Atajo Windows de scripts.publicar
├── limpiar_vercel.bat               # Limpieza guiada de la base de Vercel/Neon
├── README.md                        # Guía funcional extensa
└── DOCUMENTACION.md                 # Este documento
```

### 2.2. Archivos de raíz

| Archivo | Función |
|---|---|
| `README.md` | Guía funcional y de uso: módulos, endpoints, despliegue y ejemplos `curl`. |
| `DOCUMENTACION.md` | Arquitectura, módulos y diccionario de datos (este archivo). |
| `requirements.txt` | Dependencias directas de runtime con versión fija. |
| `requirements-dev.txt` | `pytest==9.1.1` y `esprima==4.0.1`; incluye las de runtime. |
| `pytest.ini` | `pythonpath=.`, `testpaths=tests`, salida `-q`. |
| `.python-version` | `3.14`. |
| `.env.example` | Plantilla de variables de entorno. |
| `vercel.json` | Configuración mínima del framework FastAPI. |
| `.vercelignore` | Excluye `.venv`, bases, `tests/`, `scripts/`, logs y secretos del deploy. |
| `publicar.bat` | Ejecuta `python -m scripts.publicar` con el Python del `.venv`. |

> **Nota:** `tests/` y `scripts/` no se empaquetan en Vercel (`.vercelignore`).
> El runtime sólo necesita `app/` y las dependencias de `requirements.txt`.

---

## 3. Arquitectura y flujo de datos

### 3.1. Vista de componentes

```
┌────────────────────────── Navegador ──────────────────────────┐
│  Panel secretaría        Vista móvil        Ficha pública      │
│  index.html              medico.html        anamnesis.html     │
│  app.js + calendar.js    medico.js          anamnesis.js       │
└───────────────┬───────────────┬───────────────────┬───────────┘
                │ fetch JSON    │ fetch JSON         │ fetch JSON
                ▼               ▼                   ▼
┌──────────────────────────── FastAPI (app/main.py) ────────────┐
│  CORS · manejador AppError · /health · /static · web.router    │
│                                                                │
│  api/v1/router.py                                              │
│    ├── endpoints (HTTP + Depends(get_db))                      │
│    ├── schemas Pydantic (validación / serialización)           │
│    └── services (reglas de negocio)                            │
│             ├── models SQLAlchemy 2.0                          │
│             ├── core (fechas, dinero, tokens, PDF, planillas)  │
│             └── integración WhatsApp (httpx → Graph API)       │
└───────────────────────────────┬────────────────────────────────┘
                                │ SessionLocal / get_db
                                ▼
                 ┌──────────────────────────────┐
                 │ SQLite (local)               │
                 │ PostgreSQL (Neon/producción) │
                 └──────────────────────────────┘
```

**Componentes externos opcionales:**

- **WhatsApp Cloud API (Meta)** para envío real de recordatorios y respuestas.
  Si faltan credenciales, el envío se simula con un `wamid` ficticio.
- **Vercel** para hosting serverless y **GitHub** para respaldo del código.
- **Tailwind CDN** y **Google Fonts (Inter)** para el frontend.

### 3.2. Capas y responsabilidades

| Capa | Ubicación | Responsabilidad | No debe hacer |
|---|---|---|---|
| Presentación web | `app/static`, `app/web` | HTML, CSS, JS, navegación, fetch y render. | Reglas de negocio o SQL. |
| API HTTP | `app/api/v1/endpoints` | Declarar rutas, dependencias, códigos y response models. | Cálculos de negocio extensos. |
| Contratos | `app/schemas` | Validar entrada, normalizar, serializar salida y campos calculados. | Persistencia. |
| Negocio | `app/services` | Reglas, transacciones, consultas y coordinación entre agregados. | Conocer HTTP o HTML. |
| Dominio/persistencia | `app/models` | Tablas, columnas, relaciones, constraints y eventos ORM. | Reglas de presentación. |
| Infraestructura | `app/core`, `app/db` | Configuración, engine, sesión, migraciones, utilidades. | Reglas de un módulo funcional. |

**Dirección de dependencias:** `endpoints → services → models/db`; `schemas` se
usa en los extremos; `core` es transversal. No hay imports de `endpoints` desde
servicios ni de `services` desde modelos.

### 3.3. Ciclo de una petición

1. El navegador hace `fetch("/api/v1/...")` con JSON.
2. FastAPI resuelve la ruta, inyecta una `Session` con `Depends(get_db)` y valida
   el body/query con el schema Pydantic.
3. El endpoint llama al servicio correspondiente.
4. El servicio consulta o modifica el ORM y hace `commit()` cuando corresponde.
5. SQLAlchemy traduce a SQL para SQLite o PostgreSQL.
6. El `response_model` serializa la entidad (o un DTO) a JSON.
7. Si se lanza una subclase de `AppError`, el handler global devuelve
   `{"detail": ...}` con el `status_code` de la excepción. Los errores de
   validación de Pydantic los traduce FastAPI a `422`.
8. `get_db` hace `rollback()` ante una excepción y siempre cierra la sesión.

**Convenciones transversales:**

- fechas: `datetime` **UTC naive** en toda la aplicación;
- importes: `float` en la base, operaciones con `Decimal` y redondeo a 2
  decimales (`app/core/money.py`);
- baja de catálogos: **lógica** (`activa = false`), no física;
- errores de negocio: excepciones de `app/core/exceptions.py`;
- configuración: `settings` único, leído de `.env`/entorno.

### 3.4. Flujos funcionales

#### 3.4.1. Alta de un turno y disponibilidad

1. La secretaría elige profesional, paciente, fecha, hora y duración.
2. `turno_service.crear_turno()` valida:
   - que el doctor exista y esté **activo**;
   - que el paciente exista;
   - que el doctor no tenga otro turno vigente que se cruce
     (`estado_turno != CANCELADO`, `inicio < fin_nuevo` y `fin > inicio_nuevo`);
   - que el paciente no esté citado en otro turno vigente que se cruce
     (control de “paciente ubicuo”);
   - que `fecha_hora_fin > fecha_hora_inicio`.
3. Se persiste el turno con `PENDIENTE` / `PENDIENTE_ENVIO`.
4. El índice parcial `uq_turno_doctor_fecha_inicio` impide duplicados exactos
   mientras el turno no esté cancelado. Cancelar libera ese inicio.
5. Para ofrecer horarios, `turno_service.horarios_disponibles()`:
   - resuelve la agenda efectiva del día;
   - si hay **cualquier** excepción cargada para esa fecha, ignora la agenda
     semanal y usa `HABILITADO` menos `BLOQUEADO`;
   - si no hay excepciones, usa `DisponibilidadDoctor` del día de la semana
     (0 = domingo);
   - parte los rangos en bloques de la duración pedida (30 minutos por defecto);
   - descarta los bloques que se cruzan con turnos vigentes.

#### 3.4.2. Recordatorios y enlaces Click-to-Chat

Hay dos caminos distintos:

**A. Envío automático (`POST /api/v1/tareas/enviar-recordatorios`)**

- Busca turnos cuya `fecha_hora_inicio` cae exactamente en el día
  `hoy + N` (`RECORDATORIO_DIAS`, por defecto 3).
- Filtra `estado_turno = PENDIENTE` y `estado_whatsapp = PENDIENTE_ENVIO`.
- Arma un mensaje interactivo con botones **Confirmar** / **Cancelar**.
- Si el paciente todavía no completó la anamnesis, agrega el enlace HMAC.
- Envía por `httpx` a la Graph API si hay `WHATSAPP_PHONE_NUMBER_ID` y token;
  si no, simula el envío.
- Actualiza `estado_whatsapp` a `ENVIADO` o `FALLIDO` y guarda
  `whatsapp_mensaje_id` (wamid) cuando el envío fue exitoso.

> **No hay scheduler interno.** El endpoint está pensado para que lo invoque un
> cron externo (por ejemplo, Vercel Cron) o una tarea manual. En el repositorio
> no hay configuración de cron ni proceso en segundo plano.

**B. Click-to-Chat (`GET /api/v1/whatsapp/recordatorios-pendientes`)**

- Lista turnos no cancelados desde “ahora” hasta el final del día `hoy + N`.
- Incluye los que sigan en `PENDIENTE_ENVIO`, aunque ya estén confirmados.
- Devuelve el mensaje ya redactado y un enlace `https://wa.me/<número>?text=...`.
- La secretaría abre el enlace desde su propio WhatsApp; el panel marca el turno
  como `ENVIADO` con `PATCH /turnos/{id}` sin cambiar el estado clínico.

#### 3.4.3. Webhook de WhatsApp y FAQ Bot

**Mensaje de texto (`type: "text"`):**

1. `whatsapp_service.mensaje_de_texto()` extrae el primer mensaje con cuerpo.
2. `whatsapp_bot_service.responder_mensaje()` normaliza texto y teléfono.
3. Lee el menú activo de `opciones_menu_whatsapp`; si la tabla está vacía usa
   las cuatro opciones históricas (precio, obras sociales, ubicación,
   secretaría).
4. Si el texto es un saludo/palabra clave, envía el menú.
5. Si es una tecla válida, ejecuta el tipo de la opción:
   `TEXTO`, `PRECIO`, `OBRAS_SOCIALES`, `UBICACION` o `SECRETARIA`.
6. Si no coincide, **no responde** y registra `Atención Manual Requerida` con el
   teléfono, el texto y el motivo.

**Respuesta de botón (`interactive.button_reply`):**

1. Se toma `context.id` (wamid del recordatorio original).
2. Se busca `Turno.whatsapp_mensaje_id == context.id`.
3. Se mapea el botón a `CONFIRMADO` o `CANCELADO`.
4. Se marca `estado_whatsapp = RESPONDIDO` y se confirma la transacción.

> **Seguridad pendiente:** el `POST /whatsapp/webhook` no valida la firma
> `X-Hub-Signature-256` de Meta. El `GET` sí valida `hub.verify_token`. Para
> producción con número real conviene agregar la verificación HMAC del body.

#### 3.4.4. Historia clínica y evoluciones

1. La ficha (`HistoriaClinica`) es **única por paciente** (`uq_historia_paciente`)
   y se crea de forma automática con la primera evolución o con la primera
   anamnesis enviada.
2. `numero_ficha` se deriva del id del paciente: `HC-{id:06d}` (por ejemplo,
   `HC-000123`) y es único (`uq_historia_numero_ficha`).
3. `POST /pacientes/{id}/evoluciones` valida paciente y doctor, crea la ficha si
   falta y guarda la evolución con sus adjuntos en una transacción.
4. La línea de tiempo se lee siempre con `ORDER BY fecha_atencion DESC, id DESC`.
5. **Inmutabilidad:**
   - la API sólo expone `POST` y `GET` (no hay `PUT`, `PATCH` ni `DELETE`);
   - el servicio no tiene funciones de edición/baja;
   - los eventos `before_update` y `before_delete` de `EvolucionMedica` lanzan
     `RegistroInmutableError` (HTTP 403).
6. `ArchivoAdjunto` guarda una **URL**, no el binario. La subida del archivo es
   responsabilidad externa al sistema.

> La inmutabilidad es de **aplicación/ORM**. Una sentencia SQL directa contra la
> base puede modificar una evolución; para bloquearlo también a nivel motor
> harían falta triggers o permisos de base de datos.

#### 3.4.5. Ficha médica pública y odontograma

1. El recordatorio incluye `/anamnesis/<token>` sólo si la ficha está pendiente.
2. El token tiene la forma `base64url(payload).base64url(HMAC-SHA256)`.
   Payload: `{"v":1,"turno_id":...,"paciente_id":...}`. Es determinístico y no
   requiere una tabla de enlaces.
3. `GET /api/v1/anamnesis/{token}` valida la firma, carga el turno y comprueba
   que el paciente del token coincida con el del turno. **No crea** la historia.
4. `POST /api/v1/anamnesis/{token}`:
   - crea la historia clínica si el paciente no la tenía;
   - crea o actualiza la fila única `AntecedentesSalud` con los bloques
     familiares, personales y odontológicos del PDF (el examen bucal y el
     diagnóstico no viajan por acá);
   - sincroniza los **datos filiatorios** del encabezado (fecha de nacimiento,
     estado civil, nacionalidad, domicilio, profesión, titular, lugar de trabajo
     y jerarquía) en el registro del paciente; los campos vacíos no borran lo
     que ya estaba cargado;
   - marca `completado = true` y `fecha_completado = utcnow()`;
   - devuelve el resumen con `alerta_medica` y `motivos_alerta`.
5. `PUT /api/v1/anamnesis/panel/{turno_id}` permite que la secretaría o el
   profesional carguen la ficha completa desde el mostrador, sin token: además
   del cuestionario acepta el **examen bucal** y el **diagnóstico/plan**, y
   sincroniza los filiatorios igual que el enlace público.
6. `GET/PUT /api/v1/pacientes/{id}/odontograma` es el gráfico **aparte** que el
   profesional completa después de revisar al paciente: toca un diente y elige
   el color (o elige el color y toca varios dientes), en notación FDI y con las
   referencias del PDF: rojo (existente), azul (requerida) o X (ausente/a
   extraer); se guardan además prótesis, coronas y cantidad de dientes
   existentes. El `GET` no crea la historia; el `PUT` reemplaza los dientes.
7. `GET /api/v1/anamnesis/panel` lista hasta 500 turnos no cancelados de la
   ventana pedida, con el estado de la ficha y el enlace firmado para reenviar.
8. `GET /api/v1/anamnesis/panel/{turno_id}/enlace` arma el enlace firmado, el
   texto sugerido y el Click-to-Chat (`wa.me`) de **cualquier** turno no
   cancelado, aunque esté fuera de la ventana del panel: la secretaría busca por
   paciente y no por fecha. El token se firma siempre en el servidor. El botón
   *Enviar ficha* de cada fila abre ese Click-to-Chat en una pestaña nueva
   (WhatsApp Web con el mensaje ya escrito) y la secretaría lo envía y lo
   confirma desde su propio WhatsApp; *Más opciones* abre el modal con el texto y
   el enlace para copiar o para mandar por API. El mensaje cierra con el enlace
   en su propia línea (WhatsApp sólo lo pinta en azul así), el modal lo muestra
   como link clickeable y avisa cuando la URL es `localhost`. El envío por API
   (`canal=api`) manda el texto con `preview_url` para que la Cloud API arme la
   tarjeta del enlace.
9. `POST /api/v1/anamnesis/panel/{turno_id}/enviar` es el único camino de envío
   automático:
   - `canal=manual` (por defecto) **no envía nada**: devuelve el enlace y el
     mensaje para copiarlos en WhatsApp Web o desde el celular;
   - `canal=api` usa la WhatsApp Cloud API y queda `simulado=true` cuando faltan
     las credenciales (`settings.whatsapp_envio_real` es falso);
   - ninguno de los dos toca `turno.estado_whatsapp`: el recordatorio sigue
     pendiente y la ficha se completa recién con el `POST` público del paciente.
10. La CLI `python -m scripts.enviar_ficha` repite ese circuito desde la consola
   (busca por teléfono, DNI o nombre, elige el turno vigente más próximo y pide
   el enlace a la app desplegada) sin conocer el secreto ni la base. Muestra la
   fecha y hora tal como las guarda la agenda (la misma del panel y del mensaje):
   no las convierte de zona.
11. `GET /api/v1/pacientes/{id}/historia-clinica/pdf` imprime la ficha sobre la
    **plantilla de papel** (`app/resources/historia_clinica_general.pdf`):
    - abre una copia de las dos páginas oficiales y escribe encima el paciente,
      sus filiatorios y las respuestas;
    - ubica cada dato por la **etiqueta** del formulario (no por coordenadas
      fijas) y reconoce las casillas SI/NO por la fuente Wingdings 2: dibuja una
      X en la que eligió el paciente;
    - un campo vacío no se imprime (no inventa un «NO» en una ficha pendiente);
    - el odontograma, el detalle del examen bucal y los textos que no entran en
      las cuatro líneas del diagnóstico, del plan o de las observaciones van al
      **anexo** (el formulario marca «Continúa en Anexo Nº 1»);
    - responde `application/pdf` con `Content-Disposition: inline`; el
      profesional se elige con `?doctor_id=` y, si falta, se usa el del
      odontograma o el de la última evolución. Un paciente sin ficha imprime el
      formulario con sus datos y el resto en blanco (`200`, no `404`).
    El botón *Imprimir* está en la tarjeta de la ficha del panel y de `/medico`,
    y en el modal de carga de la secretaría. Si se reemplaza la plantilla, la
    prueba `tests/test_ficha_pdf.py` avisa cuando falta una etiqueta.

#### 3.4.6. Facturación de la visita y caja

1. `GET /turnos/{id}/prestaciones-disponibles` muestra cada práctica activa con
   el arancel que corresponde al paciente.
2. `POST /turnos/{id}/presupuesto` calcula el cartel del mostrador **sin
   escribir**.
3. `POST /turnos/{id}/facturar` resuelve cada práctica:
   - **sin obra social**: cobra `valor_base` como efectivo; no hay saldo de OS;
   - **con obra social y arancel**: `monto_cubre_os` va a `monto_pendiente_os` y
     `monto_coseguro_paciente` a `monto_efectivo`;
   - **con obra social sin arancel**: responde `422`, salvo
     `permitir_particular=true`, que cobra `valor_base` y deja una advertencia.
4. Crea **un** `MovimientoCaja` por turno (`uq_movimiento_caja_turno`) y sus
   `DetalleMovimientoCaja` con código, descripción, cantidad e importes
   **congelados**.
5. Si el turno estaba `PENDIENTE`, pasa a `CONFIRMADO`. Un turno `CANCELADO` no
   se puede facturar.
6. El movimiento nace `PENDIENTE` si queda saldo de OS; si no hay saldo (paciente
   particular), nace `COBRADO`.
7. La caja diaria (`GET /movimientos-caja`) y su resumen filtran por rango de
   fechas y estados; `cobrar` y `debitar` cierran la orden de la obra social.

**Ciclo de `estado_pago`:**

```
PENDIENTE → EN_PROCESO_LIQUIDACION → COBRADO
                                  └→ DEBITADO_OS
```

La presentación del período (`POST /liquidaciones/obras-sociales`) pasa todos los
movimientos `PENDIENTE` del mes a `EN_PROCESO_LIQUIDACION` en una transacción.
Si no hay pendientes, responde `422`.

#### 3.4.7. Liquidación de profesionales y PDF

- La producción de un profesional es la suma de `monto_efectivo` +
  `monto_pendiente_os` de sus movimientos del mes.
- `DUENO`: porcentaje de producción (`valor_pago`; sin valor = 100 %).
- `COMISION`: porcentaje obligatorio (0-100).
- `SUELDO`: importe fijo mensual, independiente de la producción.
- Los reportes PDF se generan con `app/core/pdf.py` (implementación propia, sin
  ReportLab) a partir de los mismos DTO que la API; no recalculan ni consultan la
  base.

#### 3.4.8. Flujo del frontend

- **Panel (`/`)**: `index.html` carga `app.js` y `calendar.js`.
  `app.js` expone `window.OdontoApp`; `calendar.js` consume ese puente en lugar
  de duplicar el estado. La carga inicial hace `GET /doctores`, `/pacientes`,
  `/turnos?limit=500` y `/obras-sociales`, y luego pide en paralelo
  recordatorios, fichas de salud, disponibilidad y menú del bot.
- **Vista móvil (`/medico`)**: `medico.js` es autónomo, no carga `app.js`.
  Recuerda el profesional en `localStorage`, consulta turnos por rango de fechas,
  agenda con `POST /turnos` y edita/cancela con `PATCH /turnos/{id}`. Se refresca
  cada 60 segundos y al volver a la pestaña.
- **Anamnesis (`/anamnesis/{token}`)**: `anamnesis.js` lee el token del path,
  hace `GET` y `POST` contra `/api/v1/anamnesis/{token}` y usa `textContent`
  para insertar datos del paciente (evita XSS).
- **Comunicación**: `fetch` relativo al mismo origen; no hay build, npm ni
  framework de frontend. Tailwind y la fuente Inter se cargan por CDN.

### 3.5. Configuración y entornos

- `Settings` (`app/core/config.py`) es una instancia única creada al importar.
- En Vercel, si `DATABASE_URL` sigue siendo SQLite, se redirige a
  `sqlite:////tmp/odontologico.db`. Esa base es **efímera**: se pierde entre
  invocaciones/despliegues. Para persistencia real se debe configurar
  PostgreSQL (`postgresql+psycopg://...`).
- Las URLs `postgres://` y `postgresql://` se reescriben automáticamente al
  driver `psycopg` 3.
- `PUBLIC_BASE_URL` define la base de los enlaces de anamnesis. Si falta, en
  Vercel se usa `VERCEL_PROJECT_PRODUCTION_URL` o `VERCEL_URL`; en local,
  `http://localhost:8000`.
- El secreto de anamnesis sigue esta prioridad:
  `ANAMNESIS_TOKEN_SECRET` → `WHATSAPP_VERIFY_TOKEN` → SHA-256 derivado de
  `DATABASE_URL + APP_NAME + "anamnesis"`.

### 3.6. Transacciones, errores y límites

- `SessionLocal` usa `autoflush=False`, `autocommit=False` y
  `expire_on_commit=False`. Cada request recibe una sesión y la cierra al final.
- Los servicios hacen `commit()` y `rollback()` explícitos. No hay una capa que
  agrupe varias operaciones de servicios distintos en una sola transacción.
- Los errores de unicidad se traducen de `IntegrityError` a `ConflictoError`
  (HTTP 409), no a `500`.
- `AppError` permite adjuntar `extra` al JSON de error.
- Los listados tienen límites: turnos `limit <= 500`, evoluciones `<= 500`,
  fichas de salud `limit 500`; no hay paginación por cursor.

---

## 4. Dependencias principales

### 4.1. Runtime (`requirements.txt`)

| Dependencia | Versión | Propósito en la aplicación |
|---|---:|---|
| `fastapi` | 0.143.0 | Framework HTTP, inyección de dependencias, OpenAPI y validación de rutas. |
| `uvicorn[standard]` | 0.54.0 | Servidor ASGI para desarrollo local y ejecución en Vercel. |
| `SQLAlchemy` | 2.1.4 | ORM declarativo, engine, sesiones, relaciones y DDL. |
| `pydantic` | 2.14.1 | Schemas de entrada/salida, validadores y serialización. |
| `pydantic-settings` | 2.15.0 | Lectura de `.env` y variables de entorno tipadas. |
| `email-validator` | 2.3.0 | Soporte de `EmailStr` en el schema de paciente. |
| `httpx` | 0.28.1 | Cliente HTTP para la WhatsApp Cloud API. |
| `psycopg[binary]` | 3.3.6 | Driver PostgreSQL 3 usado por SQLAlchemy. |
| `python-dotenv` | 1.2.4 | Carga de archivos `.env` (complemento de settings). |
| `tzdata` | 2026.5 | Base de zonas horarias en plataformas que no la traen. |

### 4.2. Desarrollo (`requirements-dev.txt`)

| Dependencia | Versión | Propósito |
|---|---:|---|
| `pytest` | 9.1.1 | Ejecución de la suite de 704 casos. |
| `esprima` | 4.0.1 | Parser JavaScript usado por pruebas de frontend/contratos. |

### 4.3. Frontend y herramientas externas

| Componente | Uso |
|---|---|
| HTML5 + CSS3 + JavaScript ES6 | Sin framework ni bundler. |
| Tailwind CSS (CDN) | Utilidades de layout y estilos del panel/móvil. |
| Google Fonts `Inter` | Tipografía del sistema. |
| SVG locales | Marca, logo y favicon en `app/static/img/`. |
| Vercel CLI | Deploy de producción desde `scripts/publicar.py`. |
| Git/GitHub | Control de versiones y respaldo. |

### 4.4. Dependencias que **no** se usan

- **Alembic**: no hay migraciones versionadas; se usan `create_all` y
  `app/db/migraciones.py`.
- **ReportLab / WeasyPrint**: el PDF se genera con `app/core/pdf.py`.
- **openpyxl / pandas**: el XLSX se lee/escribe con `zipfile` + `xml` en
  `app/core/planilla_aranceles.py`.
- **Redis / Celery / APScheduler**: no hay cola ni scheduler persistente.
- **JWT / OAuth / passlib**: no hay autenticación implementada.
- **Node/npm**: el frontend se sirve tal cual.

---

## 5. Detalle de módulos y componentes

### 5.1. Punto de entrada e infraestructura

#### `app/main.py`

| Elemento | Función |
|---|---|
| `lifespan()` | Configura logging, ejecuta `init_db()` y registra el arranque/cierre. |
| `app` | Instancia FastAPI con título, versión, descripción y lifespan. |
| `CORSMiddleware` | Orígenes de `settings.cors_origins`; métodos GET, POST, PUT, PATCH, DELETE y OPTIONS. |
| `app_error_handler()` | Traduce `AppError` a `JSONResponse` con `detail` y `extra`. |
| `GET /health` | Devuelve estado, nombre y versión de la app. |
| `StaticFiles` | Monta `/static` sobre `app/static`. |
| `web_router` | Sirve `/`, `/medico` y `/anamnesis/{token}`. |
| `api_router` | Monta la API bajo `settings.api_v1_prefix` (`/api/v1`). |

#### `app/core/`

| Archivo | Funciones / clases principales | Propósito |
|---|---|---|
| `config.py` | `Settings`, `url_publica`, `anamnesis_secreto_efectivo`, `whatsapp_envio_real` | Configuración tipada, adaptación a Vercel, normalización del driver PostgreSQL y secretos. |
| `datetime_utils.py` | `utcnow`, `to_naive_utc`, `rango_del_dia`, `dia_semana_de` | Convención UTC naive y cálculo del día calendario (0 = domingo). |
| `exceptions.py` | `AppError`, `ValidacionError` (422), `RecursoNoEncontradoError` (404), `ConflictoError` (409), `TurnoSolapadoError`, `PacienteConTurnoSolapadoError` (400), `TurnoNoEncontradoError`, `TurnoYaFacturadoError`, `RespuestaWebhookInvalidaError` (400), `WhatsAppEnvioError` (502), `RegistroInmutableError` (403) | Errores de dominio con status HTTP. |
| `logging_config.py` | `configurar_logging()` | Un handler a stdout; no duplica si uvicorn ya configuró. |
| `money.py` | `redondear_monto`, `sumar_montos`, `multiplicar_monto`, `porcentaje_de` | Operaciones monetarias con `Decimal` y `ROUND_HALF_UP`. |
| `pagos.py` | `validar_modalidad_pago()` | Coherencia entre `tipo_pago` y `valor_pago`. |
| `whatsapp_utils.py` | `solo_digitos`, `normalizar_telefono_whatsapp`, `construir_enlace_click_to_chat` | Normalización de teléfonos y enlaces `wa.me` compartidos por recordatorios, bot, ficha y CLI (sin importar servicios). |
| `anamnesis_token.py` | `firmar_token_anamnesis`, `validar_token_anamnesis`, `construir_enlace_anamnesis`, `DatosTokenAnamnesis`, `TokenAnamnesisInvalidoError` | Token HMAC-SHA256 determinístico para la ficha pública. |
| `pdf.py` | `DocumentoPDF`, `Columna`, `ancho_texto`, `envolver`, `recortar`, `fecha_legible`, `nombre_archivo` | Generador PDF A4 con tablas, totales y paginado, sin dependencias. |
| `planilla_aranceles.py` | `leer_planilla`, `generar_csv`, `generar_xlsx`, `parsear_monto`, `canonizar_codigo`, `FilaPlanilla` | Importación/exportación de la planilla de aranceles (CSV/XLSX). |

#### `app/db/`

| Archivo | Elementos | Propósito |
|---|---|---|
| `base_class.py` | `Base(DeclarativeBase)` | Base común de todos los modelos. |
| `base.py` | Imports de los 15 modelos + `Base` | Garantiza que `Base.metadata` tenga todas las tablas. |
| `session.py` | `_crear_engine`, `engine`, `SessionLocal`, `get_db` | Engine SQLite/PostgreSQL, pool pre-ping, sesión por request. |
| `types.py` | `UTCDateTime` | `TIMESTAMP WITH TIME ZONE` en PostgreSQL, UTC naive en Python. |
| `init_db.py` | `init_db`, `drop_db` | `create_all`, columnas faltantes y seed del menú. |
| `migraciones.py` | `ColumnaNueva`, `COLUMNAS_NUEVAS`, `aplicar_columnas_faltantes` | `ALTER TABLE` aditivo e idempotente para bases existentes. |

**Columnas cubiertas por `COLUMNAS_NUEVAS`:**

| Tabla | Columna | DDL SQLite | DDL PostgreSQL |
|---|---|---|---|
| `doctores` | `tipo_pago` | `VARCHAR(15) NOT NULL DEFAULT 'DUENO'` | igual |
| `doctores` | `valor_pago` | `FLOAT` | `DOUBLE PRECISION` |
| `pacientes` | `dni` | `VARCHAR(20)` | `VARCHAR(20)` |
| `pacientes` | `obra_social_id` | `INTEGER` | `INTEGER` |
| `pacientes` | `numero_afiliado` | `VARCHAR(60)` | `VARCHAR(60)` |
| `pacientes` | `fecha_nacimiento` | `DATE` | `DATE` |
| `pacientes` | `estado_civil` | `VARCHAR(30)` | `VARCHAR(30)` |
| `pacientes` | `nacionalidad` | `VARCHAR(60)` | `VARCHAR(60)` |
| `pacientes` | `domicilio` | `VARCHAR(200)` | `VARCHAR(200)` |
| `pacientes` | `profesion` | `VARCHAR(120)` | `VARCHAR(120)` |
| `pacientes` | `titular` | `VARCHAR(120)` | `VARCHAR(120)` |
| `pacientes` | `lugar_trabajo` | `VARCHAR(120)` | `VARCHAR(120)` |
| `pacientes` | `jerarquia` | `VARCHAR(60)` | `VARCHAR(60)` |
| `turnos` | `whatsapp_mensaje_id` | `VARCHAR(128)` | `VARCHAR(128)` |

> Las **tablas nuevas** las crea `create_all`. Las **columnas nuevas** de tablas
> existentes deben agregarse a `COLUMNAS_NUEVAS`; `create_all` no las agrega.

#### `app/models/`

| Modelo | Tabla | Campos principales | Relaciones |
|---|---|---|---|
| `Doctor` | `doctores` | `id`, `nombre`, `especialidad`, `activo`, `tipo_pago`, `valor_pago` | 1-N turnos, disponibilidades, excepciones, evoluciones y movimientos. |
| `Paciente` | `pacientes` | `id`, `nombre`, `dni`, `telefono`, `email`, `obra_social_id`, `numero_afiliado`, filiatorios (`fecha_nacimiento`, `estado_civil`, `nacionalidad`, `domicilio`, `profesion`, `titular`, `lugar_trabajo`, `jerarquia`) | N-1 obra social; 1-N turnos; 1-1 historia clínica. |
| `ObraSocial` | `obras_sociales` | `id`, `nombre`, `codigo`, `activa` | 1-N pacientes y aranceles. |
| `Turno` | `turnos` | `id`, `paciente_id`, `doctor_id`, fechas, estados, `whatsapp_mensaje_id` | N-1 paciente/doctor; 1-1 movimiento de caja. |
| `DisponibilidadDoctor` | `disponibilidad_doctores` | `id`, `doctor_id`, `dia_semana`, `hora_inicio`, `hora_fin` | N-1 doctor. |
| `DisponibilidadEspecifica` | `disponibilidad_especifica` | `id`, `doctor_id`, `fecha`, horas, `tipo` | N-1 doctor. |
| `HistoriaClinica` | `historias_clinicas` | `id`, `paciente_id`, `numero_ficha` | 1-1 paciente; 1-N evoluciones; 1-1 antecedentes. |
| `EvolucionMedica` | `evoluciones_medicas` | `id`, `historia_clinica_id`, `doctor_id`, `fecha_atencion`, textos clínicos | N-1 historia/doctor; 1-N archivos. Inmutable. |
| `ArchivoAdjunto` | `archivos_adjuntos` | `id`, `evolucion_id`, `url_archivo`, `tipo_archivo`, `fecha_subida` | N-1 evolución. |
| `Prestacion` | `prestaciones` | `id`, `codigo_nomenclador`, `descripcion`, `valor_base`, `activa` | 1-N aranceles y detalles. |
| `ArancelObraSocial` | `aranceles_obra_social` | `id`, `obra_social_id`, `prestacion_id`, `monto_cubre_os`, `monto_coseguro_paciente` | N-1 obra social/prestación. |
| `MovimientoCaja` | `movimientos_caja` | `id`, `turno_id`, `doctor_id`, `fecha`, montos, `estado_pago`, `observaciones` | N-1 turno/doctor; 1-N detalles. |
| `DetalleMovimientoCaja` | `detalles_movimiento_caja` | `id`, `movimiento_caja_id`, `prestacion_id`, código/descripción, `cantidad`, montos | N-1 movimiento/prestación. |
| `OpcionMenuWhatsApp` | `opciones_menu_whatsapp` | `id`, `numero`, `titulo`, `tipo`, `respuesta`, `activa`, `orden` | Sin FK. |
| `AntecedentesSalud` | `antecedentes_salud` | `id`, `historia_clinica_id`, campos de la Historia Clínica General (familiares, personales, odontológicos, examen bucal y diagnóstico/plan), `completado`, `fecha_completado` | 1-1 historia clínica. Los **filiatorios** de la ficha no son columnas: se guardan en `pacientes`. |
| `Odontograma` | `odontogramas` | `id`, `historia_clinica_id`, `doctor_id`, prótesis fija/removible, coronas, dientes existentes, observaciones, `actualizado_en` | 1-1 historia clínica; 1-N dientes. |
| `OdontogramaDiente` | `odontograma_dientes` | `id`, `odontograma_id`, `diente` (FDI), `estado`, `notas` | N-1 odontograma; único por diente. |

**Enumeraciones (`app/models/enums.py`):**

- `EstadoTurno`: `PENDIENTE`, `CONFIRMADO`, `CANCELADO`.
- `EstadoWhatsApp`: `PENDIENTE_ENVIO`, `ENVIADO`, `FALLIDO`, `RESPONDIDO`.
- `TipoOpcionMenu`: `TEXTO`, `PRECIO`, `OBRAS_SOCIALES`, `UBICACION`, `SECRETARIA`.
- `TipoPagoDoctor`: `DUENO`, `COMISION`, `SUELDO`.
- `EstadoPagoMovimiento`: `PENDIENTE`, `EN_PROCESO_LIQUIDACION`, `COBRADO`,
  `DEBITADO_OS`.
- `TipoDisponibilidadEspecifica`: `HABILITADO`, `BLOQUEADO`.

### 5.2. Schemas Pydantic (`app/schemas/`)

| Archivo | Clases principales | Uso |
|---|---|---|
| `doctor.py` | `DoctorBase`, `DoctorCreate`, `DoctorReplace`, `DoctorUpdate`, `DoctorRead` | CRUD del profesional y validación de pago. |
| `paciente.py` | `PacienteDatosFiliatorios`, `PacienteDatosAdministrativos`, `PacienteBase`, `PacienteCreate`, `PacienteUpdate`, `PacienteRead` | Normaliza DNI, email, cobertura, filiatorios (fecha `AAAA-MM-DD` no futura) y campos vacíos; `PacienteRead` calcula `edad`. |
| `turno.py` | `TurnoBase`, `TurnoCreate`, `TurnoUpdate`, `TurnoRead` | Normaliza fechas UTC naive y valida el rango. |
| `disponibilidad.py` | `DisponibilidadBase/Create/Read/Reemplazo`, `BloqueDisponible`, `ExcepcionFechaBase`, `DisponibilidadEspecificaCreate/Read/Reemplazo`, `RangoEfectivoRead`, `HorariosDisponiblesRead` | Serializa horas como `HH:MM` y rechaza solapes semanales. |
| `obra_social.py` | `ObraSocialBase`, `ObraSocialCreate`, `ObraSocialUpdate`, `ObraSocialRead` | Catálogo de coberturas. |
| `prestacion.py` | `PrestacionBase`, `PrestacionCreate`, `PrestacionUpdate`, `PrestacionRead` | Catálogo de prácticas. |
| `arancel.py` | `ArancelObraSocialBase/Create/Update/Read`, `ConvenioFilaBase`, `GuardarConvenioRequest`, `ConvenioFilaRead`, `ConvenioObraSocialRead`, `ImportarArancelesRequest`, `ImportarArancelesFilaRead`, `ImportarArancelesRead` | Aranceles, convenios y reporte de importación. |
| `historia_clinica.py` | `ArchivoAdjuntoCreate/Read`, `EvolucionMedicaCreate/Read`, `HistoriaClinicaDetalle` | Alta y lectura clínica; no hay Update. |
| `antecedentes_salud.py` | `DeclaracionSaludCreate`, `FichaMedicaUpdate`, `AntecedentesSaludRead`, `AnamnesisPublicaRead`, `AnamnesisGuardadaRead`, `FichaSaludPanelRead`, `FichaSaludEnlaceRead`, `FichaSaludEnvioCreate`, `FichaSaludEnvioRead` | Ficha pública (sólo paciente), ficha completa del panel (con examen y diagnóstico), **filiatorios** (se guardan en el paciente y viajan también en `datos_filiatorios`), alertas calculadas y envío manual del enlace. |
| `odontograma.py` | `OdontogramaDienteCreate/Read`, `OdontogramaUpdate`, `OdontogramaRead` | Gráfico dental: notación FDI 11-48, estados del PDF y totales por color. |
| `facturacion.py` | `PrestacionFacturadaCreate`, `FacturarTurnoRequest`, `PresupuestoTurnoRequest`, `PresupuestoCobroRead`, `DetalleMovimientoCajaRead`, `MovimientoCajaRead`, `PrestacionesDisponiblesRead`, `CobrarMovimientoRequest`, `DebitarMovimientoRequest`, `ResumenCajaRead`, `PresentacionObraSocialRead`, `LiquidacionProfesionalesRead` | Cierre de visita, caja, presentaciones y liquidaciones. |
| `whatsapp.py` | `WhatsAppText`, `WhatsAppButtonReply`, `WhatsAppContext`, `WhatsAppInteractive`, `WhatsAppInboundMessage`, `WhatsAppWebhookPayload`, `WebhookProcesadoResponse`, `RespuestaBot`, `RecordatorioEnviado`, `RecordatorioResultado`, `RecordatorioPendiente` | Parseo del webhook y respuestas. |
| `opcion_menu_whatsapp.py` | `OpcionMenuBase`, `OpcionMenuCreate`, `OpcionMenuUpdate`, `OpcionMenuRead`, `MenuWhatsAppVistaPrevia` | CRUD y vista previa del menú. |

### 5.3. Servicios (`app/services/`)

| Archivo | Funciones principales | Responsabilidad |
|---|---|---|
| `doctor_service.py` | `_validar_pago`, `obtener_doctor`, `listar_doctores`, `crear_doctor`, `reemplazar_doctor`, `actualizar_doctor`, `dar_de_baja` | CRUD, baja lógica, filtro por activo y coherencia de pago. |
| `paciente_service.py` | `_sin_acentos`, `_normalizar_busqueda`, `obtener_paciente`, `_resolver_cobertura`, `_validar_dni_unico`, `crear_paciente`, `listar_pacientes`, `actualizar_paciente` | Búsqueda tolerante, DNI único y traducción de cobertura texto → FK. |
| `obra_social_service.py` | `obtener_obra_social`, `_validar_nombre_libre`, `listar_obras_sociales`, `crear_obra_social`, `actualizar_obra_social`, `desactivar_obra_social`, `resolver_por_nombre` | Catálogo con nombre único, baja lógica y compatibilidad con texto heredado. |
| `prestacion_service.py` | `obtener_prestacion`, `obtener_prestacion_activa`, `_validar_codigo_libre`, `listar_prestaciones`, `crear_prestacion`, `actualizar_prestacion`, `desactivar_prestacion` | Catálogo de prácticas y bloqueo de prácticas inactivas al facturar. |
| `disponibilidad_service.py` | `listar_rangos`, `rangos_del_dia`, `agregar_rango`, `reemplazar_agenda`, `eliminar_rango`, `RangoEfectivo`, `DisponibilidadEfectivaDia`, `listar_excepciones`, `excepciones_del_dia`, `agregar_excepcion`, `reemplazar_excepciones_del_dia`, `eliminar_excepcion`, `rangos_efectivos_del_dia` | Agenda semanal, excepciones por fecha, unión/resta de tramos y precedencia. |
| `turno_service.py` | `_validar_solapamiento`, `_validar_paciente_ubicuo`, `crear_turno`, `listar_turnos`, `obtener_turno`, `actualizar_turno`, `_bloques_de_rangos`, `_turnos_vigentes_del_dia`, `horarios_disponibles` | Alta/edición, solapamientos, agenda y bloques libres. |
| `historia_clinica_service.py` | `numero_ficha_de`, `obtener_historia`, `obtener_o_crear_historia`, `crear_evolucion`, `listar_evoluciones`, `obtener_evolucion`, `detalle_historia` | Ficha automática, evoluciones inmutables y línea de tiempo. |
| `anamnesis_service.py` | `obtener_formulario_publico`, `guardar_declaracion_publica`, `guardar_declaracion_turno`, `listar_fichas_panel`, `obtener_enlace_ficha`, `enviar_ficha`, `necesita_completar_anamnesis` | Token, alta/edición de la ficha médica completa (paciente y profesional), sincronización de los filiatorios en el paciente, panel de fichas y envío manual del enlace. |
| `odontograma_service.py` | `obtener_odontograma`, `guardar_odontograma`, `_aplicar_cabecera`, `_odontograma_de` | Lectura sin crear la historia, alta/corrección del gráfico y reemplazo del conjunto de dientes. |
| `arancel_service.py` | `obtener_arancel`, `listar_aranceles`, `crear_arancel`, `actualizar_arancel`, `reemplazar_arancel`, `eliminar_arancel`, `resolver_arancel`, `mapa_aranceles`, `coseguro_sugerido`, `convenio_de_obra_social`, `guardar_convenio`, `importar_aranceles`, `plantilla_aranceles` | Acuerdo OS–práctica, grilla de convenios, importador y plantilla. |
| `facturacion_service.py` | `periodo_mensual`, `numero_presentacion`, `movimiento_de_turno`, `listar_movimientos`, `resumen_caja`, `_resolver_prestaciones`, `prestaciones_disponibles`, `presupuestar`, `facturar_turno`, `cobrar_movimiento`, `debitar_movimiento`, `presentacion_del_periodo`, `generar_presentacion` | Motor de facturación mixta, caja y presentaciones a obras sociales. |
| `liquidacion_service.py` | `_honorarios`, `liquidar_profesionales` | Producción mensual y honorarios según `tipo_pago`. |
| `reporte_pdf_service.py` | `pdf_presentacion_obra_social`, `pdf_liquidacion_profesionales`, `nombre_archivo_presentacion`, `nombre_archivo_liquidacion` | Render PDF de los dos reportes de facturación. |
| `whatsapp_menu_service.py` | `opciones_por_defecto`, `contar_opciones`, `listar_opciones`, `obtener_menu_vigente`, `obtener_opcion`, `crear_opcion`, `actualizar_opcion`, `desactivar_opcion`, `sembrar_menu_por_defecto`, `restaurar_menu_por_defecto` | Menú configurable, unicidad de tecla y seed inicial. |
| `whatsapp_bot_service.py` | `normalizar_texto`, `detectar_opcion`, `es_saludo_inicial`, `normalizar_destinatario`, `formatear_importe`, `obtener_prestacion_consulta`, `listar_obras_sociales_activas`, `mensaje_menu`, `mensaje_precio_consulta`, `mensaje_obras_sociales`, `mensaje_ubicacion`, `responder_mensaje` | Árbol de decisión del FAQ Bot y redacción de respuestas. |
| `whatsapp_service.py` | `construir_payload_recordatorio`, `enviar_mensaje_recordatorio`, `construir_payload_texto`, `enviar_mensaje_texto`, `enviar_recordatorios`, `iterar_mensajes`, `mensaje_de_texto`, `procesar_respuesta_boton`, `normalizar_telefono_whatsapp`, `construir_mensaje_recordatorio`, `construir_enlace_whatsapp`, `listar_recordatorios_click_to_chat` | Transporte WhatsApp, recordatorios, webhook y enlaces `wa.me`. |

**Orden de dependencia entre servicios (resumen):**

```
whatsapp_bot → whatsapp_service → anamnesis_service → historia_clinica_service
liquidacion → facturacion → arancel / obra_social / prestacion
turno → disponibilidad / doctor
paciente → obra_social
```

No hay ciclos de importación entre módulos de servicio en el estado actual.

### 5.4. Catálogo de endpoints

**Códigos HTTP habituales:** `200` lectura/actualización, `201` alta, `204`
borrado sin cuerpo, `400` payload/regla inválida o paciente ubicuo, `403`
registro inmutable, `404` recurso inexistente, `405` método no permitido, `409`
conflicto de unicidad/solapamiento/estado, `422` validación Pydantic, `502`
fallo de WhatsApp.

> En las tablas de rutas, `{id}` es una abreviatura didáctica. El nombre exacto
> del parámetro está en OpenAPI (`{doctor_id}`, `{paciente_id}`,
> `{obra_social_id}`, `{turno_id}`, `{prestacion_id}`, `{arancel_id}`,
> `{movimiento_id}`, `{opcion_id}`, `{evolucion_id}`). La validación automática
> confirma que las **80 combinaciones método + ruta** del OpenAPI están
> documentadas.

#### 5.4.1. Salud, interfaz y recursos básicos

| Método | Ruta | Resumen |
|---|---|---|
| `GET` | `/health` | Estado, nombre y versión de la app. |
| `GET` | `/` | Panel HTML de la secretaría. |
| `GET` | `/medico` | Vista HTML móvil del odontólogo. |
| `GET` | `/anamnesis/{token}` | Página HTML pública de la ficha de salud. |
| `GET` | `/docs` | Swagger UI generado por FastAPI. |
| `GET` | `/redoc` | ReDoc generado por FastAPI. |
| `GET` | `/openapi.json` | Esquema OpenAPI. |
| `GET` | `/static/*` | CSS, JS, SVG y demás recursos estáticos. |

#### 5.4.2. Doctores, disponibilidad, obras sociales, pacientes y turnos

| Método | Ruta | Resumen / regla |
|---|---|---|
| `POST` | `/api/v1/doctores` | Crea profesional; valida `tipo_pago`/`valor_pago`. |
| `GET` | `/api/v1/doctores?activos=` | Lista ordenada por nombre; filtro opcional. |
| `GET` | `/api/v1/doctores/{id}` | Detalle o 404. |
| `PUT` | `/api/v1/doctores/{id}` | Reemplazo total; `activo` omitido queda `true`. |
| `PATCH` | `/api/v1/doctores/{id}` | Actualización parcial; también baja/reactivación lógica. |
| `DELETE` | `/api/v1/doctores/{id}` | Baja lógica (`activo=false`), idempotente. |
| `GET` | `/api/v1/doctores/{id}/disponibilidad` | Agenda semanal ordenada. |
| `POST` | `/api/v1/doctores/{id}/disponibilidad` | Agrega rango; solape mismo día → 409. |
| `PUT` | `/api/v1/doctores/{id}/disponibilidad` | Reemplaza la semana completa; solapes → 422. |
| `DELETE` | `/api/v1/doctores/{id}/disponibilidad/{rango_id}` | Elimina un rango semanal. |
| `GET` | `/api/v1/doctores/{id}/disponibilidad-especifica` | Lista excepciones por fecha. |
| `POST` | `/api/v1/doctores/{id}/disponibilidad-especifica` | Agrega tramo `HABILITADO`/`BLOQUEADO`. |
| `PUT` | `/api/v1/doctores/{id}/disponibilidad-especifica/{fecha}` | Reemplaza los tramos de una fecha. |
| `DELETE` | `/api/v1/doctores/{id}/disponibilidad-especifica/{excepcion_id}` | Elimina una excepción. |
| `GET` | `/api/v1/doctores/{id}/horarios-disponibles` | Bloques libres; `duracion_minutos` 5-240. |
| `POST` | `/api/v1/obras-sociales` | Crea cobertura; nombre único (case-insensitive). |
| `GET` | `/api/v1/obras-sociales?activas=&nombre=` | Lista/busca coberturas. |
| `GET` | `/api/v1/obras-sociales/{id}` | Detalle. |
| `PUT` | `/api/v1/obras-sociales/{id}` | Reemplazo total. |
| `PATCH` | `/api/v1/obras-sociales/{id}` | Edición parcial/reactivación. |
| `DELETE` | `/api/v1/obras-sociales/{id}` | Baja lógica (`activa=false`). |
| `POST` | `/api/v1/pacientes` | Crea paciente; DNI único, cobertura resuelta y datos filiatorios opcionales. |
| `GET` | `/api/v1/pacientes?telefono=&dni=&buscar=` | Filtros exactos o búsqueda por nombre/DNI. |
| `GET` | `/api/v1/pacientes/{id}` | Detalle. |
| `PATCH` | `/api/v1/pacientes/{id}` | Edición parcial; `null` limpia campos. |
| `POST` | `/api/v1/turnos` | Crea turno; valida solapes de doctor y paciente. |
| `GET` | `/api/v1/turnos?skip=&limit=&doctor_id=&paciente_id=&fecha_desde=&fecha_hasta=` | Lista paginada (máx. 500) por rango. |
| `GET` | `/api/v1/turnos/{id}` | Detalle. |
| `PATCH` | `/api/v1/turnos/{id}` | Reprograma, reasigna, confirma, cancela o reactiva. |

#### 5.4.3. Historia clínica, ficha médica y odontograma

| Método | Ruta | Resumen / regla |
|---|---|---|
| `POST` | `/api/v1/pacientes/{id}/evoluciones` | Alta de evolución; crea la ficha si falta. |
| `GET` | `/api/v1/pacientes/{id}/evoluciones?skip=&limit=` | Línea de tiempo descendente (máx. 500). |
| `GET` | `/api/v1/pacientes/{id}/historia-clinica` | Ficha + evoluciones + antecedentes; sólo lectura. |
| `GET` | `/api/v1/pacientes/{id}/historia-clinica/pdf` | Historia Clínica General en PDF sobre la plantilla de papel (`?doctor_id=` opcional); anexo con odontograma y textos largos. |
| `GET` | `/api/v1/evoluciones/{id}` | Evolución puntual; sólo lectura. |
| `GET` | `/api/v1/pacientes/{id}/odontograma` | Gráfico dental + prótesis + totales; `id` en `null` si no se cargó. |
| `PUT` | `/api/v1/pacientes/{id}/odontograma` | Guarda el odontograma completo (FDI 11-48; reemplaza los dientes). |
| `GET` | `/api/v1/anamnesis/panel?dias=&solo_pendientes=` | Turnos próximos con estado de ficha y enlace. |
| `GET` | `/api/v1/anamnesis/panel/{turno_id}/enlace?telefono=` | Enlace firmado, mensaje y `wa.me` de cualquier turno no cancelado. |
| `POST` | `/api/v1/anamnesis/panel/{turno_id}/enviar` | Prepara (`manual`) o envía (`api`) la ficha; no marca el recordatorio. |
| `PUT` | `/api/v1/anamnesis/panel/{turno_id}` | Carga/corrige la ficha completa (incluye examen bucal y diagnóstico). |
| `GET` | `/api/v1/anamnesis/{token}` | Contexto público de la ficha; no crea historia. |
| `POST` | `/api/v1/anamnesis/{token}` | Guarda/actualiza la declaración del paciente y completa la ficha. |

> No existen `PUT`, `PATCH` ni `DELETE` para evoluciones: cualquier intento
> devuelve `405 Method Not Allowed`.
>
> Los **datos filiatorios** de la ficha no son columnas de `antecedentes_salud`:
> el `POST` público y el `PUT` del panel los sincronizan en `pacientes`, y las
> lecturas (`GET /anamnesis/{token}`, `GET /anamnesis/panel`,
> `GET /pacientes/{id}/historia-clinica`) los devuelven dentro de
> `antecedentes_salud` —y en `datos_filiatorios` para precargar el formulario
> aunque la ficha siga pendiente.

#### 5.4.4. Prestaciones y aranceles

| Método | Ruta | Resumen / regla |
|---|---|---|
| `POST` | `/api/v1/prestaciones` | Crea práctica; código único, se guarda en mayúsculas. |
| `GET` | `/api/v1/prestaciones?activas=&buscar=` | Lista/busca por código o descripción. |
| `GET` | `/api/v1/prestaciones/{id}` | Detalle. |
| `PUT` | `/api/v1/prestaciones/{id}` | Reemplazo total. |
| `PATCH` | `/api/v1/prestaciones/{id}` | Edición parcial/reactivación. |
| `DELETE` | `/api/v1/prestaciones/{id}` | Baja lógica (`activa=false`). |
| `GET` | `/api/v1/obras-sociales/{id}/aranceles?prestacion_id=` | Aranceles de una cobertura. |
| `POST` | `/api/v1/obras-sociales/{id}/aranceles` | Alta de arancel para una práctica; par único. |
| `GET` | `/api/v1/aranceles/{id}` | Detalle del arancel. |
| `PUT` | `/api/v1/aranceles/{id}` | Reemplazo de montos. |
| `PATCH` | `/api/v1/aranceles/{id}` | Edición parcial de montos. |
| `DELETE` | `/api/v1/aranceles/{id}` | Baja física del acuerdo. |
| `GET` | `/api/v1/obras-sociales/{id}/convenio` | Grilla completa con coseguro sugerido y totales. |
| `PUT` | `/api/v1/obras-sociales/{id}/convenio` | Guarda la grilla en lote (una transacción). |
| `GET` | `/api/v1/obras-sociales/{id}/convenio/plantilla?formato=csv\|xlsx` | Descarga plantilla con `codigo` y `precio_cobertura`. |
| `POST` | `/api/v1/obras-sociales/{id}/importar-aranceles` | Importa CSV/XLSX; admite `simular=true`. |

#### 5.4.5. Facturación, caja, liquidaciones, tareas y WhatsApp

| Método | Ruta | Resumen / regla |
|---|---|---|
| `POST` | `/api/v1/turnos/{id}/facturar` | Cierra la visita y crea el movimiento de caja. |
| `POST` | `/api/v1/turnos/{id}/presupuesto` | Calcula el cobro sin escribir. |
| `GET` | `/api/v1/turnos/{id}/prestaciones-disponibles` | Prácticas activas con arancel resuelto. |
| `GET` | `/api/v1/turnos/{id}/facturacion` | Indica si el turno ya está facturado. |
| `GET` | `/api/v1/movimientos-caja/resumen?desde=&hasta=` | Totales de caja del rango. |
| `GET` | `/api/v1/movimientos-caja?desde=&hasta=&doctor_id=&estado_pago=` | Lista de movimientos. |
| `GET` | `/api/v1/movimientos-caja/{id}` | Detalle con prácticas. |
| `POST` | `/api/v1/movimientos-caja/{id}/cobrar` | Marca cobro de la OS; importe opcional. |
| `POST` | `/api/v1/movimientos-caja/{id}/debitar` | Marca débito; motivo obligatorio. |
| `GET` | `/api/v1/liquidaciones/obras-sociales?obra_social_id=&anio=&mes=` | Presentación del período (sólo lectura). |
| `POST` | `/api/v1/liquidaciones/obras-sociales` | Genera presentación: `PENDIENTE` → `EN_PROCESO`. |
| `GET` | `/api/v1/liquidaciones/profesionales?anio=&mes=&doctor_id=` | Producción y honorarios del mes. |
| `GET` | `/api/v1/liquidaciones/obras-sociales/pdf?...` | Presentación en PDF. |
| `GET` | `/api/v1/liquidaciones/profesionales/pdf?...` | Liquidación en PDF. |
| `POST` | `/api/v1/tareas/enviar-recordatorios?dias=` | Dispara recordatorios del día `hoy + N`. |
| `GET` | `/api/v1/whatsapp/webhook` | Verificación `hub.mode`, `hub.verify_token`, `hub.challenge`. |
| `POST` | `/api/v1/whatsapp/webhook` | Procesa texto (bot) o botón (confirmar/cancelar). |
| `GET` | `/api/v1/whatsapp/recordatorios-pendientes?dias=` | Lista con enlace `wa.me` y mensaje redactado. |
| `GET` | `/api/v1/whatsapp/menu?activas=` | Lista opciones del FAQ Bot. |
| `POST` | `/api/v1/whatsapp/menu` | Crea opción; número de un dígito único. |
| `GET` | `/api/v1/whatsapp/menu/vista-previa` | Texto del menú activo. |
| `POST` | `/api/v1/whatsapp/menu/restaurar` | Restaura las cuatro opciones históricas. |
| `GET` | `/api/v1/whatsapp/menu/{id}` | Detalle de una opción. |
| `PUT` | `/api/v1/whatsapp/menu/{id}` | Reemplazo completo. |
| `PATCH` | `/api/v1/whatsapp/menu/{id}` | Edición parcial/reactivación. |
| `DELETE` | `/api/v1/whatsapp/menu/{id}` | Baja lógica (`activa=false`). |

### 5.5. Interfaz web (`app/web` y `app/static`)

#### `app/web/router.py`

| Ruta | Archivo | Detalle |
|---|---|---|
| `GET /` | `index.html` | Panel principal de la secretaría. |
| `GET /medico` | `medico.html` | Agenda móvil del odontólogo. |
| `GET /anamnesis/{token}` | `anamnesis.html` | Ficha pública; agrega `Cache-Control: no-store`, `Referrer-Policy: no-referrer` y `X-Robots-Tag: noindex`. |

`STATIC_DIR`, `INDEX_FILE`, `MEDICO_FILE` y `ANAMNESIS_FILE` resuelven rutas
absolutas con `pathlib`.

#### Archivos estáticos

| Archivo | Tamaño aproximado | Responsabilidad |
|---|---:|---|
| `index.html` | 1.889 líneas | Estructura del panel: dashboard, agenda, calendario, doctores, pacientes (con filiatorios), historia, disponibilidad, recordatorios, fichas de salud, facturación y bot. |
| `styles.css` | 1.348 líneas | Componentes, animaciones, timeline clínica y utilidades propias. |
| `app.js` | 6.485 líneas | Estado global, cliente `fetch`, render, modales (ficha médica y envío del enlace), disponibilidad, facturación, caja, historia y puente `window.OdontoApp`. |
| `calendar.js` | 986 líneas | Calendario mensual, filtro por odontólogo, tooltip, panel del día y bloques libres; consume `window.OdontoApp` y expone `window.OdontoCalendar`. |
| `ficha_medica.js` | 686 líneas | Catálogo único de **124 campos** (8 filiatorios + antecedentes familiares, personales, odontológicos, examen y diagnóstico) + formulario, edad calculada y resumen reutilizables. |
| `ficha_medica.css` | 226 líneas | Estilos del cuestionario en la página pública, el panel y la vista móvil (incluye la edad calculada). |
| `odontograma.js` | 516 líneas | Modal del odontograma: paleta rojo/azul/X, 32 dientes FDI, prótesis y guardado por API. |
| `odontograma.css` | 431 líneas | Colores de los estados, arcadas, botón *Odontograma* y adaptación móvil. |
| `medico.html` | 360 líneas | Vista mobile-first, selección de profesional, agenda y hoja de historia/evolución. |
| `medico.css` | 424 líneas | Estilos táctiles, hojas deslizantes y timeline. |
| `medico.js` | 1.555 líneas | Agenda autónoma, navegación de fechas, alta/edición/cancelación, evolución clínica y refresco cada 60 s. |
| `anamnesis.html` | 192 líneas | Página pública: estados de carga/error, contexto del turno y contenedor del cuestionario. |
| `anamnesis.css` | 493 líneas | Estilos de la ficha pública. |
| `anamnesis.js` | 348 líneas | Token del path, GET/POST, validación de consentimiento, fusión de filiatorios y render seguro con `textContent`. |
| `img/marca-franciosi.svg` | — | Marca compacta usada en panel y anamnesis. |
| `img/logo-franciosi.svg` | — | Logo con nombre usado en `/medico`. |
| `img/logo-franciosi-horizontal.svg` | — | Variante horizontal de marca. |
| `img/favicon-franciosi.svg` | — | Favicon con fondo redondeado. |

**Grupos funcionales de `app.js`:**

- constantes de API, obras sociales y menú;
- `state` global (turnos, pacientes, doctores, disponibilidad, caja, fichas);
- utilidades de fecha, moneda, escape HTML y colores;
- cliente HTTP `api()` y `mensajeError()`;
- render de dashboard, agenda, doctores, pacientes, obras sociales y menú;
- historia clínica y anamnesis en modo lectura;
- ficha médica completa (formulario del panel y resumen) y botón del odontograma;
- disponibilidad semanal y excepciones por fecha;
- facturación, caja, convenios, prestaciones y liquidaciones;
- modales de turno, paciente, obra social y opción de menú;
- eventos, inicialización y puente `window.OdontoApp`.

**Grupos funcionales de `medico.js`:**

- selección y recordatorio del doctor (`localStorage`);
- navegación por día/semana y tira de siete días;
- carga de turnos del día/semana y bloques libres;
- alta de paciente y turno;
- edición, confirmación y cancelación;
- historia clínica y alta de evolución inmutable;
- ficha médica (sólo lectura) y odontograma gráfico;
- búsqueda local de fichas y refresco automático.

### 5.6. Scripts (`scripts/`)

| Script | Uso | Qué hace |
|---|---|---|
| `seed.py` | `python -m scripts.seed` | Carga odontólogas, pacientes, obras sociales, agenda, prestaciones, aranceles y turnos demo. Idempotente por claves naturales. |
| `seed_nomenclador_cba.py` | `python -m scripts.seed_nomenclador_cba [--actualizar] [--dry-run] [--sin-detalle]` | Precarga el nomenclador de Córdoba en `prestaciones`; no duplica códigos. |
| `migrar_pacientes_datos.py` | `python -m scripts.migrar_pacientes_datos` | Agrega `dni`, `obra_social` (texto) y `numero_afiliado` a bases legacy. |
| `migrar_obra_social.py` | `python -m scripts.migrar_obra_social` | Crea `obras_sociales`, migra el texto heredado a `obra_social_id` y elimina la columna vieja si SQLite lo permite. |
| `migrar_historias_clinicas.py` | `python -m scripts.migrar_historias_clinicas` | Crea `historias_clinicas`, `evoluciones_medicas` y `archivos_adjuntos` con sus índices. |
| `migrar_facturacion.py` | `python -m scripts.migrar_facturacion` | Crea tablas del Módulo 3 y agrega columnas de pago a `doctores`. |
| `migrar_turnos_unique_parcial.py` | `python -m scripts.migrar_turnos_unique_parcial` | Convierte la unicidad de turnos al índice parcial que excluye cancelados. |
| `migrar_disponibilidad_especifica.py` | `python -m scripts.migrar_disponibilidad_especifica` | Crea la tabla de excepciones por fecha. |
| `migrar_sqlite_a_postgres.py` | `python -m scripts.migrar_sqlite_a_postgres --destino ... [--ejecutar] [--modo reemplazar\|agregar]` | Copia datos preservando IDs, reajusta secuencias y verifica conteos. Sin `--ejecutar` es simulacro. |
| `replicar_datos_api.py` | `python -m scripts.replicar_datos_api [--url ...] [--ejecutar]` | Copia datos locales a la app desplegada usando su API; empareja por clave natural. No replica excepciones ni `wamid`. |
| `enviar_ficha.py` | `python -m scripts.enviar_ficha --telefono\|--dni\|--nombre [--turno] [--api] [--destino] [--listar] [--json]` | Pide a la app el enlace firmado de la ficha y lo imprime (o lo envía por la Cloud API). No toca la base ni firma tokens. |
| `limpiar_base.py` | `python -m scripts.limpiar_base --env-file .env.vercel.local [--modo operativo\|todo] [--ejecutar]` | Vacía filas en una transacción, sin borrar el esquema. Sin `--ejecutar` es simulacro; rechaza SQLite salvo `--permitir-sqlite`. |
| `configurar_url_vercel.py` | `python -m scripts.configurar_url_vercel [--forzar]` | Pide con entrada oculta la `DATABASE_URL` de producción y la guarda en `.env.vercel.local` (ignorado por git). |
| `publicar.py` | `python -m scripts.publicar [opciones]` | Ejecuta pytest, commit, push, deploy Vercel y verificación de `/health`, `/` y `/medico`. |

**Opciones de `publicar.py`:** `-m`, `--sin-tests`, `--sin-deploy`,
`--solo-deploy`, `--forzar`, `--sin-verificar`, `--rama`, `--url`,
`--simulacro`, `--sin-colores`. Códigos de salida: `0` OK, `1` abortado,
`2` publicado con fallas de verificación.

### 5.7. Pruebas (`tests/`)

La suite usa `pytest` con una base **SQLite en memoria** (`StaticPool`), crea
todas las tablas con `Base.metadata.create_all`, sobreescribe `get_db` y evita
el `lifespan` para no tocar la base real. `whatsapp_sin_credenciales` es
`autouse` y fuerza envíos simulados.

| Archivo | Cobertura principal |
|---|---|
| `conftest.py` | Fixtures `engine`, `db_session`, `client`, `doctor`, `paciente`; aislamiento de WhatsApp. |
| `test_anamnesis.py` | Token HMAC, formulario público, ficha completa (familiares, personales, odontológicos), opciones/rangos, separación paciente/profesional, panel y alertas. |
| `test_ficha_pdf.py` | Plantilla de papel completa (todas las etiquetas del relleno), PDF con los datos y las casillas SI/NO marcadas, anexo con odontograma y textos largos, `application/pdf` en la API y `404` de paciente/profesional. |
| `test_contrato_http_turnos.py` | Contrato PATCH de turnos, códigos y CORS. |
| `test_convenios.py` | Grilla de convenios, plantilla, importador CSV/XLSX y cierre de visita. |
| `test_deploy_vercel.py` | Configuración de despliegue y archivos incluidos/excluidos. |
| `test_disponibilidad.py` | Agenda semanal, excepciones, bloques y solapes. |
| `test_doctores.py` | CRUD, baja lógica y reactivación. |
| `test_doctores_pago.py` | Modalidad de pago y su efecto en la liquidación. |
| `test_enviar_ficha.py` | Enlace y envío manual/API de la ficha, el formato del mensaje (enlace en su propia línea), el botón que abre WhatsApp Web y el modal del panel (link clickeable y aviso de URL local), y la CLI con servidor HTTP falso. |
| `test_facturacion.py` | Precios, cierre de caja, cobros, débitos, presentaciones y liquidaciones. |
| `test_fechas_utc_naive.py` | Normalización UTC naive y comparaciones. |
| `test_historias_clinicas.py` | Alta, cronología, ficha y triple inmutabilidad. |
| `test_medico_agenda.py` | Flujo de la vista móvil y búsqueda de pacientes. |
| `test_menu_whatsapp.py` | CRUD del menú, tipos, vista previa y restauración. |
| `test_migracion_sqlite_a_postgres.py` | Copia de datos, mapeos y secuencias. |
| `test_migraciones_esquema.py` | `ALTER TABLE` aditivo, idempotencia y PostgreSQL. |
| `test_obras_sociales.py` | Catálogo, unicidad y vínculo con pacientes. |
| `test_odontograma.py` | Lectura sin crear historia, tres estados por color, reemplazo de dientes, FDI y validaciones. |
| `test_pacientes.py` | DNI, cobertura, búsqueda y normalización. |
| `test_publicar.py` | Ejecutor de comandos, git/Vercel simulados y verificación. |
| `test_recordatorios_click_to_chat.py` | Enlaces `wa.me`, ventana de días y marcado `ENVIADO`. |
| `test_replicar_datos_api.py` | Replicación por API con servidor HTTP falso. |
| `test_reportes_pdf.py` | Estructura PDF, paginado y datos de los reportes. |
| `test_seed_nomenclador_cba.py` | Seed idempotente, `--actualizar` y `--dry-run`. |
| `test_turnos.py` | Alta y listado básico de turnos. |
| `test_turnos_actualizacion.py` | Reprogramación, cancelación y reactivación. |
| `test_turnos_paciente_ubicuo.py` | Prohibición de solapamiento del paciente entre profesionales. |
| `test_turnos_rango_fechas.py` | Filtros `fecha_desde`/`fecha_hasta`. |
| `test_web.py` | Panel, vista móvil, anamnesis, estáticos y `/health`. |
| `test_whatsapp_bot.py` | FAQ Bot, normalización, menú, precios y atención manual. |
| `test_whatsapp_flow.py` | Flujo completo recordatorio → webhook → estado del turno. |

**Ejecución:**

```powershell
python -m pytest
python -m pytest tests/test_facturacion.py -q
python -m pytest --collect-only
```

---

## 6. Documentación detallada de la base de datos

### 6.1. Motor, convenciones y ciclo de vida

- **Desarrollo/pruebas:** SQLite (`sqlite:///./odontologico.db` o memoria).
- **Producción:** PostgreSQL a través de `DATABASE_URL` con driver `psycopg` 3.
- **Vercel sin PostgreSQL:** SQLite en `/tmp/odontologico.db`, efímero.
- **Engine:** `pool_pre_ping=True`; en SQLite `check_same_thread=False`.
- **Sesión:** `autoflush=False`, `autocommit=False`, `expire_on_commit=False`.
- **Fechas:** columnas `UTCDateTime`; en PostgreSQL son
  `TIMESTAMP WITH TIME ZONE` y en SQLite `DATETIME`. En Python siempre se
  entregan como **UTC naive**.
- **Enumeraciones:** se guardan como `VARCHAR` (`native_enum=False`). Salvo las
  restricciones `CHECK` explícitas, la validación de valores vive en Pydantic y
  en los servicios.
- **Importes:** `FLOAT`; no hay `NUMERIC`/`DECIMAL` en la base. El redondeo se
  hace en la capa de servicio con `Decimal`.
- **Sin timestamps universales:** no todas las tablas tienen `created_at` ni
  `updated_at`; sólo `fecha_atencion`, `fecha_subida`, `fecha` y
  `fecha_completado` donde el negocio lo requiere.
- **Sin borrado físico de catálogos:** doctores, obras sociales y prestaciones
  usan baja lógica (`activo`/`activa`).
- **Sin tabla de auditoría:** no se registra quién cambió cada fila.

**Ciclo de inicialización (`init_db`):**

```
Base.metadata.create_all()
        ↓  (crea tablas e índices nuevos; no altera tablas existentes)
aplicar_columnas_faltantes()
        ↓  (ALTER TABLE ADICIONAL e idempotente)
sembrar_menu_por_defecto()
        ↓  (sólo si opciones_menu_whatsapp está vacía)
aplicación lista
```

### 6.2. Diagrama entidad-relación

```
                         ┌────────────────┐
                         │  obras_sociales│
                         └───┬────────┬───┘
                    SET NULL │        │ CASCADE
                 ┌───────────┘        └──────────────┐
                 ▼                                   ▼
          ┌────────────┐                     ┌─────────────────────┐
          │ pacientes  │                     │ aranceles_obra_social│
          └──┬───┬─────┘                     └──────────┬──────────┘
       CASCADE│   │1:1 (unique)                        │ CASCADE
             │   ▼                                     ▼
             │ ┌──────────────────┐             ┌──────────────┐
             │ │ historias_clinicas│             │ prestaciones │
             │ └───┬──────────┬───┘             └──────┬───────┘
             │     │1:N       │1:1 (unique)            │
             │     ▼          ▼                        │
             │ ┌──────────────┐ ┌──────────────────┐   │
             │ │evoluciones_  │ │antecedentes_salud│   │
             │ │medicas       │ └──────────────────┘   │
             │ └───┬──────────┘                        │
             │     │1:N                                │
             │     ▼                                   │
             │ ┌────────────────┐                      │
             │ │archivos_adjuntos│                     │
             │ └────────────────┘                      │
             │1:N                                      │
             ▼                                         │
      ┌────────────┐   1:N    ┌────────────────────┐  │
      │   turnos   │─────────▶│  movimientos_caja  │  │
      └──┬──────┬──┘          └─────────┬──────────┘  │
         │      │1:1 (unique)           │1:N           │
         │      └───────────────────────┘              │
         │                                             │
         │N:1                                          │
         ▼                                             │
  ┌────────────┐   1:N    ┌───────────────────────────┐│
  │  doctores  │─────────▶│ detalles_movimiento_caja  │◀┘
  └──┬───┬───┬─┘          └───────────────────────────┘
     │   │   │1:N
     │   │   └──▶ disponibilidad_doctores
     │   └──────▶ disponibilidad_especifica
     └──────────▶ evoluciones_medicas / movimientos_caja (RESTRICT)
```

**Lectura del diagrama:**

- `turnos` es el eje entre `pacientes`, `doctores` y la caja.
- `historias_clinicas` es el eje clínico entre un paciente, sus evoluciones, su
  ficha médica y su odontograma.
- `aranceles_obra_social` es una **tabla de asociación con atributos** entre
  obras sociales y prestaciones (conceptual N:M).
- `opciones_menu_whatsapp` es una tabla independiente, sin relaciones.

### 6.3. Inventario de tablas

| # | Tabla | Módulo | Propósito |
|---:|---|---|---|
| 1 | `doctores` | 1 / 3 | Profesionales, estado y modalidad de pago. |
| 2 | `obras_sociales` | 3 | Catálogo de coberturas. |
| 3 | `prestaciones` | 3 | Catálogo de prácticas y precio base. |
| 4 | `opciones_menu_whatsapp` | 1 | Menú configurable del FAQ Bot. |
| 5 | `pacientes` | 1 | Datos personales y cobertura. |
| 6 | `turnos` | 1 | Citas y estado de notificación. |
| 7 | `disponibilidad_doctores` | 1 | Agenda semanal. |
| 8 | `disponibilidad_especifica` | 1 | Excepciones por fecha. |
| 9 | `historias_clinicas` | 2 | Cabecera clínica única por paciente. |
| 10 | `evoluciones_medicas` | 2 | Atenciones clínicas inmutables. |
| 11 | `archivos_adjuntos` | 2 | URLs de radiografías/estudios. |
| 12 | `antecedentes_salud` | 4 | Ficha médica (Historia Clínica General). |
| 13 | `aranceles_obra_social` | 3 | Cobertura y coseguro por práctica/OS. |
| 14 | `movimientos_caja` | 3 | Cierre económico de una visita. |
| 15 | `detalles_movimiento_caja` | 3 | Prácticas e importes congelados de la visita. |
| 16 | `odontogramas` | 4 | Cabecera del odontograma (prótesis, coronas, conteos). |
| 17 | `odontograma_dientes` | 4 | Estado pintado de cada diente (rojo/azul/X). |

### 6.4. Diccionario de datos exhaustivo

> Convenciones de las tablas:
>
> - **PK**: clave primaria entera autoincremental (índice implícito).
> - **FK**: clave foránea, con la acción `ON DELETE` indicada.
> - **Nulo**: `No` = `NOT NULL`; `Sí` = admite `NULL`.
> - **Default**: valor a nivel de base (`server_default`). El default Python
>   puede ser equivalente, pero no se lista por separado.
> - Los tipos se muestran como `SQLite / PostgreSQL`.

#### 6.4.1. `doctores`

Almacena a los profesionales odontólogos, su estado y la modalidad con la que
se liquida su producción.

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `nombre` | `VARCHAR(120) / VARCHAR(120)` | No | — | — | Nombre visible del profesional. |
| `especialidad` | `VARCHAR(120) / VARCHAR(120)` | No | — | — | Especialidad odontológica. |
| `activo` | `BOOLEAN / BOOLEAN` | No | `true` | — | Baja lógica; `false` no recibe turnos nuevos. |
| `tipo_pago` | `VARCHAR(15) / VARCHAR(15)` | No | `'DUENO'` | — | `DUENO`, `COMISION` o `SUELDO`; sin `CHECK` en base. |
| `valor_pago` | `FLOAT / FLOAT` | Sí | `NULL` | — | Porcentaje 0-100 para dueño/comisión o importe mensual para sueldo. |

**Restricciones y notas:**

- `ix_doctores_id` es un índice no único sobre la PK (redundante en la mayoría
  de los motores).
- `tipo_pago` se valida en Pydantic y en `doctor_service`; la base **no** tiene
  `CHECK` para ese enum.
- `valor_pago` es `NULL` mientras no se configura. En `DUENO`, `NULL` significa
  100 % de la producción; en `COMISION` es obligatorio; en `SUELDO` debe ser
  mayor que 0.
- No hay `created_at`/`updated_at`.
- El borrado físico no se usa: los endpoints hacen baja lógica.

#### 6.4.2. `obras_sociales`

Catálogo de obras sociales y prepagas que pueden asignarse a un paciente y
tener un convenio de aranceles.

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `nombre` | `VARCHAR(120) / VARCHAR(120)` | No | — | **UQ** `uq_obra_social_nombre` | Nombre de la cobertura. |
| `codigo` | `VARCHAR(30) / VARCHAR(30)` | Sí | `NULL` | — | Código interno o del financiador. |
| `activa` | `BOOLEAN / BOOLEAN` | No | `true` | — | Baja lógica; deja de ofrecerse en desplegables. |

**Restricciones y notas:**

- El índice único `uq_obra_social_nombre` es **case-sensitive** en SQLite y
  PostgreSQL. El servicio valida con `lower()` antes de crear/editar; ante una
  carrera, el índice protege la unicidad exacta, no la equivalencia de
  mayúsculas.
- `activa = false` conserva la fila porque los pacientes siguen apuntándole.
- No hay `created_at`/`updated_at`.

#### 6.4.3. `prestaciones`

Catálogo de prácticas facturables con su código de nomenclador y precio de
referencia.

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `codigo_nomenclador` | `VARCHAR(30) / VARCHAR(30)` | No | — | **UQ** `uq_prestacion_codigo` | Código de la práctica (se normaliza a mayúsculas en el servicio). |
| `descripcion` | `VARCHAR(200) / VARCHAR(200)` | No | — | — | Descripción de la práctica. |
| `valor_base` | `FLOAT / FLOAT` | No | `0` | — | Precio de referencia particular. |
| `activa` | `BOOLEAN / BOOLEAN` | No | `true` | — | Baja lógica; no se ofrece al facturar. |

**Restricciones y notas:**

- El código es único; la comparación del servicio es case-insensitive, pero el
  índice de base es exacto.
- Las facturas ya emitidas no dependen de esta fila: guardan copia en
  `detalles_movimiento_caja`.
- No hay `created_at`/`updated_at`.

#### 6.4.4. `opciones_menu_whatsapp`

Menú configurable que recibe el paciente cuando escribe al WhatsApp del
consultorio.

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `numero` | `VARCHAR(1) / VARCHAR(1)` | No | — | **UQ** `uq_opcion_menu_numero` | Tecla que responde el paciente (un dígito). |
| `titulo` | `VARCHAR(80) / VARCHAR(80)` | No | — | — | Texto que se muestra en el menú. |
| `tipo` | `VARCHAR(20) / VARCHAR(20)` | No | `'TEXTO'` | — | `TEXTO`, `PRECIO`, `OBRAS_SOCIALES`, `UBICACION` o `SECRETARIA`; sin `CHECK`. |
| `respuesta` | `TEXT / TEXT` | Sí | `NULL` | — | Texto libre para las opciones de tipo `TEXTO`; obligatorio en ese caso a nivel de servicio. |
| `activa` | `BOOLEAN / BOOLEAN` | No | `true` | — | Baja lógica; no se muestra ni se atiende. |
| `orden` | `INTEGER / INTEGER` | No | `0` | — | Orden de aparición; desempate por `numero`. |

**Restricciones y notas:**

- La tabla no tiene claves foráneas.
- Si está vacía, el bot usa cuatro opciones históricas no persistidas; `init_db`
  las siembra una sola vez.
- `restaurar_menu_por_defecto` borra todas las filas y vuelve a insertar las
  cuatro históricas.

#### 6.4.5. `pacientes`

Datos administrativos y **filiatorios** de la persona que reserva turnos y recibe
notificaciones. Los filiatorios son los del encabezado de la Historia Clínica
General y los comparte la ficha médica (Módulo 4).

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `nombre` | `VARCHAR(120) / VARCHAR(120)` | No | — | — | Nombre completo. |
| `dni` | `VARCHAR(20) / VARCHAR(20)` | Sí | `NULL` | **UQ** `uq_paciente_dni` | Documento normalizado (sin puntos/guiones). |
| `telefono` | `VARCHAR(30) / VARCHAR(30)` | No | — | **IDX** `ix_pacientes_telefono` | Teléfono de contacto; no es único. |
| `email` | `VARCHAR(120) / VARCHAR(120)` | Sí | `NULL` | — | Correo validado por `EmailStr`. |
| `obra_social_id` | `INTEGER / INTEGER` | Sí | `NULL` | **FK** → `obras_sociales.id` (`SET NULL`); **IDX** `ix_pacientes_obra_social_id` | Cobertura del paciente. |
| `numero_afiliado` | `VARCHAR(60) / VARCHAR(60)` | Sí | `NULL` | — | Número de afiliado dentro de la cobertura. |
| `fecha_nacimiento` | `DATE / DATE` | Sí | `NULL` | — | Filiatorio: fecha de nacimiento. La **edad no se persiste**, se calcula en `PacienteRead`. |
| `estado_civil` | `VARCHAR(30) / VARCHAR(30)` | Sí | `NULL` | — | Filiatorio: estado civil. |
| `nacionalidad` | `VARCHAR(60) / VARCHAR(60)` | Sí | `NULL` | — | Filiatorio: nacionalidad. |
| `domicilio` | `VARCHAR(200) / VARCHAR(200)` | Sí | `NULL` | — | Filiatorio: calle, número, barrio y localidad. |
| `profesion` | `VARCHAR(120) / VARCHAR(120)` | Sí | `NULL` | — | Filiatorio: profesión o actividad. |
| `titular` | `VARCHAR(120) / VARCHAR(120)` | Sí | `NULL` | — | Filiatorio: titular de la obra social. |
| `lugar_trabajo` | `VARCHAR(120) / VARCHAR(120)` | Sí | `NULL` | — | Filiatorio: lugar de trabajo. |
| `jerarquia` | `VARCHAR(60) / VARCHAR(60)` | Sí | `NULL` | — | Filiatorio: jerarquía o categoría laboral. |

**Restricciones y notas:**

- `uq_paciente_dni` es un índice único: admite múltiples `NULL`, pero no dos
  documentos informados iguales. La normalización se hace en Pydantic; la base
  no valida el formato.
- `telefono` es obligatorio pero no único: dos pacientes podrían compartir un
  teléfono familiar.
- No existe endpoint `DELETE /pacientes`; no hay baja lógica ni física expuesta.
- `obra_social_id` es `SET NULL`: si se borrara físicamente una cobertura, el
  paciente queda sin cobertura, pero no se elimina.
- Los **datos filiatorios** (los ocho del encabezado de la Historia Clínica
  General) son la única fuente de verdad: la ficha médica los muestra y, al
  guardarla, `anamnesis_service` los copia acá. Un campo vacío **no borra** un
  dato ya cargado; para limpiarlo se usa `PATCH /api/v1/pacientes/{id}` con
  `null`, que sí distingue «no enviado» de «vaciar». La ficha expone los valores
  vigentes en `datos_filiatorios` (respuesta pública y panel) y también dentro de
  `antecedentes_salud` cuando ya está completada.

#### 6.4.6. `turnos`

Cita de un paciente con un profesional, con estado clínico y estado de
notificación por WhatsApp.

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `paciente_id` | `INTEGER / INTEGER` | No | — | **FK** → `pacientes.id` (`CASCADE`); **IDX** `ix_turnos_paciente_id` | Paciente citado. |
| `doctor_id` | `INTEGER / INTEGER` | No | — | **FK** → `doctores.id` (`CASCADE`); **IDX** `ix_turnos_doctor_id` | Profesional que atiende. |
| `fecha_hora_inicio` | `DATETIME / TIMESTAMP WITH TIME ZONE` | No | — | **IDX** `ix_turnos_fecha_hora_inicio`; **UQ parcial** `uq_turno_doctor_fecha_inicio` | Inicio del turno (UTC naive en Python). |
| `fecha_hora_fin` | `DATETIME / TIMESTAMP WITH TIME ZONE` | No | — | — | Fin del turno; debe ser posterior al inicio. |
| `estado_turno` | `VARCHAR(20) / VARCHAR(20)` | No | `'PENDIENTE'` | — | `PENDIENTE`, `CONFIRMADO` o `CANCELADO`; sin `CHECK`. |
| `estado_whatsapp` | `VARCHAR(20) / VARCHAR(20)` | No | `'PENDIENTE_ENVIO'` | — | `PENDIENTE_ENVIO`, `ENVIADO`, `FALLIDO` o `RESPONDIDO`; sin `CHECK`. |
| `whatsapp_mensaje_id` | `VARCHAR(128) / VARCHAR(128)` | Sí | `NULL` | **IDX** `ix_turnos_whatsapp_mensaje_id` | `wamid` del mensaje saliente para correlacionar el botón. |

**Restricciones e índices:**

- **PK** implícita (`ix_turnos_id`).
- `uq_turno_doctor_fecha_inicio`: índice **único parcial** sobre
  `(doctor_id, fecha_hora_inicio)` con `WHERE estado_turno != 'CANCELADO'`.
  Un doctor no puede tener dos turnos vigentes con el mismo inicio; cancelar
  libera ese inicio.
- `ck_turno_fin_posterior_inicio`: `CHECK (fecha_hora_fin > fecha_hora_inicio)`.
- `ix_turnos_paciente_id`, `ix_turnos_doctor_id`,
  `ix_turnos_fecha_hora_inicio`, `ix_turnos_whatsapp_mensaje_id`.
- **No** hay restricción de exclusión para solapamientos parciales: se validan
  en `turno_service` (`inicio < fin_ajeno` y `fin > inicio_ajeno`).
- **No** hay unicidad por paciente: el control de “paciente ubicuo” también es
  de servicio.
- `estado_turno` y `estado_whatsapp` no tienen `CHECK`; una escritura SQL
  directa podría guardar un valor no previsto.

#### 6.4.7. `disponibilidad_doctores`

Agenda **semanal** habitual del profesional: varios tramos por día de la semana.

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `doctor_id` | `INTEGER / INTEGER` | No | — | **FK** → `doctores.id` (`CASCADE`); **IDX** `ix_disponibilidad_doctor_dia` | Profesional. |
| `dia_semana` | `INTEGER / INTEGER` | No | — | **IDX** `ix_disponibilidad_doctor_dia` | 0 = domingo … 6 = sábado. |
| `hora_inicio` | `TIME / TIME WITHOUT TIME ZONE` | No | — | — | Inicio del tramo. |
| `hora_fin` | `TIME / TIME WITHOUT TIME ZONE` | No | — | — | Fin del tramo, estrictamente posterior. |

**Restricciones e índices:**

- `ck_disponibilidad_dia_semana_valido`:
  `CHECK (dia_semana >= 0 AND dia_semana <= 6)`.
- `ck_disponibilidad_fin_posterior_inicio`: `CHECK (hora_fin > hora_inicio)`.
- `ix_disponibilidad_doctor_dia` compuesto `(doctor_id, dia_semana)`.
- `ix_disponibilidad_doctores_doctor_id` e `ix_disponibilidad_doctores_id`.
- **No** hay unicidad por día: se permiten varios rangos (mañana/tarde).
- **No** hay restricción de solape en base: `disponibilidad_service` y el schema
  de reemplazo rechazan solapes del mismo día. Los rangos contiguos se aceptan.
- `dia_semana` sigue la convención de JavaScript `Date.getDay()`.

#### 6.4.8. `disponibilidad_especifica`

Excepciones de agenda para **una fecha concreta**.

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `doctor_id` | `INTEGER / INTEGER` | No | — | **FK** → `doctores.id` (`CASCADE`); **IDX** `ix_especifica_doctor_fecha` | Profesional. |
| `fecha` | `DATE / DATE` | No | — | **IDX** `ix_especifica_doctor_fecha`; **IDX** `ix_disponibilidad_especifica_fecha` | Fecha calendario afectada. |
| `hora_inicio` | `TIME / TIME WITHOUT TIME ZONE` | No | — | — | Inicio del tramo. |
| `hora_fin` | `TIME / TIME WITHOUT TIME ZONE` | No | — | — | Fin del tramo, estrictamente posterior. |
| `tipo` | `VARCHAR(20) / VARCHAR(20)` | No | `'HABILITADO'` | — | `HABILITADO` suma atención; `BLOQUEADO` la resta. |

**Restricciones e índices:**

- `ck_especifica_fin_posterior_inicio`: `CHECK (hora_fin > hora_inicio)`.
- `ck_especifica_tipo_valido`:
  `CHECK (tipo IN ('HABILITADO', 'BLOQUEADO'))`.
- `ix_especifica_doctor_fecha` compuesto `(doctor_id, fecha)`.
- `ix_disponibilidad_especifica_doctor_id`, `ix_disponibilidad_especifica_fecha`
  e `ix_disponibilidad_especifica_id`.
- **No** hay unicidad por `(doctor, fecha, hora_inicio)`: se permiten varios
  tramos; el servicio valida solapes de tramos del mismo tipo.
- Regla de precedencia: si existe **cualquier** excepción para la fecha, la
  agenda semanal se ignora y el día se calcula con `HABILITADO` − `BLOQUEADO`.

#### 6.4.9. `historias_clinicas`

Cabecera de la ficha clínica de un paciente.

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `paciente_id` | `INTEGER / INTEGER` | No | — | **FK** → `pacientes.id` (`CASCADE`); **UQ** `uq_historia_paciente`; **IDX** `ix_historias_clinicas_paciente_id` | Paciente dueño de la ficha. |
| `numero_ficha` | `VARCHAR(30) / VARCHAR(30)` | No | — | **UQ** `uq_historia_numero_ficha` | Número visible `HC-000123`. |

**Restricciones y notas:**

- `uq_historia_paciente` garantiza la relación **1:1** con `pacientes`.
- `uq_historia_numero_ficha` garantiza que no se repita el número visible.
- El número se deriva del `paciente_id` (`HC-{id:06d}`) y lo asigna el servicio,
  no la base.
- La ficha se crea con la primera evolución o la primera anamnesis; no se crea
  desde un `GET` de sólo lectura.
- No hay endpoint de borrado de historia.

#### 6.4.10. `antecedentes_salud`

Ficha médica que el paciente completa antes de la consulta y que el profesional
termina de cargar desde el panel. Cuelga de la historia clínica, no directamente
del paciente, y reproduce la **Historia Clínica General** del consultorio
(`Historia-Clinica-GeneralNUEVA.pdf`).

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `historia_clinica_id` | `INTEGER / INTEGER` | No | — | **FK** → `historias_clinicas.id` (`CASCADE`); **UQ** `uq_antecedentes_historia_clinica` | Ficha clínica asociada. |
| `completado` | `BOOLEAN / BOOLEAN` | No | `false` | — | Indica que la ficha fue enviada. |
| `fecha_completado` | `DATETIME / TIMESTAMP WITH TIME ZONE` | Sí | `NULL` | — | Momento del envío (UTC naive en Python). |

**Bloques del cuestionario.** Los booleanos son `BOOLEAN NOT NULL DEFAULT false`;
los textos son `TEXT NULL` (máx. 1000 en la API) y las opciones `VARCHAR(20)
NULL`:

| Bloque | Booleanos | Textos | Opciones / números |
|---|---|---|---|
| Familiares | `padre_vive`, `madre_vive`, `hermanos`, `hermanos_sanos` | `padre_patologias`, `madre_patologias`, `hermanos_detalle` | — |
| Personales | `enfermedad_actual`, `tratamiento_medico`, `deporte`, `deporte_malestar`, `alergia_anestesia`, `alergia_penicilina`, `cicatriza_bien`, `sangra_mucho`, `colageno`, `fiebre_reumatica`, `diabetes`, `diabetes_controlada`, `problemas_cardiacos`, `anticoagulantes`, `presion_alta`, `chagas`, `problemas_renales`, `ulcera_gastrica`, `hepatitis`, `problemas_hepaticos`, `convulsiones`, `epilepsia`, `sifilis_gonorrea`, `enfermedad_infectocontagiosa`, `transfusiones`, `operaciones`, `problemas_respiratorios`, `fuma`, `embarazo`, `otra_enfermedad` | `enfermedad_actual_detalle`, `tratamiento_medico_detalle`, `medicacion_actual`, `medicamentos_ultimos_5_anios`, `alergias_medicamentos`, `alergia_otros`, `fiebre_reumatica_proteccion`, `diabetes_tratamiento`, `problemas_cardiacos_detalle`, `anticoagulantes_frecuencia`, `chagas_tratamiento`, `problemas_hepaticos_detalle`, `epilepsia_medicacion`, `enfermedad_infectocontagiosa_detalle`, `operaciones_detalle`, `operaciones_cuando`, `problemas_respiratorios_detalle`, `otra_enfermedad_detalle`, `tratamientos_alternativos`, `medico_clinico`, `centro_derivacion` | `hepatitis_tipo` (`A`/`B`/`C`); `embarazo_meses` (`INTEGER` 0-12) |
| Odontológicos | `consulta_previa`, `medicacion_previa`, `medicacion_previa_resultados`, `dolor`, `traumatismo_dental`, `fractura_dental`, `dificultad_hablar`, `dificultad_masticar`, `dificultad_abrir_boca`, `dificultad_tragar` | `medicacion_previa_detalle`, `medicacion_previa_desde`, `dolor_zona`, `dolor_calmante`, `traumatismo_cuando`, `traumatismo_como`, `fractura_cual`, `fractura_tratamiento` | `dolor_intensidad`, `dolor_duracion`, `dolor_desencadenante`, `dolor_estimulo`, `dolor_localizacion` |
| Examen bucal | `labios_anormal`, `lengua_anormal`, `paladar_anormal`, `piso_boca_anormal`, `carrillos_anormal`, `rebordes_anormal`, `trigono_anormal`, `retromolar_anormal`, `lesiones_manchas`, `lesiones_abultamiento`, `lesiones_ulceraciones`, `lesiones_ampollas`, `sangrado_encias`, `pus_boca`, `movilidad_dental`, `dientes_altos`, `cara_hinchada`, `cara_hinchada_hielo`, `cara_hinchada_calor`, `sarro`, `enfermedad_periodontal` | `examen_bucal_detalle`, `lesiones_otros`, `sangrado_encias_cuando`, `pus_boca_donde`, `cara_hinchada_otros`, `indice_placa` | `higiene_bucal` (`MUY_BUENO`, `BUENO`, `DEFICIENTE`, `MALO`); `momentos_azucar` (`INTEGER` 0-50) |
| Diagnóstico | — | `diagnostico_presuntivo`, `plan_tratamiento`, `observaciones` | `fecha_plan` (`DATE`) |

**Restricciones y notas:**

- `uq_antecedentes_historia_clinica` impide duplicar el cuestionario por ficha.
- `motivos_alerta` y `alerta_medica` **no son columnas**: se calculan con
  `calcular_motivos_alerta` (modelo) y se exponen como campos Pydantic, así la
  tarjeta del panel y la API nunca se desincronizan.
- El enlace público sólo escribe los bloques del paciente
  (`DeclaracionSaludCreate`): el **examen bucal** y el **diagnóstico** sólo los
  acepta el `PUT` interno con `FichaMedicaUpdate`.
- Los **datos filiatorios** de la ficha (`fecha_nacimiento`, `estado_civil`,
  `nacionalidad`, `domicilio`, `profesion`, `titular`, `lugar_trabajo`,
  `jerarquia`) **no se guardan en esta tabla**: el servicio los sincroniza en
  `pacientes` y al leer los completa desde el paciente. Así la secretaría no los
  tipea dos veces y una corrección en el formulario de pacientes se ve en la
  ficha.
- Los textos vacíos se normalizan a `NULL` y las opciones cerradas se guardan en
  MAYÚSCULAS.
- No hay historial de versiones de la ficha: el reenvío actualiza la misma fila.

#### 6.4.11. `evoluciones_medicas`

Cada atención registrada en la historia clínica. Es un registro clínico con
valor legal y, por diseño, **inmutable**.

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `historia_clinica_id` | `INTEGER / INTEGER` | No | — | **FK** → `historias_clinicas.id` (`CASCADE`); **IDX** `ix_evolucion_historia_fecha` | Ficha del paciente. |
| `doctor_id` | `INTEGER / INTEGER` | No | — | **FK** → `doctores.id` (`RESTRICT`); **IDX** `ix_evoluciones_medicas_doctor_id` | Profesional que atendió. |
| `fecha_atencion` | `DATETIME / TIMESTAMP WITH TIME ZONE` | No | `CURRENT_TIMESTAMP` / `now()` | **IDX** `ix_evolucion_historia_fecha`; **IDX** `ix_evoluciones_medicas_fecha_atencion` | Fecha/hora de la atención. |
| `motivo_consulta` | `TEXT / TEXT` | No | — | — | Motivo de la consulta. |
| `diagnostico` | `TEXT / TEXT` | No | — | — | Diagnóstico. |
| `tratamiento_efectuado` | `TEXT / TEXT` | No | — | — | Tratamiento realizado. |
| `observaciones` | `TEXT / TEXT` | Sí | `NULL` | — | Observaciones adicionales. |

**Restricciones e índices:**

- `ix_evolucion_historia_fecha` compuesto `(historia_clinica_id, fecha_atencion)`.
- Índices individuales sobre `historia_clinica_id`, `doctor_id`,
  `fecha_atencion` e `id`.
- **Inmutabilidad:** los eventos ORM `before_update` y `before_delete` lanzan
  `RegistroInmutableError`; el servicio no expone edición/baja; la API no declara
  `PUT`/`PATCH`/`DELETE`.
- `doctor_id` usa `RESTRICT` para que un profesional con evoluciones no pueda
  borrarse físicamente.
- `historia_clinica_id` usa `CASCADE`; en la práctica no se borra una historia.
- Los textos clínicos tienen un máximo de 4000 caracteres en el schema.

#### 6.4.12. `archivos_adjuntos`

Referencias a radiografías, fotos o estudios asociados a una evolución.

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `evolucion_id` | `INTEGER / INTEGER` | No | — | **FK** → `evoluciones_medicas.id` (`CASCADE`); **IDX** `ix_adjunto_evolucion` | Evolución a la que pertenece. |
| `url_archivo` | `VARCHAR(500) / VARCHAR(500)` | No | — | — | URL del archivo ya subido. |
| `tipo_archivo` | `VARCHAR(30) / VARCHAR(30)` | No | `'RADIOGRAFIA'` | — | Tipo normalizado a mayúsculas (`FOTO`, `ESTUDIO`, `OTRO`…). |
| `fecha_subida` | `DATETIME / TIMESTAMP WITH TIME ZONE` | No | `CURRENT_TIMESTAMP` / `now()` | — | Momento de alta del adjunto. |

**Restricciones y notas:**

- No se guarda el binario, ni tamaño, ni MIME, ni hash: sólo la URL.
- `ix_adjunto_evolucion` y `ix_archivos_adjuntos_evolucion_id` indexan la FK.
- No hay endpoint independiente de adjuntos: se crean junto con la evolución.

#### 6.4.13. `aranceles_obra_social`

Acuerdo económico entre una obra social y una práctica: cuánto cubre la
cobertura y cuánto paga el paciente como coseguro.

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `obra_social_id` | `INTEGER / INTEGER` | No | — | **FK** → `obras_sociales.id` (`CASCADE`); **IDX** `ix_aranceles_obra_social_obra_social_id` | Cobertura. |
| `prestacion_id` | `INTEGER / INTEGER` | No | — | **FK** → `prestaciones.id` (`CASCADE`); **IDX** `ix_aranceles_obra_social_prestacion_id` | Práctica. |
| `monto_cubre_os` | `FLOAT / FLOAT` | No | `0` | — | Importe que paga la obra social. |
| `monto_coseguro_paciente` | `FLOAT / FLOAT` | No | `0` | — | Importe a cobrar en el mostrador. |

**Restricciones y notas:**

- `uq_arancel_os_prestacion` es un índice único sobre
  `(obra_social_id, prestacion_id)`: una cobertura no puede tener dos aranceles
  para la misma práctica.
- No hay `CHECK` de montos no negativos en la base; los schemas y servicios
  validan `>= 0`.
- Ambas FK usan `CASCADE`: el arancel es configuración; si se borra la cobertura
  o la práctica, la fila no tiene sentido.
- La relación conceptual es **N:M** entre `obras_sociales` y `prestaciones`,
  resuelta con esta tabla de asociación con atributos.

#### 6.4.14. `movimientos_caja`

Cierre económico de una visita. Resume lo cobrado en efectivo y lo pendiente de
la obra social.

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `turno_id` | `INTEGER / INTEGER` | No | — | **FK** → `turnos.id` (`RESTRICT`); **UQ** `uq_movimiento_caja_turno` | Turno facturado; un único cierre por turno. |
| `doctor_id` | `INTEGER / INTEGER` | No | — | **FK** → `doctores.id` (`RESTRICT`); **IDX** `ix_movimientos_caja_doctor_id` | Profesional que produjo la atención. |
| `fecha` | `DATETIME / TIMESTAMP WITH TIME ZONE` | No | `CURRENT_TIMESTAMP` / `now()` | **IDX** `ix_movimiento_caja_fecha`; **IDX** `ix_movimientos_caja_fecha` | Fecha del cierre; usada por caja y liquidaciones. |
| `monto_efectivo` | `FLOAT / FLOAT` | No | `0` | — | Cobrado en mostrador (coseguros + particulares). |
| `monto_pendiente_os` | `FLOAT / FLOAT` | No | `0` | — | Importe presentado a la obra social. |
| `monto_cobrado_os` | `FLOAT / FLOAT` | Sí | `NULL` | — | Importe realmente acreditado por la OS; `NULL` = todavía no pagó. |
| `estado_pago` | `VARCHAR(25) / VARCHAR(25)` | No | `'PENDIENTE'` | — | `PENDIENTE`, `EN_PROCESO_LIQUIDACION`, `COBRADO` o `DEBITADO_OS`; sin `CHECK`. |
| `observaciones` | `TEXT / TEXT` | Sí | `NULL` | — | Nota del cierre o motivo del débito. |

**Restricciones e índices:**

- `uq_movimiento_caja_turno` garantiza **un solo movimiento por turno**.
- `ix_movimiento_caja_fecha` e `ix_movimientos_caja_fecha` indexan `fecha`
  (el esquema del modelo declara el primero; el segundo puede existir como
  índice legacy en bases ya migradas).
- `ix_movimientos_caja_turno_id`, `ix_movimientos_caja_doctor_id`,
  `ix_movimientos_caja_id`.
- `turno_id` y `doctor_id` usan `RESTRICT`: el movimiento es un comprobante
  contable y no debe desaparecer en cascada.
- No hay `CHECK` de importes ni de estado.
- `monto_total` no se guarda: se calcula como `monto_efectivo +
  monto_pendiente_os` en los DTO/servicios.
- En un paciente particular, `monto_cobrado_os` se guarda `0` y el movimiento
  nace `COBRADO`.

#### 6.4.15. `detalles_movimiento_caja`

Prácticas realizadas en una visita, con los importes **congelados** al momento
de facturar.

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `movimiento_caja_id` | `INTEGER / INTEGER` | No | — | **FK** → `movimientos_caja.id` (`CASCADE`); **IDX** `ix_detalle_movimiento` | Movimiento padre. |
| `prestacion_id` | `INTEGER / INTEGER` | No | — | **FK** → `prestaciones.id` (`RESTRICT`); **IDX** `ix_detalles_movimiento_caja_prestacion_id` | Práctica original. |
| `codigo_nomenclador` | `VARCHAR(30) / VARCHAR(30)` | No | — | — | Copia del código al facturar. |
| `descripcion` | `VARCHAR(200) / VARCHAR(200)` | No | — | — | Copia de la descripción al facturar. |
| `cantidad` | `INTEGER / INTEGER` | No | `1` | — | Repeticiones de la práctica (1-99 en API). |
| `monto_cubre_os` | `FLOAT / FLOAT` | No | `0` | — | Total que cubre la OS para esa línea. |
| `monto_coseguro_paciente` | `FLOAT / FLOAT` | No | `0` | — | Total del coseguro para esa línea. |

**Restricciones y notas:**

- `ix_detalle_movimiento` e `ix_detalles_movimiento_caja_movimiento_caja_id`
  indexan la FK del movimiento.
- `prestacion_id` usa `RESTRICT`: la práctica no puede borrarse si está facturada.
- `movimiento_caja_id` usa `CASCADE`: los detalles pertenecen al movimiento.
- No hay unicidad por práctica dentro del movimiento; el payload puede repetir
  una práctica y se generarían líneas separadas.
- No hay columna de total por línea: se deriva como
  `monto_cubre_os + monto_coseguro_paciente`.
- Los campos congelados permiten que una factura histórica no cambie si luego se
  modifica el catálogo de prestaciones.

#### 6.4.16. `odontogramas`

Cabecera del odontograma que completa el profesional **después** de revisar al
paciente. Un odontograma por historia clínica.

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `historia_clinica_id` | `INTEGER / INTEGER` | No | — | **FK** → `historias_clinicas.id` (`CASCADE`); **UQ** `uq_odontograma_historia` | Ficha clínica asociada. |
| `doctor_id` | `INTEGER / INTEGER` | Sí | `NULL` | **FK** → `doctores.id` (`SET NULL`) | Profesional que cargó el gráfico. |
| `protesis_fija` | `TEXT / TEXT` | Sí | `NULL` | — | Prótesis fija (texto libre). |
| `protesis_removible` | `TEXT / TEXT` | Sí | `NULL` | — | Prótesis removible. |
| `coronas` | `TEXT / TEXT` | Sí | `NULL` | — | Coronas. |
| `dientes_existentes` | `INTEGER / INTEGER` | Sí | `NULL` | — | Cantidad de dientes existentes (0-32). |
| `observaciones` | `TEXT / TEXT` | Sí | `NULL` | — | Observaciones del gráfico. |
| `actualizado_en` | `DATETIME / TIMESTAMP WITH TIME ZONE` | No | `now()` | — | Último guardado (UTC naive en Python). |

#### 6.4.17. `odontograma_dientes`

Estado pintado de cada diente. Sólo se guardan los dientes con estado: el `PUT`
reemplaza el conjunto completo en cada guardado.

| Campo | Tipo SQLite / PostgreSQL | Nulo | Default | Clave / índice | Descripción |
|---|---|---|---|---|---|
| `id` | `INTEGER / INTEGER` | No | autoincremental | **PK** | Identificador interno. |
| `odontograma_id` | `INTEGER / INTEGER` | No | — | **FK** → `odontogramas.id` (`CASCADE`); **UQ** `uq_odontograma_diente` | Odontograma dueño. |
| `diente` | `INTEGER / INTEGER` | No | — | **UQ** `uq_odontograma_diente` | Número FDI 11-18, 21-28, 31-38 o 41-48. |
| `estado` | `VARCHAR(12) / VARCHAR(12)` | No | — | — | `EXISTENTE` (rojo), `REQUERIDO` (azul) o `AUSENTE` (X). |
| `notas` | `TEXT / TEXT` | Sí | `NULL` | — | Detalle libre del diente. |

**Restricciones y notas:**

- `uq_odontograma_diente` impide dos filas del mismo diente en un odontograma.
- La API valida los números FDI contra `DIENTES_PERMANENTES` y los estados contra
  `ESTADOS_DIENTE`; un valor fuera de rango, un diente repetido o un estado
  desconocido responde `422`.
- `GET /pacientes/{id}/odontograma` **no** crea la historia clínica; el `PUT` sí
  la crea, igual que la ficha de salud.

### 6.5. Relaciones del sistema

| Origen | Campo | Destino | Cardinalidad | `ON DELETE` | Regla |
|---|---|---|---|---|---|
| `pacientes` | `obra_social_id` | `obras_sociales.id` | N:1 | `SET NULL` | Un paciente tiene una cobertura o ninguna. |
| `turnos` | `paciente_id` | `pacientes.id` | N:1 | `CASCADE` | Un paciente tiene muchos turnos. |
| `turnos` | `doctor_id` | `doctores.id` | N:1 | `CASCADE` | Un doctor tiene muchos turnos. |
| `disponibilidad_doctores` | `doctor_id` | `doctores.id` | N:1 | `CASCADE` | Varios rangos semanales por doctor. |
| `disponibilidad_especifica` | `doctor_id` | `doctores.id` | N:1 | `CASCADE` | Varias excepciones por doctor. |
| `historias_clinicas` | `paciente_id` | `pacientes.id` | **1:1** (UQ) | `CASCADE` | Una ficha por paciente. |
| `antecedentes_salud` | `historia_clinica_id` | `historias_clinicas.id` | **1:1** (UQ) | `CASCADE` | Una ficha médica por ficha clínica. |
| `odontogramas` | `historia_clinica_id` | `historias_clinicas.id` | **1:1** (UQ) | `CASCADE` | Un odontograma por ficha clínica. |
| `odontogramas` | `doctor_id` | `doctores.id` | N:1 | `SET NULL` | Profesional que cargó el gráfico. |
| `odontograma_dientes` | `odontograma_id` | `odontogramas.id` | N:1 | `CASCADE` | Varios dientes por odontograma. |
| `evoluciones_medicas` | `historia_clinica_id` | `historias_clinicas.id` | N:1 | `CASCADE` | Muchas atenciones por ficha. |
| `evoluciones_medicas` | `doctor_id` | `doctores.id` | N:1 | `RESTRICT` | El doctor no se borra si firmó evoluciones. |
| `archivos_adjuntos` | `evolucion_id` | `evoluciones_medicas.id` | N:1 | `CASCADE` | Varios archivos por evolución. |
| `aranceles_obra_social` | `obra_social_id` | `obras_sociales.id` | N:1 | `CASCADE` | Varios aranceles por cobertura. |
| `aranceles_obra_social` | `prestacion_id` | `prestaciones.id` | N:1 | `CASCADE` | Varios aranceles por práctica. |
| `movimientos_caja` | `turno_id` | `turnos.id` | **1:1** (UQ) | `RESTRICT` | Un cierre por turno. |
| `movimientos_caja` | `doctor_id` | `doctores.id` | N:1 | `RESTRICT` | El doctor no se borra si tiene caja. |
| `detalles_movimiento_caja` | `movimiento_caja_id` | `movimientos_caja.id` | N:1 | `CASCADE` | Varias prácticas por movimiento. |
| `detalles_movimiento_caja` | `prestacion_id` | `prestaciones.id` | N:1 | `RESTRICT` | La práctica no se borra si está facturada. |

**Relación N:M conceptual:** `obras_sociales` ↔ `prestaciones`, resuelta por
`aranceles_obra_social` con los atributos `monto_cubre_os` y
`monto_coseguro_paciente`. No hay una tabla puente pura sin atributos.

**Relaciones 1:1 reales (defendidas por índice único):**

- `pacientes` ↔ `historias_clinicas` (`uq_historia_paciente`).
- `historias_clinicas` ↔ `antecedentes_salud` (`uq_antecedentes_historia_clinica`).
- `historias_clinicas` ↔ `odontogramas` (`uq_odontograma_historia`).
- `turnos` ↔ `movimientos_caja` (`uq_movimiento_caja_turno`).

### 6.6. Restricciones e integridad

#### Restricciones declaradas en la base

| Tipo | Nombre | Tabla | Definición |
|---|---|---|---|
| Índice único | `uq_paciente_dni` | `pacientes` | `dni` único, admite múltiples `NULL`. |
| Índice único | `uq_obra_social_nombre` | `obras_sociales` | `nombre` único. |
| Índice único | `uq_prestacion_codigo` | `prestaciones` | `codigo_nomenclador` único. |
| Índice único | `uq_arancel_os_prestacion` | `aranceles_obra_social` | `(obra_social_id, prestacion_id)` único. |
| Índice único | `uq_historia_paciente` | `historias_clinicas` | `paciente_id` único (1:1). |
| Índice único | `uq_historia_numero_ficha` | `historias_clinicas` | `numero_ficha` único. |
| Índice único | `uq_antecedentes_historia_clinica` | `antecedentes_salud` | `historia_clinica_id` único (1:1). |
| Índice único | `uq_odontograma_historia` | `odontogramas` | `historia_clinica_id` único (1:1). |
| Índice único | `uq_odontograma_diente` | `odontograma_dientes` | `(odontograma_id, diente)` único. |
| Índice único | `uq_movimiento_caja_turno` | `movimientos_caja` | `turno_id` único (1:1). |
| Índice único | `uq_opcion_menu_numero` | `opciones_menu_whatsapp` | `numero` único. |
| Índice único parcial | `uq_turno_doctor_fecha_inicio` | `turnos` | `(doctor_id, fecha_hora_inicio)` donde `estado_turno != 'CANCELADO'`. |
| `CHECK` | `ck_turno_fin_posterior_inicio` | `turnos` | `fecha_hora_fin > fecha_hora_inicio`. |
| `CHECK` | `ck_disponibilidad_dia_semana_valido` | `disponibilidad_doctores` | `dia_semana BETWEEN 0 AND 6`. |
| `CHECK` | `ck_disponibilidad_fin_posterior_inicio` | `disponibilidad_doctores` | `hora_fin > hora_inicio`. |
| `CHECK` | `ck_especifica_fin_posterior_inicio` | `disponibilidad_especifica` | `hora_fin > hora_inicio`. |
| `CHECK` | `ck_especifica_tipo_valido` | `disponibilidad_especifica` | `tipo IN ('HABILITADO','BLOQUEADO')`. |

#### Reglas que sólo viven en la aplicación

- Valores válidos de `EstadoTurno`, `EstadoWhatsApp`, `TipoPagoDoctor`,
  `EstadoPagoMovimiento` y `TipoOpcionMenu` (columnas `VARCHAR` sin `CHECK`).
- Solapamiento **parcial** de turnos del mismo doctor y solapamiento del paciente
  con otro profesional.
- Solapamiento de rangos semanales o de excepciones del mismo tipo.
- Coherencia `tipo_pago`/`valor_pago`.
- DNI: formato y normalización; la base sólo aplica unicidad.
- Inmutabilidad de evoluciones.
- Un turno cancelado no se factura y un turno se factura una sola vez (la
  unicidad del movimiento sí está en base).
- Una práctica inactiva no se factura.
- Un paciente sin cobertura paga `valor_base`; con cobertura y sin arancel
  responde 422 salvo `permitir_particular`.
- Importes no negativos (schemas Pydantic).

#### Advertencia importante sobre SQLite y las claves foráneas

El proyecto **no ejecuta** `PRAGMA foreign_keys = ON` en las conexiones SQLite.
SQLite tiene las claves foráneas desactivadas por defecto; por lo tanto, en
desarrollo local:

- las FK existen en el DDL, pero **no se aplican** las acciones `CASCADE`,
  `RESTRICT` ni `SET NULL`;
- los `CHECK` y los índices únicos **sí** se aplican;
- un borrado físico directo puede dejar filas huérfanas;
- `passive_deletes=True` del ORM asume que la base borra en cascada, pero SQLite
  no lo hará sin el `PRAGMA`.

En PostgreSQL (producción recomendada) las FK sí se aplican. Si se necesita
paridad local, agregar un event listener de SQLAlchemy que ejecute
`PRAGMA foreign_keys=ON` al abrir cada conexión SQLite.

#### Concurrencia y transacciones

- Las condiciones de carrera de unicidad se resuelven con índices únicos y
  `IntegrityError` traducido a 409.
- La creación concurrente de historia clínica se resuelve releyendo la fila
  creada por la otra transacción.
- La creación concurrente del menú por defecto se resuelve con el índice único
  y rollback silencioso.
- No hay bloqueo optimista (`version`) ni aislamiento explícito por operación.
- Un turno puede cancelarse y volver a crearse en el mismo inicio; el índice
  parcial lo permite porque excluye `CANCELADO`.

### 6.7. Estados y ciclos de vida

#### Turno (`turnos.estado_turno`)

| Valor | Significado | Efecto en agenda |
|---|---|---|
| `PENDIENTE` | Cita creada, aún no confirmada. | Ocupa el horario. |
| `CONFIRMADO` | Paciente confirmó o se confirmó desde el panel. | Ocupa el horario y se pinta verde. |
| `CANCELADO` | Cita anulada; no se borra. | **Libera** el horario y queda fuera del índice único parcial. |

No hay una máquina de estados formal: `PATCH /turnos/{id}` puede asignar
cualquiera de los tres valores; el webhook asigna confirmado/cancelado. Las
reglas de negocio (por ejemplo, no facturar cancelados) se validan en los
servicios.

#### Notificación (`turnos.estado_whatsapp`)

| Valor | Significado |
|---|---|
| `PENDIENTE_ENVIO` | Todavía no se envió el recordatorio. |
| `ENVIADO` | Se envió (real o simulado) y se guardó el `wamid`. |
| `FALLIDO` | El envío falló. |
| `RESPONDIDO` | El paciente respondió con un botón. |

El enlace Click-to-Chat no envía por API: cuando la secretaría lo abre, el panel
hace `PATCH` y pasa a `ENVIADO`.

#### Cobro (`movimientos_caja.estado_pago`)

| Valor | Significado |
|---|---|
| `PENDIENTE` | La OS todavía no pagó; el movimiento no fue presentado. |
| `EN_PROCESO_LIQUIDACION` | Se incluyó en una presentación del período. |
| `COBRADO` | La OS acreditó el importe (total o parcial). |
| `DEBITADO_OS` | La OS rechazó o debitó el importe; puede ser parcial. |

#### Disponibilidad específica (`disponibilidad_especifica.tipo`)

| Valor | Efecto |
|---|---|
| `HABILITADO` | Agrega atención en esa fecha. |
| `BLOQUEADO` | Recorta atención en esa fecha. |

#### Pago del profesional (`doctores.tipo_pago`)

| Valor | Cálculo |
|---|---|
| `DUENO` | `valor_pago` % de la producción; si es `NULL`, 100 %. |
| `COMISION` | `valor_pago` % obligatorio (0-100). |
| `SUELDO` | `valor_pago` fijo mensual, mayor que 0. |

#### Menú del bot (`opciones_menu_whatsapp.tipo`)

`TEXTO` (respuesta manual), `PRECIO` (tarifa de consulta), `OBRAS_SOCIALES`
(coberturas activas), `UBICACION` (dirección/horarios del `.env`) y
`SECRETARIA` (deriva a atención manual, sin respuesta automática).

### 6.8. Fechas, dinero y diferencias entre motores

#### Fechas

- `UTCDateTime` declara `DateTime(timezone=True)`.
- En PostgreSQL la columna es `TIMESTAMP WITH TIME ZONE`; al leer, se convierte a
  UTC y se quita `tzinfo`.
- En SQLite se guarda como texto `DATETIME` sin zona.
- Las comparaciones de rango se hacen con `rango_del_dia()` (00:00:00 a
  23:59:59.999999).
- El frontend interpreta las fechas como hora local del consultorio y envía
  `datetime` sin zona; el backend las normaliza a UTC naive.
- No se almacena la zona horaria del consultorio ni se hacen conversiones de
  horario de verano.

#### Dinero

- Columnas `FLOAT`, no `DECIMAL`.
- `redondear_monto`, `sumar_montos`, `multiplicar_monto` y `porcentaje_de` usan
  `Decimal(str(valor))` y `ROUND_HALF_UP` a 2 decimales.
- Los importes se guardan ya redondeados por los servicios; una escritura SQL
  directa puede introducir más decimales.
- Moneda implícita: pesos argentinos (ARS).
- `monto_total` no se persiste; se calcula en los DTO.
- `detalles_movimiento_caja` congela importes para que la factura histórica no
  cambie con el catálogo.

#### Nulos

- `NULL` en textos opcionales significa “no informado”, no cadena vacía. Los
  schemas convierten `""`/espacios a `NULL`.
- `NULL` en `monto_cobrado_os` significa “la OS todavía no pagó”.
- `NULL` en `dni` es un paciente sin documento y no colisiona con otros `NULL`.
- `NULL` en `valor_pago` tiene semántica distinta según `tipo_pago`.

### 6.9. Índices y consultas críticas

| Consulta / caso de uso | Tabla(s) | Índice(s) disponible(s) | Observación |
|---|---|---|---|
| Turnos de un doctor en un rango | `turnos` | `ix_turnos_doctor_id`, `ix_turnos_fecha_hora_inicio` | No hay índice compuesto `(doctor_id, fecha_hora_inicio)`; a gran volumen conviene agregarlo. |
| Turnos de un paciente | `turnos` | `ix_turnos_paciente_id` | Frecuente en historia y vista móvil. |
| Buscar turno por wamid | `turnos` | `ix_turnos_whatsapp_mensaje_id` | Clave del webhook de botones. |
| Evitar duplicado exacto de turno | `turnos` | `uq_turno_doctor_fecha_inicio` (parcial) | Excluye cancelados. |
| Agenda semanal por doctor/día | `disponibilidad_doctores` | `ix_disponibilidad_doctor_dia` | Cubre `(doctor_id, dia_semana)`. |
| Excepciones por doctor/fecha | `disponibilidad_especifica` | `ix_especifica_doctor_fecha` | Cubre `(doctor_id, fecha)`. |
| Línea de tiempo clínica | `evoluciones_medicas` | `ix_evolucion_historia_fecha` | Cubre `(historia_clinica_id, fecha_atencion)`. |
| Ficha por paciente | `historias_clinicas` | `uq_historia_paciente` | 1:1. |
| Declaración por ficha | `antecedentes_salud` | `uq_antecedentes_historia_clinica` | 1:1. |
| Odontograma por ficha | `odontogramas` | `uq_odontograma_historia` | 1:1. |
| Dientes de un odontograma | `odontograma_dientes` | `ix_odontograma_dientes_odontograma_id`, `uq_odontograma_diente` | Lista ordenada y unicidad por diente. |
| Caja por fecha | `movimientos_caja` | `ix_movimiento_caja_fecha` | Puede existir un índice legacy duplicado. |
| Caja por doctor | `movimientos_caja` | `ix_movimientos_caja_doctor_id` | Reportes de liquidación. |
| Un cierre por turno | `movimientos_caja` | `uq_movimiento_caja_turno` | Unicidad. |
| Detalle por movimiento | `detalles_movimiento_caja` | `ix_detalle_movimiento` | Factura/presentación. |
| Arancel de un par OS/práctica | `aranceles_obra_social` | `uq_arancel_os_prestacion` | Búsqueda y unicidad. |
| Buscar paciente por nombre | `pacientes` | — | `lower/replace` sobre `nombre`; escaneo secuencial. A gran volumen: índice de expresión o `pg_trgm` en PostgreSQL. |
| Buscar paciente por DNI | `pacientes` | `uq_paciente_dni` | Coincidencia exacta; la búsqueda parcial usa `LIKE` y puede escanear. |
| Pacientes por teléfono | `pacientes` | `ix_pacientes_telefono` | Filtro exacto. |
| Prácticas por código | `prestaciones` | `uq_prestacion_codigo` | Búsqueda/unicidad. |
| Coberturas por nombre | `obras_sociales` | `uq_obra_social_nombre` | Exacta; la búsqueda parcial escanea. |
| Menú por tecla | `opciones_menu_whatsapp` | `uq_opcion_menu_numero` | Un dígito por opción. |

**Índices duplicados/legacy detectados:** en una base SQLite ya migrada pueden
coexistir `ix_movimiento_caja_fecha` e `ix_movimientos_caja_fecha` sobre la misma
columna; el modelo sólo declara el primero. También hay índices `ix_*_id` sobre
la PK que son redundantes. No afectan la corrección, pero conviene limpiarlos en
una migración controlada.

### 6.10. Migraciones y evolución del esquema

**No se usa Alembic.** El esquema se sostiene con tres mecanismos:

1. `Base.metadata.create_all(bind=engine)` al arrancar:
   - crea tablas nuevas con todos sus índices y constraints;
   - **no** agrega columnas, índices ni constraints a tablas existentes;
   - **no** renombra ni borra nada.
2. `aplicar_columnas_faltantes(engine)`:
   - agrega las columnas aditivas listadas en `COLUMNAS_NUEVAS` (pago del
     profesional, DNI/cobertura del paciente, `wamid` de WhatsApp y las ~91
     columnas de la ficha médica ampliada);
   - es aditivo e idempotente;
   - en PostgreSQL usa `ADD COLUMN IF NOT EXISTS`;
   - en SQLite ignora el error `duplicate column name`.
3. Scripts `scripts/migrar_*.py`:
   - resuelven casos legacy concretos (pacientes, obra social, historia,
     facturación, disponibilidad específica, índice parcial de turnos);
   - `migrar_sqlite_a_postgres.py` aplica además `aplicar_columnas_faltantes`
     sobre el **origen** antes de copiar, para que una base local vieja no falle
     con «no such column»;
   - suelen guardar copia `.db.bak-*` y verificar conteos;
   - no forman una cadena versionada ni tienen `down`.

**Consecuencias:**

- Agregar una tabla nueva suele ser suficiente con `create_all`, pero en
  producción conviene crear la tabla antes del despliegue para evitar carreras
  entre instancias.
- Agregar un índice o un `UNIQUE` a una tabla existente **no** lo hace
  `create_all`: hay que escribir el `CREATE INDEX` en un script o migración.
- Cambiar el tipo o el `NOT NULL` de una columna existente requiere una
  migración manual (SQLite suele necesitar reconstrucción de tabla).
- Renombrar o eliminar columnas no está soportado por el mecanismo actual.
- No hay tabla `alembic_version` ni historial de migraciones aplicadas.

### 6.11. Copias de seguridad y restauración

- **Local:** el archivo `odontologico.db` es la base completa; los scripts de
  migración generan copias `odontologico.db.bak-<fecha>`. No se versiona en Git.
- **Producción:** la copia debe hacerla el proveedor PostgreSQL (Neon u otro):
  backups automáticos, point-in-time recovery o `pg_dump`.
- Antes de una migración destructiva o de tipo:
  1. detener la aplicación;
  2. hacer backup verificado;
  3. ejecutar la migración en una copia;
  4. validar conteos y constraints;
  5. aplicar en producción;
  6. verificar `/health`, `/`, `/medico` y un flujo de lectura.
- Para restaurar SQLite: reemplazar el archivo con la app detenida y verificar
  `PRAGMA integrity_check`.
- Para PostgreSQL: restaurar el dump en una base nueva y apuntar
  `DATABASE_URL` a ella; no mezclar filas de dos backups.
- La información es **sensible** (datos clínicos y de salud). Los backups deben
  cifrarse y tener acceso restringido.

---

## 7. Guía para futuras modificaciones

### 7.1. Reglas de oro

1. **No editar la base a mano en producción.** Todo cambio de esquema debe estar
   en un modelo y en una migración reproducible.
2. **No confiar en `create_all` para columnas nuevas.** Sólo crea tablas
   faltantes; para columnas de tablas existentes hay que usar
   `COLUMNAS_NUEVAS` o un script.
3. **Mantener las rutas delgadas.** Validación de contrato en `schemas`, reglas en
   `services`, persistencia en `models`.
4. **No romper contratos HTTP.** Agregar campos es compatible; quitar o renombrar
   requiere deprecación y una transición.
5. **Toda fecha nueva debe ser UTC naive** con `UTCDateTime` y `utcnow`.
6. **Todo importe nuevo debe pasar por `app/core/money.py`.**
7. **Toda tabla clínica nueva debe evaluar inmutabilidad y privacidad.**
8. **Toda modificación debe tener test y actualización de este documento.**
9. **No commitear secretos ni bases.** `.env`, `*.db`, tokens y backups están
   fuera de Git.
10. **El deploy no debe depender de un cambio destructivo inmediato.** Aplicar
    primero esquemas compatibles, luego desplegar código y recién después
    limpiar.

### 7.2. Agregar una funcionalidad o endpoint

**Orden recomendado:**

1. **Definir el contrato** en `app/schemas/<dominio>.py`:
   - `Create`, `Update`, `Read` según corresponda;
   - validadores de normalización;
   - `ConfigDict(from_attributes=True)` para leer ORM.
2. **Definir la regla** en `app/services/<dominio>_service.py`:
   - una función pública por caso de uso;
   - lanzar `ValidacionError`, `RecursoNoEncontradoError` o `ConflictoError`;
   - hacer `commit()` y `refresh()` cuando corresponda;
   - no devolver `HTTPException` ni objetos de FastAPI.
3. **Exponer la ruta** en `app/api/v1/endpoints/<dominio>.py`:
   - `response_model`, `status_code`, `summary` y `description`;
   - `db: Session = Depends(get_db)`;
   - delegar en el servicio.
4. **Registrar el router** en `app/api/v1/router.py` si es un archivo nuevo.
5. **Actualizar el frontend**:
   - `app.js` para el panel;
   - `medico.js` si aplica a la vista móvil;
   - `anamnesis.js` sólo si es una ruta pública.
6. **Agregar tests** de contrato, reglas y caso borde.
7. **Actualizar `DOCUMENTACION.md`** (endpoint, tabla, estado, flujo).

**Esqueleto de endpoint:**

```python
@router.post(
    "",
    response_model=MiRecursoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear mi recurso",
)
def crear_mi_recurso(
    payload: MiRecursoCreate,
    db: Session = Depends(get_db),
) -> MiRecurso:
    return mi_recurso_service.crear(db, payload)
```

**Esqueleto de servicio:**

```python
def crear(db: Session, data: MiRecursoCreate) -> MiRecurso:
    entidad = MiRecurso(**data.model_dump())
    db.add(entidad)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictoError("Ya existe un registro con esos datos.") from exc
    db.refresh(entidad)
    return entidad
```

**Checklist de un endpoint nuevo:**

- [ ] schema de entrada y salida;
- [ ] regla en servicio;
- [ ] ruta con `response_model`;
- [ ] router incluido;
- [ ] error de negocio mapeado a 4xx;
- [ ] frontend conectado o justificación de por qué no;
- [ ] test de éxito, 404, 409/422;
- [ ] documentación actualizada.

### 7.3. Agregar una tabla nueva

1. Crear `app/models/mi_tabla.py`:

```python
class MiTabla(Base):
    __tablename__ = "mi_tabla"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    activo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
```

2. Importarla en **dos** lugares:
   - `app/models/__init__.py` (para los imports públicos);
   - `app/db/base.py` (para que `Base.metadata` la conozca).
3. Crear sus schemas en `app/schemas/`.
4. Crear su servicio en `app/services/`.
5. Crear su endpoint en `app/api/v1/endpoints/` e incluirlo en el router.
6. Agregar tests.
7. Evaluar si necesita seed (`scripts/seed.py`) o datos iniciales.
8. Actualizar `DOCUMENTACION.md` (inventario, diccionario, relaciones).

**Migración:**

- Si la tabla es nueva y no tiene dependencias problemáticas, `create_all` la
  crea al arrancar.
- Si la tabla debe existir antes de desplegar el código que la usa, crear un
  script/migración con `CREATE TABLE IF NOT EXISTS` y sus índices.
- Si la tabla tiene datos iniciales obligatorios, sembrarlos con un script
  idempotente (`insert if not exists`), no asumir que `create_all` lo hace.
- Si la tabla es grande o tendrá backfill, no ejecutar el backfill en `lifespan`:
  usar un script separado con transacción y verificación.

### 7.4. Agregar una columna a una tabla existente

**Caso simple (columna opcional):**

1. Agregar el campo al modelo:

```python
telefono_alternativo: Mapped[Optional[str]] = mapped_column(
    String(30), nullable=True
)
```

2. Agregar la entrada en `app/db/migraciones.py`:

```python
ColumnaNueva(
    "pacientes",
    "telefono_alternativo",
    "VARCHAR(30)",
    "VARCHAR(30)",
),
```

3. Actualizar el schema Pydantic (`PacienteDatosAdministrativos`, `Read`).
4. Actualizar el servicio si debe normalizar o validar.
5. Agregar tests.
6. Actualizar este documento.

**Caso con `NOT NULL` y datos existentes (tres fases):**

1. Agregar la columna **nullable** (o con `DEFAULT`) y desplegar.
2. Ejecutar un backfill idempotente por lotes:
   ```sql
   UPDATE mi_tabla SET nueva_columna = <valor> WHERE nueva_columna IS NULL;
   ```
3. Recién entonces agregar `NOT NULL` (migración específica) y, si corresponde,
   el índice/constraint.

No agregar `NOT NULL` sin default en una tabla con filas: el `ALTER TABLE`
fallará o dejará la tabla bloqueada.

**Reglas:**

- En PostgreSQL usar `ADD COLUMN IF NOT EXISTS`.
- En SQLite no existe `IF NOT EXISTS` para columnas; el código tolera
  `duplicate column name`.
- Si la columna se usa en un índice, crear el índice en el mismo release o antes.
- Si la columna es obligatoria para la lógica nueva, el código debe tolerar
  `NULL` durante la ventana de transición.
- Actualizar también los scripts legacy si la base puede tener un esquema muy
  antiguo.

### 7.5. Cambiar el tipo, renombrar o eliminar una columna

**Regla general: expandir → migrar → contraer.** Nunca hacer los tres pasos en un
solo despliegue.

#### Cambiar el tipo

1. Crear una columna nueva con el tipo deseado.
2. Copiar y transformar los datos con un script verificable.
3. Desplegar código que escriba en ambas columnas (dual write) o que lea la
   nueva con fallback.
4. Backfill de lo que haya quedado.
5. Cambiar la lectura a la nueva columna.
6. Dejar de escribir la vieja.
7. Eliminarla en un release posterior.

En SQLite un cambio de tipo suele requerir reconstruir la tabla (crear nueva,
copiar, borrar, renombrar) y volver a crear índices. Hacerlo con la app detenida
y backup previo.

#### Renombrar

- No renombrar de golpe: agregar la nueva, copiar, exponer la nueva en la API
  (con alias Pydantic si hace falta), migrar clientes y luego eliminar la vieja.
- Mantener el nombre viejo en la API durante al menos un release si hay clientes
  externos.

#### Eliminar

- Confirmar que no hay lecturas/escrituras en el código.
- Hacer backup.
- En PostgreSQL, `DROP COLUMN`; en SQLite, reconstrucción de tabla.
- Si la columna tiene un índice o constraint asociado, borrarlo primero.
- No eliminar columnas de las que dependan vistas, scripts o reportes.

#### Cambiar un `CHECK` o un enum de base

- Cambiar un `CHECK` en SQLite requiere reconstrucción de tabla.
- Si el enum se guarda como `VARCHAR` sin `CHECK` (caso actual), agregar un valor
  sólo requiere actualizar el enum Python, los schemas y la UI.

### 7.6. Agregar índices, `UNIQUE` o claves foráneas

#### Índice simple

```sql
CREATE INDEX IF NOT EXISTS ix_mi_tabla_mi_columna
ON mi_tabla (mi_columna);
```

- En el modelo, declarar `index=True` para que `create_all` lo cree en bases
  nuevas.
- En bases existentes, crear el índice con un script o migración; `create_all`
  **no** lo agrega.
- Usar nombres estables (`ix_<tabla>_<columna>`), no autogenerados.

#### Índice único

1. Detectar duplicados:
   ```sql
   SELECT columna, COUNT(*) FROM mi_tabla GROUP BY columna HAVING COUNT(*) > 1;
   ```
2. Resolverlos antes de crear el índice.
3. Crear el índice único con nombre (`uq_...`).
4. Si la columna admite `NULL`, recordar que los `NULL` no colisionan.
5. Si la unicidad es case-insensitive, crear un índice funcional:
   ```sql
   CREATE UNIQUE INDEX uq_mi_tabla_lower ON mi_tabla (LOWER(columna));
   ```
   SQLite y PostgreSQL lo soportan; el servicio también debe normalizar.

#### Índice parcial

```sql
CREATE UNIQUE INDEX uq_mi_tabla_vigente
ON mi_tabla (clave)
WHERE estado != 'CANCELADO';
```

Es el patrón de `uq_turno_doctor_fecha_inicio`. Sirve para excluir filas
“borradas” o estados que no deben bloquear.

#### Clave foránea

- Elegir `ON DELETE` según el significado:
  - `CASCADE`: el hijo no existe sin el padre (detalle, archivo, agenda).
  - `RESTRICT`: el padre no debe borrarse si hay hijos (evolución, caja).
  - `SET NULL`: el vínculo es opcional (paciente → obra social).
- En PostgreSQL se puede agregar con `ALTER TABLE ... ADD CONSTRAINT`.
- En SQLite, agregar una FK a una tabla existente requiere reconstruir la tabla.
- Recordar que SQLite no aplica FKs sin `PRAGMA foreign_keys=ON`.
- Indexar la columna FK explícitamente; PostgreSQL no crea ese índice solo.

### 7.7. Agregar o cambiar un estado/enumeración

1. Agregar el valor en `app/models/enums.py`.
2. Actualizar los schemas que lo aceptan (Pydantic valida el enum).
3. Actualizar las reglas del servicio (`if/elif`, transiciones, cálculos).
4. Actualizar las etiquetas y colores de:
   - `app.js`;
   - `medico.js`;
   - `reporte_pdf_service.py` si el estado se imprime;
   - `README.md`/`DOCUMENTACION.md`.
5. Agregar tests de contrato y de lógica.
6. **No** hace falta migrar la columna si es `VARCHAR` sin `CHECK`; sí hace falta
   si se decide agregar un `CHECK` o un tipo enum nativo.
7. Si se elimina un valor, primero migrar las filas existentes a otro valor y
   verificar que ningún cliente lo envíe.

**Transiciones recomendadas para turnos:**

```
PENDIENTE → CONFIRMADO
PENDIENTE → CANCELADO
CONFIRMADO → CANCELADO
CANCELADO → PENDIENTE (reactivación explícita)
```

El backend actual no impide otras asignaciones por `PATCH`; si se quiere una
máquina de estados estricta, implementarla en `turno_service` y cubrirla con
tests antes de documentarla como regla.

### 7.8. Modificar el frontend

- **No hay build ni npm.** Los archivos se sirven tal cual desde `app/static`.
- Usar el cliente existente `api()` de `app.js` o el `pedir()` de cada módulo;
  no repetir `fetch` con manejo de errores distinto.
- Escapar todo dato dinámico:
  - en panel/móvil, `esc()` antes de interpolar en `innerHTML`;
  - en anamnesis, `textContent` para datos del paciente.
- No insertar HTML del backend sin sanitizar.
- Mantener las fechas como `YYYY-MM-DD` y `HH:MM` en la UI; el backend espera
  `datetime` sin zona.
- Si se agrega una vista nueva:
  1. agregar el contenedor en `index.html`;
  2. agregar la entrada de navegación;
  3. implementar `render...()` y `cargar...()` en `app.js`;
  4. registrar eventos por delegación;
  5. actualizar `window.OdontoApp` si `calendar.js` necesita el estado;
  6. agregar estilos en `styles.css`;
  7. probar en `/medico` si el flujo es compartido.
- Para la vista móvil, recordar que `medico.js` es autónomo: no importa
  `app.js`. Si se comparte lógica, crear un módulo común explícito en lugar de
  duplicar.
- Evitar `onclick="..."` inline; el proyecto usa delegación de eventos por
  `data-*`.
- Probar en ancho móvil y con teclado; la vista móvil usa objetivos táctiles.

### 7.9. Agregar pruebas

**Patrón del proyecto:**

- `tests/conftest.py` provee `engine`, `db_session`, `client`, `doctor` y
  `paciente`.
- `client` usa `TestClient` sin `lifespan`, para no tocar la base real.
- `get_db` se sobreescribe con la sesión de prueba.
- WhatsApp se fuerza a simulación con `whatsapp_sin_credenciales`.
- No se debe llamar a la red: usar `monkeypatch`/fakes, como en
  `test_publicar.py` y `test_replicar_datos_api.py`.

**Casos mínimos para una funcionalidad nueva:**

1. camino feliz (201/200);
2. recurso inexistente (404);
3. conflicto de unicidad o solapamiento (409);
4. validación (422);
5. regla de negocio específica;
6. efecto en base (consultar la fila);
7. contrato de método no permitido, si aplica;
8. migración, si cambia el esquema.

**Ejemplo de test de contrato:**

```python
def test_crear_mi_recurso(client):
    respuesta = client.post("/api/v1/mi-recurso", json={"nombre": "Demo"})
    assert respuesta.status_code == 201
    assert respuesta.json()["nombre"] == "Demo"
```

**Comandos:**

```powershell
python -m pytest
python -m pytest tests/test_mi_recurso.py -q
python -m pytest --collect-only
```

### 7.10. Despliegue y migraciones en producción

**Antes de desplegar:**

1. Ejecutar la suite completa y confirmar 0 fallos.
2. Revisar que la migración sea **compatible hacia atrás** (el código viejo debe
   seguir funcionando mientras conviven instancias).
3. Si se agrega una columna, que sea nullable o con default.
4. Si se agrega un índice en una tabla grande, crearlo fuera de la ventana de
   arranque (`CREATE INDEX CONCURRENTLY` en PostgreSQL, si aplica).
5. Hacer backup de producción.
6. Probar la migración en una copia.
7. Desplegar y verificar `/health`, `/`, `/medico` y un flujo de lectura/escritura.

**Orden recomendado para cambios compatibles:**

```
1. Migración aditiva (columna/tabla/índice)
2. Deploy del código nuevo
3. Backfill (si hace falta)
4. Validación
5. Limpieza destructiva en un release posterior
```

**Vercel y concurrencia:**

- Varias instancias pueden ejecutar `init_db()` a la vez.
- PostgreSQL usa `ADD COLUMN IF NOT EXISTS` para tolerar la carrera.
- No poner backfills largos en `lifespan`: cada arranque los repetiría y la
  Function tiene límite de tiempo.
- Un deploy no debe depender de que el esquema se cree en el primer request: si
  la tabla es crítica, crearla en una migración previa.
- Si se cambia una variable de entorno, redesplegar; Vercel no siempre reinicia
  las Functions existentes.

**Rollback:**

- El rollback de código es inmediato con Vercel, pero **no revierte la base**.
- Por eso toda migración debe ser compatible con la versión anterior.
- Si el cambio es destructivo, el rollback exige restaurar el backup.
- Guardar el commit y la URL del deploy anterior.

**Flujo automatizado del proyecto:**

```powershell
python -m scripts.publicar
```

Hace, en orden: pytest → commit → push a GitHub → `vercel --prod --yes` →
verificación de `/health`, `/` y `/medico`. Si falla un test, no toca Git ni
Vercel.

### 7.11. Seguridad y privacidad

> El sistema maneja datos personales y clínicos. Antes de exponerlo a Internet
> con datos reales, resolver como mínimo los puntos **P0**.

| Prioridad | Tema | Situación actual | Acción recomendada |
|---|---|---|---|
| **P0** | Autenticación | No hay login ni token de API en el panel/API. | Agregar autenticación (JWT/sesión) y proteger todos los routers salvo `/health`, `/anamnesis/{token}` y estáticos públicos. |
| **P0** | Autorización | Cualquiera puede listar pacientes, historias y caja. | Roles: secretaría, odontólogo, administrador; filtros por profesional. |
| **P0** | Firma del webhook | `POST /whatsapp/webhook` no valida `X-Hub-Signature-256`. | Verificar HMAC del body con el secreto de la app de Meta antes de procesar. |
| **P1** | CORS | `CORS_ORIGINS=["*"]` por defecto. | Restringir a los dominios reales; no usar `*` con credenciales. |
| **P1** | Secretos | `WHATSAPP_*`, `ANAMNESIS_TOKEN_SECRET`, `DATABASE_URL` en entorno. | Mantener en Vercel/GitHub Secrets; rotar; nunca commitear `.env`. |
| **P1** | Enlaces de anamnesis | Token determinístico sin vencimiento. | Evaluar expiración/rotación; mientras el turno exista, el enlace sigue válido. |
| **P1** | Rate limiting | Sin límites por IP/teléfono. | Limitar webhook, anamnesis y login para evitar abuso/fuerza bruta. |
| **P1** | Auditoría | No se registra quién modificó pacientes, historias o caja. | Tabla de auditoría y usuario autenticado. |
| **P1** | Logs | Algunos logs incluyen teléfono y texto del paciente. | Minimizar PII, redactar teléfonos/textos clínicos y definir retención. |
| **P2** | Cabeceras | Sólo se agregan headers en la página de anamnesis. | CSP, `X-Content-Type-Options`, `X-Frame-Options`, HSTS en producción. |
| **P2** | Archivos | `url_archivo` es una URL arbitraria. | Validar esquema/host, no permitir `file://`, considerar URLs firmadas. |
| **P2** | Dependencias | Versiones fijas, sin escaneo automatizado. | `pip-audit`, Dependabot y revisión periódica. |
| **P2** | Backups | Dependen del proveedor. | Verificar restore y cifrado de backups. |

**Buenas prácticas ya presentes:**

- consultas parametrizadas por SQLAlchemy (sin SQL concatenado con input);
- token HMAC con `compare_digest`;
- `Referrer-Policy: no-referrer` y `no-store` en la ficha pública;
- DNI/telefono normalizados;
- errores de dominio sin filtrar stack traces al cliente;
- `.env`, bases y tokens fuera de Git.

**Rotación de `ANAMNESIS_TOKEN_SECRET`:** invalida todos los enlaces emitidos
antes de la rotación. Si se rota, reenviar los enlaces desde el panel.

### 7.12. Checklist de “definición de terminado”

- [ ] El cambio tiene modelo/schema/servicio/ruta coherentes.
- [ ] No se modificó el contrato existente sin deprecación.
- [ ] Las fechas son UTC naive.
- [ ] Los importes usan `money.py`.
- [ ] La migración es compatible y está probada en SQLite y PostgreSQL.
- [ ] Se actualizaron `COLUMNAS_NUEVAS`/scripts si hacía falta.
- [ ] Se agregaron tests de éxito y error.
- [ ] La suite completa pasa.
- [ ] El frontend escapa datos.
- [ ] Se actualizó `DOCUMENTACION.md`.
- [ ] Se verificó `/health`, `/`, `/medico` en producción.

**Anti-patrones a evitar:**

- Poner lógica de negocio en el endpoint.
- Hacer `commit()` por cada fila dentro de un bucle sin transacción.
- Usar `create_all` como única estrategia de migración.
- Agregar una FK sin índice.
- Suponer que SQLite aplica `ON DELETE`.
- Guardar `datetime` con zona o comparar aware con naive.
- Sumar `float` directamente para dinero.
- Devolver entidades ORM sin `response_model`.
- Editar una evolución o borrarla desde un script.
- Duplicar la lógica de estados en el frontend sin actualizar todas las copias.
- Exponer la API administrativa sin autenticación.

---

## 8. Operación y despliegue

### 8.1. Puesta en marcha local

```powershell
cd c:\Sistema\Odontologico

# 1) Entorno virtual
py -m venv .venv
.\.venv\Scripts\activate

# 2) Dependencias
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 3) Variables de entorno
copy .env.example .env
# editar .env con DATABASE_URL, secretos y datos del consultorio

# 4) Base de ejemplo (opcional)
python -m scripts.seed

# 5) Servidor
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**URLs locales:**

- Panel: `http://127.0.0.1:8000/`
- Vista móvil: `http://127.0.0.1:8000/medico`
- Swagger: `http://127.0.0.1:8000/docs`
- Salud: `http://127.0.0.1:8000/health`
- Ficha pública: `http://127.0.0.1:8000/anamnesis/<token>`

Para acceder desde el celular en la misma Wi-Fi, usar la IP de la PC y
`--host 0.0.0.0`, y permitir el puerto 8000 en el firewall.

### 8.2. Variables de entorno

| Variable | Default / ejemplo | Uso |
|---|---|---|
| `APP_NAME` | `Franciosi Odontología API` | Título de la app y `/health`. |
| `APP_VERSION` | `1.0.0` | Versión mostrada. |
| `API_V1_PREFIX` | `/api/v1` | Prefijo de la API. |
| `DEBUG` | `false` | Logging SQL y nivel DEBUG. |
| `DATABASE_URL` | `sqlite:///./odontologico.db` | Conexión principal. |
| `DATABASE_URL_DESTINO` | vacío | Destino opcional de `migrar_sqlite_a_postgres`. |
| `CORS_ORIGINS` | `["*"]` | Orígenes permitidos; acepta lista JSON. |
| `CORS_ALLOW_CREDENTIALS` | `false` | Cookies/credenciales CORS. |
| `WHATSAPP_API_URL` | `https://graph.facebook.com/v21.0` | Base de la Graph API. |
| `WHATSAPP_PHONE_NUMBER_ID` | vacío | Phone Number ID de Meta (sólo dígitos). |
| `WHATSAPP_TOKEN` | vacío | Alias corto del access token. |
| `WHATSAPP_ACCESS_TOKEN` | vacío | Access token; tiene prioridad sobre `WHATSAPP_TOKEN`. |
| `WHATSAPP_VERIFY_TOKEN` | vacío | Verify token del webhook. |
| `WHATSAPP_CONSULTA_CODIGO` | `10100` | Código de consulta para la opción PRECIO. |
| `WHATSAPP_CONSULTORIO_DIRECCION` | `Av. Colón 1234, Córdoba Capital` | Dirección del bot. |
| `WHATSAPP_CONSULTORIO_HORARIOS` | `Lunes a viernes...` | Horarios del bot. |
| `RECORDATORIO_DIAS` | `3` | Antelación de recordatorios. |
| `WHATSAPP_CODIGO_PAIS` | `549` | Prefijo para enlaces `wa.me`. |
| `PUBLIC_BASE_URL` | vacío | Base absoluta de enlaces públicos. |
| `ANAMNESIS_TOKEN_SECRET` | vacío | Secreto HMAC de la ficha. |
| `VERCEL` / `VERCEL_URL` / `VERCEL_PROJECT_PRODUCTION_URL` | provistas por Vercel | Detección de entorno y URL pública. |

**Orden de prioridad del token de WhatsApp:**
`WHATSAPP_ACCESS_TOKEN` → `WHATSAPP_TOKEN`. Un valor vacío se ignora.

### 8.3. Seeds y datos de ejemplo

| Comando | Efecto |
|---|---|
| `python -m scripts.seed` | Datos demo completos; idempotente por claves naturales. |
| `python -m scripts.seed_nomenclador_cba` | Carga el nomenclador base; no duplica códigos. |
| `python -m scripts.seed_nomenclador_cba --actualizar` | Actualiza descripción/valor de códigos existentes. |
| `python -m scripts.seed_nomenclador_cba --dry-run` | Muestra sin escribir. |
| `python -m scripts.migrar_sqlite_a_postgres --destino ... --ejecutar` | Copia local a PostgreSQL. |
| `python -m scripts.replicar_datos_api --url ... --ejecutar` | Copia local a la app desplegada vía API. |

> `seed.py` **agrega** datos demo. No ejecutarlo sobre una base con datos reales
> sin revisar antes qué crea.

#### Limpieza de la base de producción (Vercel / Neon)

`limpiar_vercel.bat` (raíz del proyecto) vacía las tablas de la PostgreSQL que
usa Vercel, sin borrar el esquema. Vercel no almacena las tablas: la aplicación
se conecta a Neon mediante `DATABASE_URL`.

El CLI de Vercel marca `DATABASE_URL` como `Secret` y no la exporta
(`vercel env pull` escribe `[SENSITIVE]`). Por eso el `.bat` pide la cadena una
sola vez con entrada oculta y la guarda en `.env.vercel.local`, archivo ignorado
por git. También se puede crear a mano:

```dotenv
DATABASE_URL="postgresql://usuario:clave@host/db?sslmode=require"
```

Modos:

| Modo | Qué vacía | Qué conserva |
|---|---|---|
| `operativo` (recomendado) | `pacientes`, `turnos`, `historias_clinicas`, `evoluciones_medicas`, `antecedentes_salud`, `odontogramas`, `odontograma_dientes`, `archivos_adjuntos`, `movimientos_caja`, `detalles_movimiento_caja`, `disponibilidad_especifica` | `doctores`, `obras_sociales`, `prestaciones`, `aranceles_obra_social`, `disponibilidad_doctores` y `opciones_menu_whatsapp` |
| `todo` | Las 17 tablas | Nada; vuelve a sembrar las 4 opciones del menú del bot |

El `.bat` primero ejecuta un **simulacro** (conteos), después pide escribir `SI`
y recién entonces borra. En PostgreSQL usa `TRUNCATE ... RESTART IDENTITY` sin
`CASCADE` y todo ocurre en una transacción; si falla, no deja cambios. Para
probar sin borrar:

```powershell
.\limpiar_vercel.bat --simulacro
```

Equivalente por consola:

```powershell
python -m scripts.limpiar_base --env-file .env.vercel.local --modo operativo
python -m scripts.limpiar_base --env-file .env.vercel.local --modo operativo --ejecutar
```

> No apuntar `.env.vercel.local` a la base local. El script rechaza SQLite por
> defecto justamente para evitar limpiar `odontologico.db` por error.

### 8.4. Pruebas y calidad

```powershell
python -m pytest
python -m pytest --collect-only
python -m pytest tests/test_facturacion.py -q
```

- La suite no usa la base real.
- WhatsApp se simula.
- Los tests de deploy/publicación usan fakes y no tocan Git, Vercel ni la red.
- Resultado actual: **704 tests**.

### 8.5. Publicación

**Automática:**

```powershell
python -m scripts.publicar
```

o doble clic en `publicar.bat`.

**Por etapas:**

```powershell
python -m scripts.publicar --sin-deploy      # tests + commit + push
python -m scripts.publicar --solo-deploy     # sólo Vercel + verificación
python -m scripts.publicar --simulacro       # no escribe nada
```

**Requisitos:** Vercel CLI instalada y carpeta enlazada al proyecto; rama no
detached; remoto `origin`; tests en verde.

### 8.6. Particularidades de Vercel

- `vercel.json` sólo declara `framework: fastapi`; no hay `build` propio.
- `.vercelignore` excluye `tests/`, `scripts/`, `.venv`, bases, logs y `.env`.
- Sin `DATABASE_URL` PostgreSQL, la app usa SQLite en `/tmp` y **pierde datos**.
- El filesystem es de sólo lectura salvo `/tmp`.
- Las Functions pueden escalar horizontalmente: el código debe tolerar arranques
  concurrentes.
- `publicar.py` verifica `/health`, `/` y `/medico`; un 401/403 puede ser
  Deployment Protection de Vercel, no un fallo de la app.

### 8.7. Diagnóstico rápido

| Síntoma | Causa probable | Solución |
|---|---|---|
| `500` al listar/guardar doctores o pacientes | Falta una columna que `create_all` no agrega. | Ejecutar `init_db`/migración y revisar `COLUMNAS_NUEVAS`. |
| WhatsApp responde 400/404 | `WHATSAPP_PHONE_NUMBER_ID` es un teléfono, no el ID de Meta. | Copiar el Phone Number ID de API Setup. |
| Webhook no verifica | `WHATSAPP_VERIFY_TOKEN` vacío o distinto. | Cargar el mismo valor en Meta y en el entorno. |
| Datos desaparecen en Vercel | SQLite en `/tmp`. | Configurar PostgreSQL. |
| `409` al crear turno | Solape del doctor o duplicado exacto. | Elegir otro horario; cancelar el turno viejo libera el inicio. |
| `400` “paciente ya tiene turno” | Paciente citado con otro profesional en el mismo rango. | Cambiar horario o cancelar el turno existente. |
| `405` al editar/borrar evolución | Inmutabilidad clínica. | Crear una evolución nueva; no se edita la anterior. |
| Fechas corridas | Se envió una fecha con zona o se interpretó UTC como local. | Enviar `datetime` sin zona y tratar todo como UTC naive. |
| “No se pudo conectar” en el panel | Backend caído, URL incorrecta o CORS. | Verificar `/health`, consola y `CORS_ORIGINS`. |
| `401/403` en la URL de Vercel | Deployment Protection. | Autenticarse en Vercel o desactivar protección para el dominio. |
| La migración no agrega un índice | `create_all` no altera tablas existentes. | Crear el índice con un script. |

---

## 9. Riesgos, deuda técnica y roadmap

### 9.1. Matriz de riesgos

| Prioridad | Riesgo | Impacto | Mitigación recomendada |
|---|---|---|---|
| **P0** | API y panel sin autenticación | Cualquiera con la URL puede leer/editar datos clínicos y de caja. | Autenticación + RBAC antes de usar con datos reales en Internet. |
| **P0** | Webhook POST sin firma | Un tercero podría simular respuestas de pacientes. | Validar `X-Hub-Signature-256` con el App Secret de Meta. |
| **P1** | SQLite efímero en Vercel | Pérdida total de datos entre despliegues. | `DATABASE_URL` PostgreSQL y backups verificados. |
| **P1** | Migraciones sin versionado | Deriva de esquema, cambios no reproducibles, rollback difícil. | Adoptar Alembic; mantener scripts legacy sólo para datos. |
| **P1** | `Float` para dinero | Errores de centavos en sumas/reportes. | `Numeric(12,2)` o `Decimal` persistido; mantiene `money.py`. |
| **P1** | Sin auditoría | No se puede saber quién cambió una historia o un cobro. | Tabla `auditoria` + usuario autenticado + triggers/servicio. |
| **P1** | PII en logs | Teléfonos y textos de pacientes en stdout. | Redacción, retención y acceso restringido a logs. |
| **P1** | Sin rate limiting | Abuso del webhook, anamnesis o fuerza bruta futura. | Límites por IP/teléfono y backoff. |
| **P1** | Token de anamnesis sin expiración | Enlace válido mientras exista el turno. | Expiración explícita o versión de token por turno. |
| **P1** | CORS abierto | Cualquier origen puede llamar a la API desde un navegador. | Restringir dominios; no usar `*` con credenciales. |
| **P2** | `app.js` monolítico | Regresiones y difícil mantenimiento. | Separar por vistas/módulos sin cambiar la API. |
| **P2** | FK no aplicadas en SQLite | Huérfanos en desarrollo/pruebas. | `PRAGMA foreign_keys=ON` por conexión. |
| **P2** | Enums sin `CHECK` | Valores inválidos por SQL directo. | `CHECK` o enums nativos; validar en escritura. |
| **P2** | Índices duplicados/ausentes | Consultas lentas a gran volumen. | Limpiar duplicados; índices compuestos/expresiones. |
| **P2** | Sin scheduler | Recordatorios dependen de un cron externo no incluido. | Vercel Cron o worker con idempotencia. |
| **P2** | Sin outbox de WhatsApp | Un fallo entre envío y commit deja estados inconsistentes. | Patrón outbox + reintentos. |
| **P2** | Archivos sólo URL | No hay control de subida, permisos ni malware. | Almacenamiento gestionado + URLs firmadas + antivirus. |
| **P2** | Sin observabilidad | Diagnóstico sólo por logs. | Métricas, trazas, alertas y correlation id. |
| **P2** | Sin pruebas de carga | Límites desconocidos en Vercel/PostgreSQL. | Pruebas de carga y presupuesto de latencia. |

### 9.2. Deuda técnica priorizada

**Fase 1 — antes de producción con datos reales:**

1. autenticación y roles;
2. firma del webhook;
3. PostgreSQL persistente + backups;
4. CORS restringido;
5. revisión de logs/PII.

**Fase 2 — estabilidad y mantenibilidad:**

1. Alembic;
2. `Numeric` para importes;
3. auditoría;
4. `PRAGMA foreign_keys=ON` en SQLite;
5. tests de migración en PostgreSQL real (no sólo SQLite).

**Fase 3 — escala y producto:**

1. dividir `app.js`;
2. outbox/scheduler de WhatsApp;
3. índices de búsqueda;
4. almacenamiento de archivos;
5. métricas y trazas;
6. mejoras de accesibilidad y experiencia móvil.

### 9.3. Lo que **no** es deuda técnica

- La separación rutas/servicios/modelos es adecuada para el tamaño actual.
- La ausencia de microservicios no es un problema: el dominio es pequeño y la
  transacción entre agregados es valiosa.
- Los snapshots de facturación (`detalles_movimiento_caja`) son una decisión
  correcta de trazabilidad, no duplicación innecesaria.
- La inmutabilidad clínica está bien defendida en tres capas aplicativas.

---

## 10. Anexos

### 10.1. Glosario

| Término | Definición |
|---|---|
| **Turno** | Cita de un paciente con un profesional en un rango horario. |
| **Disponibilidad semanal** | Rangos de atención recurrentes por día de la semana. |
| **Excepción de agenda** | Tramo `HABILITADO`/`BLOQUEADO` para una fecha concreta. |
| **Paciente ubicuo** | Control que impide citar al mismo paciente en dos agendas a la vez. |
| **Historia clínica** | Ficha única y permanente de un paciente. |
| **Evolución médica** | Registro inmutable de una atención clínica. |
| **Anamnesis** | Ficha médica (Historia Clínica General) que completa el paciente y termina el profesional. |
| **Obra social** | Cobertura o prepaga que financia total o parcialmente una práctica. |
| **Prestación** | Práctica facturable (consulta, obturación, endodoncia, etc.). |
| **Arancel** | Acuerdo de cobertura y coseguro para una práctica y una obra social. |
| **Coseguro** | Parte del costo que paga el paciente en el mostrador. |
| **Convenio** | Grilla completa de aranceles de una cobertura. |
| **Presentación** | Documento/período que el consultorio envía a la obra social para cobrar. |
| **Débito** | Importe que la obra social rechaza o descuenta. |
| **Wamid** | Identificador del mensaje de WhatsApp; se usa para correlacionar respuestas. |
| **UTC naive** | Fecha/hora en UTC sin `tzinfo`, convención de todo el backend. |
| **Baja lógica** | Marcar `activo/activa = false` sin borrar la fila. |
| **Snapshot** | Copia congelada de datos (código, descripción, importes) al facturar. |
| **DTO** | Objeto de transferencia usado para la respuesta de la API. |

### 10.2. Consultas de diagnóstico

**Conteo de filas por tabla (SQLite/PostgreSQL):**

```sql
SELECT 'doctores' AS tabla, COUNT(*) AS filas FROM doctores
UNION ALL SELECT 'pacientes', COUNT(*) FROM pacientes
UNION ALL SELECT 'turnos', COUNT(*) FROM turnos
UNION ALL SELECT 'historias_clinicas', COUNT(*) FROM historias_clinicas
UNION ALL SELECT 'evoluciones_medicas', COUNT(*) FROM evoluciones_medicas
UNION ALL SELECT 'movimientos_caja', COUNT(*) FROM movimientos_caja
UNION ALL SELECT 'detalles_movimiento_caja', COUNT(*) FROM detalles_movimiento_caja;
```

**Duplicados de DNI (no debería devolver filas):**

```sql
SELECT dni, COUNT(*) FROM pacientes
WHERE dni IS NOT NULL
GROUP BY dni HAVING COUNT(*) > 1;
```

**Turnos huérfanos (si SQLite no aplicó FKs):**

```sql
SELECT t.id
FROM turnos t
LEFT JOIN pacientes p ON p.id = t.paciente_id
LEFT JOIN doctores d ON d.id = t.doctor_id
WHERE p.id IS NULL OR d.id IS NULL;
```

**Movimientos pendientes de presentar:**

```sql
SELECT id, turno_id, fecha, monto_pendiente_os
FROM movimientos_caja
WHERE estado_pago = 'PENDIENTE'
ORDER BY fecha;
```

**Turnos de un día:**

```sql
SELECT id, paciente_id, doctor_id, fecha_hora_inicio, estado_turno
FROM turnos
WHERE fecha_hora_inicio >= '2026-10-19 00:00:00'
  AND fecha_hora_inicio <= '2026-10-19 23:59:59.999999'
ORDER BY fecha_hora_inicio;
```

**Integridad SQLite:**

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

### 10.3. Mapa de propiedad de archivos

| Si vas a cambiar... | Mirá primero... |
|---|---|
| Reglas de turnos | `turno_service.py`, `turno.py`, `test_turnos*.py` |
| Agenda/disponibilidad | `disponibilidad_service.py`, `disponibilidad*.py` |
| Historia clínica | `historia_clinica_service.py`, `evolucion_medica.py` |
| Anamnesis / ficha médica | `anamnesis_service.py`, `anamnesis_token.py`, `antecedentes_salud.py`, `ficha_medica.js`, `ficha_pdf_service.py` |
| Odontograma | `odontograma_service.py`, `odontograma.py`, `odontograma.js`, `test_odontograma.py` |
| Facturación/caja | `facturacion_service.py`, `movimiento_caja.py` |
| Aranceles/convenios | `arancel_service.py`, `arancel_obra_social.py` |
| Liquidaciones/PDF | `liquidacion_service.py`, `reporte_pdf_service.py`, `pdf.py` |
| WhatsApp | `whatsapp_service.py`, `whatsapp_bot_service.py`, `whatsapp_menu_service.py` |
| Configuración | `config.py`, `.env.example` |
| Esquema/migraciones | `models/`, `db/migraciones.py`, `db/base.py` |
| Panel | `index.html`, `app.js`, `calendar.js`, `styles.css` |
| Vista móvil | `medico.html`, `medico.js`, `medico.css` |
| Ficha pública | `anamnesis.html`, `anamnesis.js`, `anamnesis.css` |
| Deploy | `scripts/publicar.py`, `vercel.json`, `.vercelignore` |

### 10.4. Mantenimiento de este documento

Actualizar `DOCUMENTACION.md` cuando:

- se agrega/elimina una tabla, columna, índice o constraint;
- se agrega/elimina un endpoint;
- cambia un estado o una regla de negocio;
- cambia una variable de entorno;
- cambia el flujo de despliegue;
- se resuelve una deuda técnica de la sección 9.

**Verificación usada para generar este documento:**

```powershell
python -m pytest --collect-only
python -m pytest
git ls-files
```

**Fecha de última verificación:** 26/10/2026.
**Versión de la app:** 1.0.0.
**Resultado de la suite:** 704 tests.

### 10.5. Conclusión

El proyecto **no es un monolito acoplado sin estructura**, pero tampoco es una
arquitectura modular por features ni microservicios. Es un **monolito modular
por capas**, adecuado para un consultorio y para el tamaño actual del dominio:

- la separación `endpoint → schema → service → model` es consistente;
- los cuatro módulos funcionales están identificables;
- la base está normalizada y las relaciones críticas están protegidas;
- la facturación conserva trazabilidad mediante snapshots;
- la historia clínica tiene una política de inmutabilidad explícita;
- el frontend está desacoplado por HTTP, aunque el panel merece dividirse.

Los riesgos principales no son de “arquitectura de carpetas”, sino de
**operación y seguridad**: falta autenticación, el webhook no verifica firma, las
migraciones no están versionadas y SQLite en Vercel no persiste. Resolver esos
puntos es más urgente que partir el sistema en servicios.

La evolución recomendada es incremental: **mantener el monolito modular**,
agregar autenticación/RBAC, adoptar PostgreSQL y Alembic, endurecer el webhook y
separar `app.js` por vistas. Con esas mejoras, la estructura actual puede
sostener el crecimiento del consultorio sin una reescritura.

---

*Fin de la documentación técnica.*
