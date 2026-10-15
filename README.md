# Sistema Odontológico — Módulos 1, 2 y 3

**Módulo 1: Gestión de Turnos y Automatización de WhatsApp** ·
**Módulo 2: Historias Clínicas y Evoluciones Médicas** ·
**Módulo 3: Obras Sociales, Liquidación de Médicos y Facturación Mixta** —
Backend en **FastAPI + SQLAlchemy 2.0**, con soporte dual **SQLite / PostgreSQL**.

El **Módulo 1** implementa:

1. **Modelo de datos** (`Doctor`, `Paciente`, `Turno` y `DisponibilidadDoctor`)
   con estados de turno y de notificación por WhatsApp, y restricciones de
   integridad para evitar solapamientos.
2. **Tarea programada** de recordatorios: detecta los turnos a *N* días (por
   defecto **3**), filtra los pendientes y dispara el envío de WhatsApp
   (simulado o real).
3. **Webhook de entrada de WhatsApp** (`POST /api/v1/whatsapp/webhook`) que
   procesa la respuesta de botón del paciente (**Confirmar** / **Cancelar**),
   localiza el turno por el id del mensaje contextual y actualiza sus estados.
4. **Recordatorios por enlace *Click-to-Chat*** (`GET
   /api/v1/whatsapp/recordatorios-pendientes`): la pestaña *Recordatorios
   Pendientes* del panel lista los turnos próximos, arma el enlace `wa.me` con
   el mensaje ya redactado y, al abrirlo, marca el turno como `ENVIADO`.
5. **Disponibilidad flexible por odontólogo** (`DisponibilidadDoctor`): rangos
   horarios por día de la semana —**varios por día**, p. ej. lunes 08:00-12:00 y
   16:00-20:00— que la secretaría administra en la pestaña *Disponibilidad*. El
   calendario ofrece **únicamente** los bloques (de 30 minutos por defecto, o de
   la duración que se elija) que caen dentro de esos rangos, descontando los
   turnos ya agendados.
6. **Estados con color** en la interfaz: los turnos `CONFIRMADO` se pintan en
   **verde** con prioridad, los `PENDIENTE` conservan el color del odontólogo y
   los `CANCELADO` quedan en gris atenuado.

---

## 🩺 Módulo 2 · Historias Clínicas y Evoluciones Médicas

1. **Ficha clínica por paciente** (`HistoriaClinica`): se crea **sola** con la
   primera evolución (relación 1 a 1 con `Paciente`, defendida por el índice
   único `uq_historia_paciente`) y lleva el **número de ficha** visible
   (`HC-000123`, derivado del paciente e indexado como único en
   `uq_historia_numero_ficha`).
2. **Evoluciones médicas** (`EvolucionMedica`): **motivo de consulta**,
   **diagnóstico**, **tratamiento efectuado** y **observaciones**, con el
   **odontólogo que atendió** y la **fecha de atención** (`utcnow` por defecto,
   UTC naive como todo el proyecto).
3. **Radiografías y estudios** (`ArchivoAdjunto`): cada evolución puede llevar
   sus archivos (`url_archivo`, `tipo_archivo`, `fecha_subida`). Se guarda la
   **URL** del archivo, no el binario.
4. **Regla estricta de inmutabilidad** (normativa de salud): una evolución **no
   se puede editar ni borrar**. La API sólo expone **`POST`** (crear) y **`GET`**
   (leer) para el recurso: no hay `PUT`, `PATCH` ni `DELETE` y cualquier intento
   responde **405 Method Not Allowed**. La regla se defiende en **tres capas**:
   las rutas, el servicio (no tiene funciones de edición/baja) y los eventos del
   ORM —`before_update`/`before_delete` disparan `RegistroInmutableError`
   (**403**)—, de modo que ni un script ni una consola puedan alterar un registro.
5. **Búsqueda de pacientes** (`GET /pacientes?buscar=`): por **nombre** (sin
   distinguir mayúsculas ni acentos: `suarez` encuentra a *Suárez*) o por **DNI**
   parcial (se ignoran puntos y guiones: `30.123.456` encuentra a `30123456`).
6. **Celular del odontólogo** (`/medico`): el botón *Historia clínica* abre un
   **buscador por nombre o DNI**; al elegir al paciente se abre su ficha con la
   **línea de tiempo** de sus atenciones —de la más nueva a la más vieja, con el
   doctor que atendió en cada una— y un **formulario táctil** (motivo,
   diagnóstico, tratamiento, observaciones) para guardar la evolución del día.
7. **Panel Principal de la secretaría**: la misma consulta en **modo sólo
   lectura** (buscador + línea de tiempo, sin formulario de alta) y el botón
   *Historia* de cada fila de la tabla de pacientes, que abre la ficha en el
   panel.

---

## 💳 Módulo 3 · Obras Sociales, Liquidación de Médicos y Facturación Mixta

Facturación mixta: cada visita se cobra en parte **en el mostrador** (coseguro
del paciente) y en parte se **presenta a la obra social** para cobrarla por
período. El módulo cubre todo el circuito, desde el precio de cada práctica
hasta la liquidación mensual de los profesionales.

1. **Catálogo de prestaciones** (`Prestacion`): cada práctica facturable con su
   `codigo_nomenclador` (único: `uq_prestacion_codigo`), su descripción y su
   `valor_base` (el precio cuando el paciente no tiene cobertura). La baja es
   **lógica** (`activa = false`): sus aranceles y las facturas ya emitidas se
   conservan.
2. **Aranceles por obra social** (`ArancelObraSocial`): para cada par
   (obra social, prestación) define **cuánto cubre la cobertura**
   (`monto_cubre_os`) y **cuánto queda de coseguro** para el paciente
   (`monto_coseguro_paciente`). Un par por vez: índice único
   `uq_arancel_os_prestacion` (`409` si se repite).
3. **Modalidad de pago del odontólogo**: `Doctor.tipo_pago`
   (`DUENO`, `COMISION`, `SUELDO`) y `Doctor.valor_pago` (porcentaje 0-100 o
   importe fijo mensual). La coherencia se valida en el alta y la edición
   (`422` si una comisión no trae porcentaje, o un sueldo no trae importe) y los
   doctores que ya existían quedan como `DUENO` (comportamiento histórico).
4. **Cierre de la visita (facturación mixta)**: al terminar la atención la
   secretaría elige qué prestaciones se hicieron y el sistema calcula, según la
   cobertura del paciente, **cuánto paga la OS** y **cuánto se cobra de
   coseguro en efectivo**; el resultado se guarda como **un movimiento de caja
   por turno** (`MovimientoCaja`, índice único `uq_movimiento_caja_turno`).
   - Paciente **sin cobertura** → se cobra el `valor_base` completo (particular)
     y el movimiento nace saldado (`COBRADO`).
   - Paciente **con cobertura pero sin arancel cargado** → la facturación se
     **rechaza** con `422` (no se inventan importes). Se puede forzar el cobro a
     valor de referencia con `permitir_particular: true`, que deja la
     advertencia registrada en el movimiento.
   - Un turno **cancelado** no se factura (`422`) y un turno **ya facturado**
     responde `409`. Al facturar, un turno `PENDIENTE` pasa a `CONFIRMADO`.
5. **Detalle congelado** (`DetalleMovimientoCaja`): cada práctica facturada
   guarda el código, la descripción y los importes **al momento del cierre**, de
   modo que editar el arancel mañana no cambia una factura ni una presentación ya
   emitida.
6. **Liquidación de obras sociales**: se agrupan los movimientos `PENDIENTE` de
   un mes por cobertura y se emite la **presentación del período**
   (`PRES-OS3-202611`), que los pasa a `EN_PROCESO_LIQUIDACION`. Cuando entra el
   depósito bancario, la secretaría marca cada orden como **cobrada**
   (`COBRADO`, con el importe realmente depositado si difiere) o **debitada**
   (`DEBITADO_OS`, con el motivo obligatorio). Se puede hacer de a una o sobre
   las órdenes tildadas, y el resumen del período se **exporta a CSV** (para
   Excel) o en **PDF** (para presentarlo ante la prepaga).
7. **Liquidación de profesionales**: a fin de mes, el reporte calcula la
   producción de cada odontólogo (coseguros cobrados + lo presentado a las obras
   sociales) y sus **honorarios** según su tipo de pago: dueño (100 % de su
   producción, o el porcentaje configurado), comisión (porcentaje) o sueldo fijo.
   La modalidad se configura en **Doctores → Editar** (*Tipo de pago*, con el
   porcentaje o el importe mensual que habilita cada opción) y el reporte se
   entrega en **CSV** o en un **PDF** con el detalle del cálculo.
8. **Panel "Facturación y Caja"**: nueva sección del menú con cinco pestañas —
   *Caja del día*, *Liquidación de obras sociales*, *Liquidación de médicos*,
   *Prestaciones y aranceles* y *Convenios*— más el **formulario de cobro** (que
   muestra el cartel **«Cobrar Coseguro en mostrador: $X.XXX»**, el arancel de
   cada práctica y lo que cubre la obra social) y el botón *Cobrar* de cada
   tarjeta de turno de la agenda.
9. **Convenios por obra social e importación de precios**: la pestaña *Convenios*
   muestra **todo el nomenclador base de Córdoba** para la cobertura elegida y,
   al lado del arancel de referencia de cada práctica, el campo **Monto que cubre
   la OS**; el **coseguro sugerido** (arancel base − lo que cubre la OS, nunca
   negativo) se calcula **en vivo** en el navegador y se guarda de verdad en
   `aranceles_obra_social` (los totales de la grilla —arancel base, cubre la OS y
   coseguro sugerido— se actualizan con cada tecla). La misma pantalla trae el
   **importador masivo**: *Descargar Plantilla Excel de Ejemplo* baja una planilla
   de **dos columnas** (`codigo` y `precio_cobertura`) en `.csv` (el que abre
   Excel es-AR) o `.xlsx`, se completa en Excel y se **arrastra** al recuadro; el
   sistema traduce cada código contra el nomenclador, muestra una **vista previa**
   (nuevas, actualizadas, sin cambios, sin precio y filas con error) y recién
   entonces confirma la importación.

---

## 📁 Estructura del proyecto

```
Odontologico/
├── app/
│   ├── main.py                     # Instancia FastAPI, lifespan, manejador de errores
│   ├── core/
│   │   ├── config.py               # Configuración (pydantic-settings / .env)
│   │   ├── datetime_utils.py       # Utilidades de fecha/hora (UTC naive)
│   │   ├── exceptions.py           # Excepciones de dominio -> códigos HTTP
│   │   ├── money.py                # Redondeo y suma de importes (centavos)
│   │   ├── planilla_aranceles.py   # Módulo 3: planilla de precios (CSV/XLSX) del importador
│   │   ├── pdf.py                  # Generador de PDF sin dependencias (reportes)
│   │   └── logging_config.py       # Configuración de logging
│   ├── db/
│   │   ├── base_class.py           # DeclarativeBase (SQLAlchemy 2.0)
│   │   ├── base.py                 # Agregador de metadatos (importa modelos)
│   │   ├── session.py              # Engine, SessionLocal, dependencia get_db
│   │   ├── types.py                # UTCDateTime (UTC naive en PostgreSQL)
│   │   └── init_db.py              # create_all / drop_all
│   ├── models/
│   │   ├── enums.py                # EstadoTurno, EstadoWhatsApp
│   │   ├── doctor.py               # Modelo Doctor
│   │   ├── paciente.py             # Modelo Paciente (obra_social_id → catálogo)
│   │   ├── obra_social.py          # Modelo ObraSocial (catálogo de coberturas)
│   │   ├── disponibilidad.py       # Modelo DisponibilidadDoctor (agenda semanal)
│   │   ├── turno.py                # Modelo Turno (+ constraints)
│   │   ├── historia_clinica.py     # Módulo 2: ficha clínica del paciente (1 a 1)
│   │   ├── evolucion_medica.py     # Módulo 2: atención inmutable (bloquea UPDATE/DELETE)
│   │   ├── archivo_adjunto.py      # Módulo 2: radiografías/estudios de la evolución
│   │   ├── prestacion.py           # Módulo 3: catálogo de prácticas facturables
│   │   ├── arancel_obra_social.py  # Módulo 3: cobertura y coseguro por práctica
│   │   └── movimiento_caja.py      # Módulo 3: cierre de la visita + detalle congelado
│   ├── schemas/                    # Esquemas Pydantic v2 (I/O de la API)
│   │   ├── doctor.py, paciente.py, turno.py, whatsapp.py
│   │   ├── obra_social.py          # Catálogo de obras sociales
│   │   ├── disponibilidad.py       # Rangos semanales y bloques ofrecidos
│   │   ├── historia_clinica.py     # Módulo 2: alta de evolución y ficha (sólo lectura)
│   │   └── prestacion.py, arancel.py, facturacion.py # Módulo 3: precios, caja y liquidaciones
│   ├── services/                   # Lógica de negocio
│   │   ├── turno_service.py        # Alta/consulta, solapamientos y bloques libres
│   │   ├── doctor_service.py       # Alta, edición (PUT/PATCH), baja lógica y pago
│   │   ├── paciente_service.py     # Alta/edición, búsqueda y resolución de la cobertura
│   │   ├── obra_social_service.py  # CRUD del catálogo (nombre único, baja lógica)
│   │   ├── disponibilidad_service.py # CRUD de la agenda semanal por odontólogo
│   │   ├── historia_clinica_service.py # Módulo 2: ficha + alta/consulta de evoluciones
│   │   ├── prestacion_service.py   # Módulo 3: catálogo de prácticas (código único)
│   │   ├── arancel_service.py      # Módulo 3: aranceles por cobertura (resolución)
│   │   ├── facturacion_service.py  # Módulo 3: cierre de visita, caja y liquidación OS
│   │   ├── liquidacion_service.py  # Módulo 3: producción y honorarios por profesional
│   │   ├── reporte_pdf_service.py  # Módulo 3: los dos cierres de mes en PDF
│   │   └── whatsapp_service.py     # Recordatorios, webhook y enlaces wa.me
│   └── api/v1/
│       ├── router.py               # Router agregador
│       └── endpoints/
│           ├── doctores.py         # Doctor: CRUD + baja lógica (DELETE)
│           ├── pacientes.py, turnos.py
│           ├── historias_clinicas.py # Módulo 2: historia clínica (sólo GET y POST)
│           ├── obras_sociales.py   # Catálogo de obras sociales: CRUD + baja lógica
│           ├── prestaciones.py     # Módulo 3: CRUD del catálogo de prácticas
│           ├── aranceles.py        # Módulo 3: aranceles por cobertura + convenios e importador
│           ├── facturacion.py      # Módulo 3: facturar, caja y liquidaciones
│           ├── disponibilidad.py   # Agenda semanal + horarios disponibles
│           ├── tareas.py           # Tarea programada de recordatorios
│           └── whatsapp.py         # Webhook + recordatorios Click-to-Chat
│   ├── web/
│   │   └── router.py               # Sirve el panel (raíz /) y la vista móvil (/medico)
│   └── static/                     # Interfaz web (panel de la secretaría + móvil)
│       ├── index.html              # Estructura + Tailwind (CDN)
│       ├── styles.css              # Componentes, animaciones y línea de tiempo clínica
│       ├── app.js                  # Lógica: fetch contra /api/v1 (+ ficha en sólo lectura)
│       ├── medico.html             # Vista móvil del odontólogo (agenda + historia clínica)
│       ├── medico.css              # Ajustes mobile-first (táctil, hojas, timeline clínico)
│       └── medico.js               # Profesional + agenda + acciones + nueva evolución
├── scripts/
│   ├── seed.py                          # Datos de ejemplo (odontólogas, pacientes y turnos)
│   ├── seed_nomenclador_cba.py          # Nomenclador Córdoba (prestaciones + valor base)
│   ├── publicar.py                      # Publicar en un paso: pruebas + commit + push + deploy
│   ├── migrar_historias_clinicas.py      # Migración Módulo 2: ficha, evoluciones y adjuntos
│   ├── migrar_facturacion.py            # Migración Módulo 3: prácticas, aranceles, caja y pagos
│   ├── migrar_turnos_unique_parcial.py  # Migración al índice único parcial de turnos
│   ├── migrar_pacientes_datos.py        # Migración: DNI y cobertura del paciente
│   └── migrar_obra_social.py            # Migración: obra_social (texto) → catálogo
├── tests/
│   ├── conftest.py                 # Fixtures (SQLite en memoria + TestClient)
│   ├── test_contrato_http_turnos.py # Contrato HTTP: PATCH de turnos y CORS
│   ├── test_disponibilidad.py      # Agenda semanal y bloques ofrecidos
│   ├── test_doctores.py            # Edición, baja lógica y reactivación del doctor
│   ├── test_historias_clinicas.py  # Módulo 2: alta, cronología e inmutabilidad
│   ├── test_facturacion.py         # Módulo 3: precios, cierre de caja y liquidaciones
│   ├── test_convenios.py           # Módulo 3: convenios por obra social e importador de precios
│   ├── test_reportes_pdf.py        # Módulo 3: reportes en PDF (estructura y datos)
│   ├── test_obras_sociales.py      # Catálogo de coberturas y su vínculo con pacientes
│   ├── test_pacientes.py
│   ├── test_recordatorios_click_to_chat.py # Enlaces wa.me y recordatorios pendientes
│   ├── test_seed_nomenclador_cba.py # Seed del nomenclador de Córdoba (idempotente)
│   ├── test_turnos.py
│   ├── test_turnos_actualizacion.py # PATCH /turnos/{id} (reprogramar, cancelar…)
│   ├── test_turnos_paciente_ubicuo.py
│   ├── test_web.py                 # Panel Principal, estáticos y salud de la API
│   └── test_whatsapp_flow.py
├── requirements.txt
├── pytest.ini
├── publicar.bat                    # Doble clic: publica usando el intérprete del .venv
├── .env.example
└── README.md
```

---

## 🚀 Puesta en marcha

```powershell
# 1) Crear y activar entorno virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # PowerShell
# .\.venv\Scripts\activate.bat      # CMD

# 2) Instalar dependencias
pip install -r requirements.txt

# 3) (Opcional) Configurar variables de entorno
Copy-Item .env.example .env

# 4) Cargar datos de ejemplo (crea odontologico.db)
python -m scripts.seed

# 4-bis) (Opcional) Precargar el nomenclador odontológico de Córdoba
python -m scripts.seed_nomenclador_cba

# 5) Levantar el servidor
uvicorn app.main:app --reload
```

- **Panel Principal (interfaz de la secretaría):** <http://127.0.0.1:8000/>
- **Agenda móvil del odontólogo (celular):** <http://127.0.0.1:8000/medico>
- Documentación interactiva (OpenAPI/Swagger): <http://127.0.0.1:8000/docs>
- Health-check: <http://127.0.0.1:8000/health>

> **Siempre con `--reload` (o reiniciando el proceso).** Al editar o cancelar un
> turno la interfaz usa `PATCH /api/v1/turnos/{id}`. Si el servidor que responde
> es un proceso **viejo** (levantado antes del último cambio y sin `--reload`),
> seguirá atendiendo con el código anterior y el navegador mostrará
> **`405 Method Not Allowed`** aunque el endpoint ya exista en el repositorio:
> los cambios de Python recién se aplican al reiniciar el proceso. Para
> comprobarlo: `GET /openapi.json` debe listar `get` **y** `patch` en la ruta
> `/api/v1/turnos/{turno_id}`. En ese caso, detené el proceso (`Ctrl+C`) y
> volvé a levantar el servidor.
>
> **¿El panel aparece sin datos?** Revisá `DATABASE_URL`: si la sesión de
> PowerShell quedó con la variable apuntando a una base de prueba (por ejemplo
> `$env:DATABASE_URL = "sqlite:///./_e2e.db"`), el servidor arranca contra esa
> base vacía en lugar de `odontologico.db`. Se resuelve con
> `Remove-Item Env:DATABASE_URL` (o quitando la línea del `.env`) y reiniciando
> el servidor.

---

## ☁️ Despliegue en Vercel (opcional)

El repositorio está preparado para desplegarse en Vercel: `requirements.txt`
declara FastAPI y el proyecto define `app = FastAPI(...)` en `app/main.py`, que
es uno de los *entrypoints* que Vercel reconoce (`app.py`, `index.py`,
`server.py`, `main.py`, `wsgi.py`, `asgi.py` — y los mismos nombres dentro de
`app/` o `src/`). El `vercel.json` sólo declara el preset:

```json
{ "$schema": "https://openapi.vercel.sh/vercel.json", "framework": "fastapi" }
```

> ⚠️ **No usar `vercel.json` con `builds`/`routes`**: ese formato (API v2) está
> obsoleto y hace fallar el *build*. El contrato se verifica en
> `tests/test_deploy_vercel.py`.

### Publicar los cambios en un paso (`scripts/publicar.py`)

Subir una modificación son varios pasos que hay que repetir **en orden**:
probar, *commitear*, empujar a GitHub y desplegar en Vercel. El script los
encadena y corta al primer fallo (nunca se publica código sin probar ni quedan
*commits* locales sin subir):

```powershell
cd c:\Sistema\Odontologico
python -m scripts.publicar                        # pruebas + commit + push + deploy + verificación
python -m scripts.publicar -m "Arregla el panel"  # con mensaje de commit propio
python -m scripts.publicar --simulacro            # muestra qué haría, sin escribir nada
```

En Windows también está `publicar.bat` en la raíz (doble clic): usa el
intérprete del `.venv` y deja la ventana abierta para leer el resultado.

| Opción | Para qué sirve |
|---|---|
| `-m, --mensaje "..."` | Mensaje del commit (si no se pasa, se arma uno con fecha y hora) |
| `--sin-tests` | No corre `pytest` antes de subir (no recomendado) |
| `--sin-deploy` | Sólo GitHub: *commitea* y empuja, sin desplegar |
| `--solo-deploy` | Sólo Vercel: despliega sin tocar git |
| `--forzar` | Despliega aunque no haya cambios nuevos |
| `--simulacro` | Informa qué haría y con qué archivos, sin ejecutar nada que escriba |
| `--sin-verificar` | No consulta la URL publicada al terminar |
| `--rama <nombre>` | Rama a empujar (por defecto, la que está activa) |
| `--url <base>` | Base de la app publicada que se verifica |

- Las pruebas corren con **el mismo intérprete** que ejecuta el script
  (`python -m pytest`); si alguna falla, no se sube nada.
- Si no hay cambios ni *commits* pendientes, no *commitea* ni despliega: lo
  informa y termina (es el caso de un doble clic de más).
- Al final consulta `/health`, el panel y `/medico` de la URL publicada. Un
  `401`/`403` se informa como **aviso**: es la *Deployment Protection* de Vercel,
  no un error de la aplicación.
- Controles: `tests/test_publicar.py` (con un ejecutor de comandos y un cliente
  HTTP falsos: no corren git, ni la CLI de Vercel, ni tocan la red).

### 0) Deploy por CLI (lo que se usó acá)

```powershell
npm install -g vercel     # una sola vez (requiere Node.js)
vercel login              # elegir "Continue with GitHub"
vercel --prod --yes       # desde c:\Sistema\Odontologico
```

La CLI respeta `.vercelignore` (no sube `.venv/`, la base `.db`, `tests/` ni
`scripts/`). Si el proyecto ya existía creado como *static*, borrar el
`vercel.json` viejo y redeployar es lo que fuerza el preset `fastapi`.


### 1) Importar el proyecto

1. Entrar a <https://vercel.com/new> → **Import Git Repository** →
   `msappia-spec/odontologico` (rama `main`).
2. No hace falta tocar *Build Command*, *Output Directory* ni *Install Command*.
3. Definir las **Environment Variables** (Production y Preview):

   | Variable | Valor | ¿Obligatoria? |
   |---|---|---|
   | `DATABASE_URL` | `postgresql+psycopg://usuario:clave@host:5432/db` | Sí, para persistir datos |
   | `DEBUG` | `false` | No |
   | `CORS_ORIGINS` | `["https://TU-PROYECTO.vercel.app"]` | No (por defecto `["*"]`) |
   | `RECORDATORIO_DIAS` | `3` | No |
   | `WHATSAPP_CODIGO_PAIS` | `549` | No |
   | `WHATSAPP_API_URL` / `WHATSAPP_PHONE_NUMBER_ID` / `WHATSAPP_ACCESS_TOKEN` | credenciales de Meta | Sólo para envíos reales |

4. **Deploy**.

### 2) URLs publicadas

- Panel Principal: `https://TU-PROYECTO.vercel.app/`
- Agenda móvil del odontólogo: `https://TU-PROYECTO.vercel.app/medico`
- Swagger: `/docs` · Health-check: `/health`

### 3) Particularidades del entorno *serverless*

- **Base de datos.** Vercel monta el código en un sistema de archivos de **sólo
  lectura**: el único directorio escribible es `/tmp`. Si no se define
  `DATABASE_URL` con PostgreSQL, la aplicación redirige el SQLite a
  `sqlite:////tmp/odontologico.db` (ver `app/core/config.py`): el panel y la
  vista móvil funcionan, pero **los datos se pierden** cuando Vercel recicla la
  instancia. El esquema lo crea el `lifespan` (`init_db()`); los datos de ejemplo
  se cargan una vez contra esa base con `python -m scripts.seed` y la base local
  se puede copiar entera con `python -m scripts.migrar_sqlite_a_postgres`
  (ver *Migrar los datos locales a la nube*).
- **Recordatorios.** La tarea programada no corre en segundo plano: se dispara
  con `POST /api/v1/tareas/enviar-recordatorios`. Para automatizarla, apuntar un
  *Cron Job* externo (cron-job.org, GitHub Actions) a la URL de producción.
- **Webhook de WhatsApp.** Configurar
  `https://TU-PROYECTO.vercel.app/api/v1/whatsapp/webhook` como *Callback URL*
  en la app de Meta.
- **Versión de Python.** Se fija con `.python-version` (3.14).
- **Frontend.** El panel y la vista móvil se sirven por el mismo dominio
  (`app.mount("/static", ...)` + rutas del `web_router`), por lo que no hay que
  cambiar nada respecto del uso local.

### 4) Migrar los datos locales a la nube

La base de desarrollo es `odontologico.db` (SQLite). Con `DATABASE_URL` ya
definida en Vercel, lo que tenés cargado localmente se copia con
`scripts/migrar_sqlite_a_postgres.py`:

```powershell
# Simulacro: informa qué copiaría y no escribe nada
python -m scripts.migrar_sqlite_a_postgres --destino "postgresql://usuario:clave@ep-xxx.sa-east-1.aws.neon.tech/neondb?sslmode=require"
# Copia real
python -m scripts.migrar_sqlite_a_postgres --destino "postgresql://..." --ejecutar
```

El script crea el esquema en el destino, copia las tablas en **orden de
dependencias** (obras sociales y doctores antes que pacientes y turnos) y conserva
los `id`, así que las claves foráneas siguen siendo válidas. Al terminar reajusta
las secuencias de PostgreSQL (`setval`) y compara los conteos: si algo no cuadra,
termina con error. `--modo agregar` inserta sólo las filas que falten, sin borrar
lo cargado desde la web. Controles en
`tests/test_migracion_sqlite_a_postgres.py`.

Existe además la vía **sin la cadena de PostgreSQL**: cuando la integración de Neon
la guarda como *sensitive* (`vercel env pull` devuelve `[SENSITIVE]`),
`python -m scripts.replicar_datos_api --ejecutar` copia los datos con la propia API
HTTP de la app desplegada, emparejando por nombre, documento/teléfono y
odontólogo + fecha del turno, sin tocar lo que ya existe. Controles en
`tests/test_replicar_datos_api.py`.

---

## 🖥️ Interfaz web (Panel Principal de la secretaría)

Servida por la propia aplicación FastAPI, **sin pasos de build** ni dependencias
nuevas: HTML + Tailwind CSS (CDN) + JavaScript `fetch` contra la API.

- **URL:** <http://127.0.0.1:8000/> — la raíz sirve el panel principal.
- **Archivos:** `app/static/index.html`, `app/static/styles.css`,
  `app/static/app.js` (datos, formularios y toasts) y
  `app/static/calendar.js` (grilla mensual, filtro por odontólogo y panel del
  día). El router que sirve la página está en `app/web/router.py` y el montaje
  de `/static` en `app/main.py`.
- **Encabezado:** el `<h1>` (`text-3xl font-bold text-slate-800 tracking-tight`)
  abarca todo el ancho del área de contenido y va **arriba de todo**, por encima
  de la barra de acciones fija (*Recordatorios* / *Nuevo turno*) y del
  calendario. Muestra el **nombre de la operación elegida** en el menú lateral
  («Panel Principal», «Calendario», «Disponibilidad», «Doctores»…) y el subtítulo
  de abajo (`#page-subtitle`) explica para qué sirve; `app.js` los reescribe en
  cada cambio de pestaña. En el grupo *Operación* del menú, **Panel Principal va
  primero**.

### Secciones

| Sección | Función |
|---------|---------|
| **Panel Principal** | KPIs (turnos de hoy, confirmados, pendientes, cancelados), **agenda del día en una columna por odontólogo** (cada columna trae el avatar, la especialidad y el cupo de turnos del profesional, con las tarjetas compactas del día y sus acciones *Confirmar asistencia* / *Editar* / *Cancelar* / *Reactivar*), próximos turnos, envío de recordatorios y tarjeta *Recordatorios por WhatsApp* con el contador de pendientes (lleva a esa pestaña). |
| **Calendario** | Grilla mensual de turnos con **color por estado** (`CONFIRMADO` en verde, `PENDIENTE` con el color del odontólogo y `CANCELADO` en gris atenuado), *tooltip* con el detalle del turno, **filtro por odontólogo** (*Todos los odontólogos* + un botón por profesional) y panel del día con turnos ocupados y horarios disponibles. Los horarios disponibles salen de la **agenda semanal configurada por profesional**, no de una lista fija. Al hacer clic en un **horario disponible con un odontólogo filtrado**, el modal se abre con ese profesional ya seleccionado y **bloqueado** (con un aviso en pantalla), para que el turno no se agende por error en otra agenda. |
| **Agenda** | Navegación por día (‹ / Hoy / › + selector de fecha), filtro por doctor y tarjetas de turno. |
| **Disponibilidad** | Editor de la **agenda semanal por odontólogo**: un panel por día (lunes a domingo) con sus rangos —se pueden cargar **varios por día**, p. ej. lunes 08:00-13:00 y 15:00-19:00—, botón para eliminar cada rango, formulario *desde / hasta* para agregar otro y contador de rangos. Incluye **Restaurar plantilla sugerida** (lun-vie doble turno y sábado por la mañana), **Vaciar la semana** y un probador que muestra los **bloques libres** que la API ofrece para la fecha y la duración elegidas. |
| **Doctores** | Formulario de alta estilizado + tabla con la **forma de pago** de cada profesional, la cantidad de turnos, el **estado** (activo / dado de baja) y las acciones *Editar* (modal con `PUT` que incluye el desplegable **Tipo de pago** —*Dueño*, *Comisión por porcentaje* o *Sueldo fijo*— y habilita el campo numérico del porcentaje o del sueldo mensual), *Dar de baja* (`DELETE` lógico) y *Reactivar* (`PATCH`). |
| **Pacientes** | Formulario de alta y **edición** (nombre, teléfono, email, DNI, obra social —**desplegable del catálogo**— y Nº de afiliado) + tabla de pacientes con acción *Editar*. Si el paciente tenía una cobertura ya dada de baja, el formulario reincorpora la opción con el rótulo *(dada de baja)* para no perderla al guardar otros cambios. |
| **Obras Sociales** | Catálogo de coberturas: nombre (único) y código interno opcional, con alta/edición en modal, baja lógica y reactivación, filtro **Mostrar las dadas de baja** y contador de activas en el menú. Sólo las activas alimentan el desplegable del formulario de pacientes. |
| **Recordatorios Pendientes** | Turnos de los próximos días que todavía no recibieron el aviso, con la vista previa del mensaje y el botón verde **Enviar por WhatsApp** (enlace Click-to-Chat). El contador se ve en el menú y en la cabecera. |
| **Facturación y Caja** | **Módulo 3.** Cinco pestañas: *Caja del día* (KPIs de efectivo y saldo de obras sociales + movimientos del día con acciones *Cobrar* / *Debitar*), *Liquidación de obras sociales* (cobertura + mes, totales del período, botón **Generar presentación del período**, tildado de órdenes pagadas o debitadas, cierre en lote y **exportación a CSV** o **PDF para la cobertura**), *Liquidación de médicos* (elegís el profesional y el mes: **tabla de subtotales** con la producción, el efectivo, lo presentado a las obras sociales, los honorarios por profesional y los totales del consultorio al pie —según la forma de pago configurada en *Doctores*— más exportación a CSV o **PDF de la liquidación**, que respeta el profesional elegido), *Prestaciones y aranceles* (catálogo de prácticas y tabla de aranceles por cobertura) y *Convenios* (**todo el nomenclador de Córdoba** para la cobertura elegida, con el campo **Monto que cubre la OS**, el **coseguro sugerido** calculado en vivo, los totales de la grilla, guardado en lote y el **importador masivo**: *Descargar Plantilla Excel de Ejemplo* —dos columnas: `codigo` y `precio_cobertura`, en `.csv` o `.xlsx`— + recuadro de **arrastrar y soltar** con vista previa por fila y confirmación). El **formulario de cobro** —que se abre con el botón *Cobrar una visita* o desde la tarjeta de un turno— tiene un **buscador rápido** por código del nomenclador o nombre, muestra por cada práctica su **arancel**, lo que **cubre la obra social** y el **coseguro**, avisa que el saldo queda **PENDIENTE para la liquidación de la cobertura** y calcula el cartel **«Cobrar Coseguro en mostrador: $X.XXX»** con el total de las prácticas; se confirma con **Confirmar y cerrar atención**, que registra el movimiento de caja y finaliza el turno. |

### Colores por estado del turno

El estado clínico manda sobre el color del odontólogo, tanto en la grilla mensual
(`calendar.js`) como en las tarjetas de turno del día y del panel principal (`app.js`):

| `estado_turno` | Color del bloque | Detalle |
|----------------|------------------|---------|
| `CONFIRMADO` | **verde** (`bg-green-500 text-white`) | Tiene prioridad: se pinta en verde aunque el odontólogo tenga otro color asignado. |
| `PENDIENTE` | color del odontólogo | Se conserva la paleta de los avatares/leyenda (`.cal-evento` con el color del profesional). |
| `CANCELADO` | gris atenuado | Tarjeta y bloque en gris, con el nombre tachado; el turno deja de contar y **libera su horario** en *Horarios disponibles*. |

### Disponibilidad médica (agenda semanal)

- Cada odontólogo tiene sus **rangos de atención** en la tabla
  `disponibilidad_doctores`: `dia_semana` (**0 = domingo … 6 = sábado**, igual que
  `Date.getDay()` de JavaScript), `hora_inicio` y `hora_fin`. Al no haber unicidad
  por día, un profesional puede tener **varios rangos el mismo día**.
- El panel los administra en la pestaña **Disponibilidad**: agregar
  (`POST`), eliminar (`DELETE`) y reemplazar toda la semana (`PUT`).
- Los **bloques** que el calendario ofrece se calculan en el backend
  (`turno_service.horarios_disponibles`) partiendo cada rango en tramos de 30
  minutos (configurable) y descartando los que se cruzan con turnos vigentes. Un
  profesional **sin rangos ese día no ofrece ningún horario** y el hueco entre
  dos rangos (p. ej. 12:00-16:00) no se ofrece.
- El script de datos de ejemplo carga la plantilla (lunes a viernes 09:00-13:00 y
  15:00-19:00, sábado por la mañana) **sólo si el profesional todavía no tiene
  rangos**, así `python -m scripts.seed` nunca pisa una agenda editada desde el
  panel.

### Indicadores visuales de WhatsApp

| Situación | Indicador en pantalla |
|-----------|-----------------------|
| `PENDIENTE_ENVIO` | ⏳ *Sin enviar* (gris) |
| `ENVIADO` | 🕐 **reloj amarillo** — enviado, el paciente aún no respondió |
| `FALLIDO` | ⚠️ *Falló el envío* (rojo) |
| Respondió **Confirmar** | ✔️ **tilde verde** — *Confirmado* |
| Respondió **Cancelar** | ✖️ **cruz roja** — *Cancelado* |

### Recordatorios Pendientes (*Click-to-Chat*)

Alternativa **gratuita** al envío automático por la Cloud API, pensada para el
día a día del consultorio: la secretaría abre el chat del paciente con el
mensaje ya redactado y lo envía desde el WhatsApp del consultorio.

- La pestaña **Recordatorios Pendientes** lista los turnos de los próximos
  **3 días** (la ventana se cambia con el selector *Días a futuro*: 1, 2, 3, 5,
  7 o 14 días), ordenados cronológicamente, con el teléfono, los días restantes
  y la vista previa del mensaje.
- El botón verde **Enviar por WhatsApp** abre
  `https://wa.me/<teléfono>?text=<mensaje>` con el texto **codificado (URL
  Encoding)**, en una pestaña nueva. Si el paciente no tiene teléfono, el botón
  queda deshabilitado.
- Al abrir el enlace, el panel marca el turno con
  `PATCH /api/v1/turnos/{id}` (`{"estado_whatsapp": "ENVIADO"}`) **sin tocar el
  estado clínico**, y el turno desaparece de la lista y del contador.
- El mensaje no depende del idioma del sistema: la fecha se arma "en letras"
  (`viernes 16 de octubre de 2026 a las 10:00`) y el profesional se nombra con
  su artículo (`con la Dra. Gómez`, `con el Dr. House`). Los teléfonos se
  normalizan a formato internacional con el prefijo de móvil de
  `WHATSAPP_CODIGO_PAIS` (`549` para Argentina).

### Conexión real con la API

El panel principal consume los mismos endpoints del backend:

- `POST /api/v1/doctores` y `POST /api/v1/pacientes` → botones **Guardar** de los formularios.
- `POST /api/v1/turnos` → modal *Nuevo turno* (doctor + paciente + fecha + hora + duración).
- `PATCH /api/v1/turnos/{id}` → acciones **Editar**, **Cancelar** y **Reactivar** de las tarjetas de turno (día y calendario).
- `GET /api/v1/doctores/{id}/disponibilidad` → pestaña **Disponibilidad** y grilla
  mensual: cada profesional lee su agenda semanal al cargar los datos del panel.
- `POST`, `PUT` y `DELETE` sobre `/api/v1/doctores/{id}/disponibilidad…` →
  agregar, reemplazar y eliminar los rangos semanales desde la pestaña
  **Disponibilidad**.
- `GET /api/v1/doctores/{id}/horarios-disponibles?fecha=&duracion_minutos=` →
  probador de *bloques libres* de la pestaña **Disponibilidad** (el mismo cálculo
  que alimenta el panel *Horarios disponibles* del calendario).
- `POST /api/v1/tareas/enviar-recordatorios` → botones de recordatorios del Panel Principal.
- `GET /api/v1/whatsapp/recordatorios-pendientes?dias=N` → pestaña **Recordatorios
  Pendientes** (lista, contador del menú y de la cabecera).
- `PATCH /api/v1/turnos/{id}` con `{"estado_whatsapp": "ENVIADO"}` → se envía al
  abrir el enlace de WhatsApp de una fila de *Recordatorios Pendientes*.
- `POST /api/v1/whatsapp/webhook` → botones **“Responder: Confirma / Cancela”**,
  que aparecen en el turno una vez enviado el recordatorio. Reproducen el webhook
  de Meta usando el `whatsapp_mensaje_id` como `context.id`, lo que permite ver el
  ciclo completo (enviado → respuesta → confirmado/cancelado) sin un teléfono real.
  Como dependen de ese identificador (dato efímero de Meta que **no** se copia al
  migrar la base), la asistencia también se puede confirmar sin WhatsApp con el
  botón **Confirmar asistencia** de la tarjeta del turno
  (`PATCH` con `{"estado_turno":"CONFIRMADO"}`) y con el tilde verde de la agenda
  móvil: son acciones de un toque pensadas para el paciente que avisa por teléfono
  o llega al consultorio.

> **Método HTTP de la actualización de turnos.** *Editar* y *Cancelar* comparten
> el mismo endpoint: **`PATCH /api/v1/turnos/{id}`** (el `fetch` del panel
> envía `method: "PATCH"` con `Content-Type: application/json` y, al cancelar,
> el cuerpo `{"estado_turno":"CANCELADO"}`). La ruta **no** expone `PUT` ni
> `DELETE`: si se invocan, la API responde `405 Method Not Allowed`. Cancelar no
> borra el turno (queda el historial con estado `CANCELADO`) y libera el bloque
> en *Horarios disponibles* del calendario. Una suite de pruebas
> (`tests/test_contrato_http_turnos.py`) fija este contrato por los dos lados
> —API y JavaScript del panel principal— para que una desalineación falle en `pytest`
> y no en el navegador.

> **Errores visibles para la secretaría.** El rechazo por *solapamiento del
> odontólogo* (`409`) y el de *paciente ya agendado en otra agenda* (`400`) se
> muestran dentro del modal —título, motivo y el turno en conflicto— y además se
> avisan con un *toast*, de modo que ninguna solicitud falla en silencio.

> Todo lo que se guarda desde la interfaz va a la **misma base SQLite**
> (`odontologico.db`) que usa la API.


---

## 📱 Vista móvil del odontólogo (`/medico`)

Interfaz **exclusiva para celulares**, pensada para que cada profesional consulte
y opere su propia agenda del día sin pasar por la recepción. Es otra página de la
misma aplicación (`app/static/medico.html` + `medico.css` + `medico.js`), servida
por `app/web/router.py` en **`/medico`** y **sin pasos de build**: Tailwind por
CDN, HTML y `fetch` contra la misma API `v1`.

- **Meta viewport móvil:** `width=device-width, initial-scale=1.0,
  viewport-fit=cover` (más `theme-color` y `mobile-web-app-capable`), campos con
  `font-size: 16px` para que iOS no haga zoom al enfocar y objetivos táctiles de
  **44 px** como mínimo.
- **Selección del profesional:** pantalla limpia con una tarjeta por odontólogo
  activo (`GET /doctores?activos=true`), su especialidad y cuántos turnos tiene
  hoy. La elección se guarda en **`localStorage`** (clave
  `odonto.medico.doctorId`), así el celular entra directo a la agenda en la
  próxima visita; el botón del encabezado permite cambiar de profesional.
- **Agenda del día:** una **lista vertical cronológica** que intercala los turnos
  ocupados (`GET /turnos?doctor_id=…`) con los **horarios disponibles** que
  calcula el backend
  (`GET /doctores/{id}/horarios-disponibles?fecha=hoy&duracion_minutos=30`), es
  decir, la disponibilidad **regular y específica** de ese día. La cabecera
  aclara si el día se rige por la *Agenda habitual* (semanal) o por un *Día
  personalizado* (excepciones por fecha) y resume cuántos turnos y horarios
  libres hay.
- **Turnos ocupados:** nombre del paciente, teléfono, DNI, horario y estado. El
  `CONFIRMADO` se pinta con **fondo verde**, el `PENDIENTE` toma el color del
  odontólogo y los cancelados del día se listan aparte, en gris y tachados.
- **Confirmar la asistencia en un toque:** el botón de *tilde* de cada turno
  pendiente ejecuta `PATCH /api/v1/turnos/{id}` con `{"estado_turno": "CONFIRMADO"}`
  (el mismo que el botón *Confirmar asistencia* del panel) y el bloque pasa al
  verde sin esperar la respuesta del recordatorio de WhatsApp.
- **Cancelar en un toque:** el botón de *cesto* abre una hoja de confirmación que
  ejecuta `PATCH /api/v1/turnos/{id}` con `{"estado_turno": "CANCELADO"}` (el
  mismo endpoint que el botón *Cancelar* del panel); el horario vuelve a quedar
  libre.
- **Agendar en un toque:** cada bloque *Horario disponible* es cliqueable y abre
  un formulario móvil simplificado (**Nombre, Teléfono y DNI** opcional). Se crea
  el paciente con `POST /api/v1/pacientes` —reutilizando la ficha si el DNI ya
  existe— y el turno con `POST /api/v1/turnos` en el horario del bloque.
- **Siempre sincronizado:** al usar los mismos endpoints y la misma base SQLite,
  lo que se agenda o cancela desde el celular aparece al instante en el Panel
  Principal de la recepción (y a la inversa). La agenda además se refresca al
  volver a la app, cada 60 segundos y con el botón *Actualizar*. Los rechazos del
  backend se explican en pantalla: `409` (el odontólogo ya tiene un turno en ese
  horario), `400` (el paciente está citado en otra agenda) y los errores de
  validación del alta.

---

## 🗃️ Modelo de datos

| Entidad      | Campos |
|--------------|--------|
| **Doctor**   | `id`, `nombre`, `especialidad`, `activo`, `tipo_pago`, `valor_pago` |
| **Paciente** | `id`, `nombre`, `telefono`, `email`, `dni`, `obra_social_id`, `numero_afiliado` |
| **ObraSocial** | `id`, `nombre`, `codigo`, `activa` |
| **Turno**    | `id`, `paciente_id`, `doctor_id`, `fecha_hora_inicio`, `fecha_hora_fin`, `estado_turno`, `estado_whatsapp`, `whatsapp_mensaje_id` |
| **DisponibilidadDoctor** | `id`, `doctor_id`, `dia_semana`, `hora_inicio`, `hora_fin` |
| **Prestacion** | `id`, `codigo_nomenclador`, `descripcion`, `valor_base`, `activa` |
| **ArancelObraSocial** | `id`, `obra_social_id`, `prestacion_id`, `monto_cubre_os`, `monto_coseguro_paciente` |
| **MovimientoCaja** | `id`, `turno_id`, `doctor_id`, `fecha`, `monto_efectivo`, `monto_pendiente_os`, `monto_cobrado_os`, `estado_pago`, `observaciones` |
| **DetalleMovimientoCaja** | `id`, `movimiento_caja_id`, `prestacion_id`, `codigo_nomenclador`, `descripcion`, `cantidad`, `monto_cubre_os`, `monto_coseguro_paciente` |

**Enumeraciones**

- `EstadoTurno`: `PENDIENTE`, `CONFIRMADO`, `CANCELADO`
- `EstadoWhatsApp`: `PENDIENTE_ENVIO`, `ENVIADO`, `FALLIDO`, `RESPONDIDO`
- `TipoPagoDoctor`: `DUENO`, `COMISION`, `SUELDO` (cómo se liquida la producción)
- `EstadoPagoMovimiento`: `PENDIENTE`, `EN_PROCESO_LIQUIDACION`, `COBRADO`, `DEBITADO_OS`

**Restricciones de integridad del turno**

- `uq_turno_doctor_fecha_inicio` → **índice único parcial** sobre
  (`doctor_id`, `fecha_hora_inicio`, `WHERE estado_turno <> 'CANCELADO'`): impide
  que un mismo doctor tenga dos turnos **vigentes** con idéntica fecha/hora de
  inicio. Al excluir los `CANCELADO`, cancelar un turno libera su horario exacto.
- `ck_turno_fin_posterior_inicio` → `CheckConstraint(fecha_hora_fin > fecha_hora_inicio)`.

**Restricciones de integridad de la disponibilidad**

- `ck_disponibilidad_dia_semana_valido` →
  `CheckConstraint(dia_semana >= 0 AND dia_semana <= 6)` (0 = domingo … 6 = sábado).
- `ck_disponibilidad_fin_posterior_inicio` →
  `CheckConstraint(hora_fin > hora_inicio)`.
- `doctor_id` es `ForeignKey(..., ondelete="CASCADE")`: al borrar un profesional
  se eliminan sus rangos (el ORM también usa `delete-orphan`).
- `ix_disponibilidad_doctor_dia`: índice compuesto por (`doctor_id`, `dia_semana`).
- El solapamiento **entre rangos del mismo día** se valida en la capa de servicio
  (`disponibilidad_service.agregar_rango` → `409`; y el `PUT` de la semana entera
  lo valida el esquema `DisponibilidadReemplazo` → `422`). Los rangos
  **contiguos** (08:00-12:00 y 12:00-16:00) se aceptan siempre.

**Restricciones de integridad del paciente**

- `uq_paciente_dni` → **índice único** sobre `dni`: el documento es opcional,
  pero no se repite entre pacientes. Al tratarse de un índice único, admite
  tantos `NULL` como pacientes sin documento haya (ni SQLite ni PostgreSQL
  consideran duplicados dos `NULL`), mientras que un DNI informado —normalizado
  sin puntos ni guiones— sí es excluyente.
- `nombre` y `telefono` son obligatorios; `email`, `dni`, `obra_social_id` y
  `numero_afiliado` se guardan como `NULL` cuando llegan vacíos.
- `obra_social_id` es `ForeignKey(..., ondelete="SET NULL")` al catálogo
  `obras_sociales` (índice `ix_pacientes_obra_social_id`). La cobertura se expone
  también como nombre en la lectura (`obra_social`), resuelto desde la relación,
  para que el panel muestre la tabla sin hacer *joins* en el navegador.

**Restricciones de integridad del catálogo de obras sociales**

- `uq_obra_social_nombre` → **índice único** sobre `nombre`: el nombre de la
  cobertura no se repite (la verificación previa del servicio tampoco distingue
  mayúsculas → `409`, y el índice cubre la condición de carrera).
- `activa` implementa el **borrado lógico** del catálogo: `DELETE
  /api/v1/obras-sociales/{id}` marca `activa = false` y la cobertura deja de
  ofrecerse en el desplegable, pero la fila se conserva porque los pacientes que
  la tenían siguen apuntándole (su nombre se sigue viendo en la ficha).
- `codigo` es opcional (`VARCHAR(30)`) y sirve para identificar la cobertura ante
  el financiador; enviarlo vacío se guarda como `NULL`.

**Restricciones de integridad de la facturación (Módulo 3)**

- `uq_prestacion_codigo` → **índice único** sobre `codigo_nomenclador`: el código
  con el que la obra social reconoce la práctica no se repite (la verificación
  previa del servicio tampoco distingue mayúsculas → `409`). La baja de la
  práctica es **lógica** (`activa = false`).
- `uq_arancel_os_prestacion` → **índice único** sobre
  (`obra_social_id`, `prestacion_id`): una cobertura tiene **un** arancel por
  práctica (repetirlo responde `409`). El borrado del arancel es **físico**
  (`DELETE /aranceles/{id}`): es una tabla de precios, y las facturas ya emitidas
  conservan sus importes congelados en el detalle.
- `uq_movimiento_caja_turno` → **índice único** sobre `turno_id`: un turno se
  factura **una sola vez** (el cierre de caja es el comprobante de esa visita; la
  segunda facturación responde `409`).
- `ix_movimiento_caja_fecha` → índice por `fecha`: la caja diaria y las
  liquidaciones consultan por rango de fechas (mes calendario).
- `turno_id` y `doctor_id` son `ForeignKey(..., ondelete="RESTRICT")`: el
  movimiento es un registro contable y no se borra en cascada (mismo criterio que
  `EvolucionMedica.doctor_id`).
- `doctor_id` en `detalles_movimiento_caja` es `RESTRICT`, pero el detalle guarda
  su **propia copia** de la práctica (`codigo_nomenclador`, `descripcion` y los
  importes): la factura no cambia si luego se edita el catálogo.
- Los **importes** (`valor_base`, montos de arancel, `monto_efectivo`,
  `monto_pendiente_os`) son `Float` y se redondean a **dos decimales** con
  `app.core.money` (aritmética decimal, para que el total del mostrador coincida
  exactamente con el que calcula la obra social).

> **Sobre la baja de doctores.** `activo` en `Doctor` funciona igual que `activa`
> en el catálogo: `DELETE /api/v1/doctores/{id}` marca `activo = false`
> (`doctor_service.dar_de_baja`) sin borrar la fila, de modo que sus turnos y su
> historial se conservan, deja de ofrecerse en las agendas y desplegables y se
> reactiva con `PATCH {"activo": true}`. La regla se aplica en un solo lugar
> (`doctor_service.obtener_doctor(..., solo_activos=True)`), que usan el alta y la
> reprogramación de turnos: agendar con un profesional dado de baja responde
> `404` con el detalle de que está inactivo.

> **Sobre el solapamiento (nota de diseño).** Un índice único sólo puede
> bloquear duplicados **exactos**; los solapamientos *parciales* (p. ej.
> 09:00–09:30 vs. 09:15–09:45) se validan además en la capa de servicio
> `turno_service._validar_solapamiento` (condición `existente.inicio < nuevo.fin
> AND existente.fin > nuevo.inicio`), ignorando los turnos `CANCELADO`.
> Consecuencia: un turno cancelado **no** bloquea ni su inicio exacto ni los
> solapamientos parciales, por lo que el horario vuelve a estar disponible (es
> lo que ofrece el panel de *Horarios disponibles* del calendario).
>
> **Sobre el paciente ubicuo (nota de diseño).** Un paciente tampoco puede estar
> en dos agendas a la vez. Antes de crear o actualizar un turno, la capa de
> servicio ejecuta `turno_service._validar_paciente_ubicuo`, que busca otro turno
> **del mismo paciente** con otro odontólogo, en estado distinto de `CANCELADO` y
> con solapamiento parcial o exacto. Si existe, responde **HTTP 400** con
> `detail = "El paciente ya tiene un turno asignado con otro odontólogo en este
> horario"` y el detalle `turno_en_conflicto` (id, doctor_id y rango), que la
> interfaz usa para explicar dónde está el paciente. El caso del *mismo*
> odontólogo se excluye de esta validación porque ya lo cubre
> `_validar_solapamiento` con `409`. Un turno `CANCELADO` deja de bloquear: por
> eso, al reactivar un turno, el horario se revalida y puede rechazarse con `400`.
>
> **Migración de bases existentes.** Las bases creadas con la versión anterior
> conservan la `UniqueConstraint` global (un cancelado seguía bloqueando su
> inicio exacto). Para convertirlas al índice parcial, con la app detenida:
>
> ```bash
> python -m scripts.migrar_turnos_unique_parcial
> ```
>
> El script es idempotente (no hace nada si la restricción antigua ya no está) y
> guarda una copia de seguridad del archivo `.db` antes de reconstruir la tabla.
>
> **Migración de pacientes (DNI y cobertura).** Las bases creadas antes de
> incorporar `dni`, `obra_social` y `numero_afiliado` no reciben las columnas
> nuevas con `create_all`. Para agregarlas (operación no destructiva que conserva
> las filas existentes) y crear el índice único del DNI, con la app detenida:
>
> ```bash
> python -m scripts.migrar_pacientes_datos
> ```
>
> También es idempotente, guarda una copia de seguridad del `.db` y documenta el
> equivalente en PostgreSQL para quien cambie de motor.
>
> **Migración de la historia clínica (Módulo 2).** Las bases creadas antes de la
> historia clínica no reciben las tablas nuevas con `create_all` si la app no se
> reinicia; para crearlas **explícitamente** (operación puramente aditiva, no toca
> datos existentes), con la app detenida:
>
> ```bash
> python -m scripts.migrar_historias_clinicas
> ```
>
> Crea `historias_clinicas`, `evoluciones_medicas` y `archivos_adjuntos` con sus
> índices (``uq_historia_paciente``, ``uq_historia_numero_ficha``, el compuesto
> ``ix_evolucion_historia_fecha`` y ``ix_adjunto_evolucion``), completa los índices
> que falten si el archivo venía de una corrida interrumpida, guarda una copia del
> `.db` y verifica que `pacientes`, `doctores` y `turnos` conserven sus filas. Es
> idempotente: si ya está todo creado informa *"nada que migrar"*.
>
> En PostgreSQL, `Base.metadata.create_all` (que corre al arrancar la app) también
> crea las tres tablas; el encabezado del script incluye el DDL equivalente.
>
> **Migración de la facturación (Módulo 3).** El catálogo de prácticas, los
> aranceles y las tablas de caja son nuevas, y `doctores` suma las columnas
> `tipo_pago` y `valor_pago`. `create_all` crea las tablas nuevas pero **no**
> agrega columnas a una tabla existente, así que las bases con datos necesitan
> este paso (aditivo, no borra nada), con la app detenida:
>
> ```bash
> python -m scripts.migrar_facturacion
> ```
>
> Crea `prestaciones`, `aranceles_obra_social`, `movimientos_caja` y
> `detalles_movimiento_caja` con sus índices únicos (`uq_prestacion_codigo`,
> `uq_arancel_os_prestacion`, `uq_movimiento_caja_turno`), agrega
> `doctores.tipo_pago` (`VARCHAR(15) NOT NULL DEFAULT 'DUENO'`) y
> `doctores.valor_pago` (admite `NULL`), guarda una copia del `.db` y verifica que
> `pacientes`, `doctores`, `turnos` y `obras_sociales` conserven sus filas. Es
> idempotente y los doctores que ya existían quedan como **dueños** (se liquidan
> con el 100 % de su producción) hasta que se les configure otra modalidad.
> El encabezado del script incluye el `ALTER TABLE` y el DDL equivalente para
> PostgreSQL.
>
> **Nomenclador odontológico de Córdoba (opcional, no es una migración).** La
> grilla referencial del Colegio Odontológico de la Provincia de Córdoba se
> precarga en la tabla `prestaciones` con `python -m scripts.seed_nomenclador_cba`:
> 9 códigos oficiales (`10100` consulta, `10400` urgencia, `20100`/`21600`
> obturaciones, `30100`/`30200` conductos, `50100` tartrectomía, `10010`/`10030`
> cirugía) con la descripción por capítulo y un **valor base orientativo**. Usa la
> misma `DATABASE_URL` que la aplicación (SQLite local o PostgreSQL en la nube) y
> **no duplica**: compara los códigos ya existentes sin distinguir mayúsculas ni
> espacios (idx. `uq_prestacion_codigo`) y los omite, así que se puede ejecutar
> todas las veces que haga falta. Acepta `--actualizar` (reescribe descripción y
> precio de los ya cargados, útil tras un aumento de la grilla), `--dry-run`
> (muestra sin escribir) y `--sin-detalle` (sólo el resumen). No toca pacientes,
> odontólogos ni turnos; los **aranceles por obra social** se cargan aparte, en
> *Facturación y Caja → Convenios* (la grilla completa del nomenclador, con la
> planilla de Excel para importarlos en lote) o de a uno en *Prestaciones y
> aranceles*, porque son el acuerdo con cada cobertura.
>
> **Migración de la cobertura al catálogo.** Las bases creadas con el campo de
> texto `Paciente.obra_social` necesitan el paso a la clave foránea
> `obra_social_id` (tabla `obras_sociales`, nombre único sin distinguir
> mayúsculas). Con la app detenida:
>
> ```bash
> python -m scripts.migrar_obra_social
> ```
>
> Crea la tabla del catálogo y su índice único, agrega la columna
> `obra_social_id` (con su índice), **traslada** cada nombre distinto de texto al
> catálogo vinculando a sus pacientes (backfill, no destructivo) y elimina la
> columna heredada si el SQLite lo permite (3.35+). Verifica al final que la
> cantidad de pacientes no cambió, guarda una copia del `.db` y es idempotente.
> Si la base todavía no tenía ninguna de las dos columnas, el orden es primero
> `migrar_pacientes_datos` (agrega `obra_social` de texto + `dni`) y después
> `migrar_obra_social` (lo convierte en clave foránea).


---

## 🔌 Endpoints

Prefijo base de la API: `/api/v1` (las rutas de la interfaz web —`/`, `/docs`,
`/static/*`— no llevan prefijo).

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET`  | `/` | Sirve el **panel principal web** de la secretaría |
| `GET`  | `/docs` | Documentación interactiva (Swagger UI) |
| `GET`  | `/health` | Chequeo de estado |
| `GET`  | `/static/*` | Recursos de la interfaz (CSS/JS) |
| `POST` | `/doctores` | Crear doctor |
| `GET`  | `/doctores` | Listar doctores (`?activos=true/false`; sin filtro devuelve también los dados de baja) |
| `GET`  | `/doctores/{id}` | Obtener doctor |
| `PUT`  | `/doctores/{id}` | Reemplazar doctor (payload completo: `nombre`, `especialidad`, `activo`) |
| `PATCH`| `/doctores/{id}` | Actualizar doctor (parcial; `{"activo": false}` da de baja y `{"activo": true}` reactiva) |
| `DELETE`| `/doctores/{id}` | **Baja lógica** del doctor (`activo = false`, conserva sus turnos) |
| `GET`  | `/doctores/{id}/disponibilidad` | Listar la **agenda semanal** del odontólogo (varios rangos por día) |
| `POST` | `/doctores/{id}/disponibilidad` | Agregar un rango horario (si se solapa con otro del mismo día → `409`) |
| `PUT`  | `/doctores/{id}/disponibilidad` | Reemplazar la semana completa (rangos invertidos o solapados en el payload → `422`) |
| `DELETE`| `/doctores/{id}/disponibilidad/{rango_id}` | Eliminar un rango (`404` si no existe o pertenece a otro odontólogo) |
| `GET`  | `/doctores/{id}/horarios-disponibles` | **Bloques libres** de una fecha según la agenda configurada (`?fecha=AAAA-MM-DD&duracion_minutos=5..240`, 30 por defecto) |
| `POST` | `/pacientes` | Crear paciente (DNI único → `409` si ya existe) |
| `GET`  | `/pacientes` | Listar pacientes (`?telefono=...&dni=...`, exactos, y **`?buscar=`** por nombre sin acentos o DNI con puntos) |
| `GET`  | `/pacientes/{id}` | Obtener paciente |
| `PATCH`| `/pacientes/{id}` | Actualizar paciente (parcial: sólo los campos enviados) |
| `POST` | `/pacientes/{id}/evoluciones` | **Módulo 2:** registrar una evolución médica (crea la ficha del paciente) |
| `GET`  | `/pacientes/{id}/evoluciones` | **Módulo 2:** línea de tiempo del paciente (de la más nueva a la más vieja) |
| `GET`  | `/pacientes/{id}/historia-clinica` | **Módulo 2:** ficha completa + línea de tiempo (sólo lectura) |
| `GET`  | `/evoluciones/{id}` | **Módulo 2:** detalle de una evolución médica |
| `POST` | `/obras-sociales` | Crear obra social (nombre único → `409`) |
| `GET`  | `/obras-sociales` | Listar el catálogo (`?activas=true/false`, `?nombre=`) |
| `GET`  | `/obras-sociales/{id}` | Obtener obra social |
| `PUT`  | `/obras-sociales/{id}` | Reemplazar obra social (payload completo: `nombre`, `codigo`, `activa`) |
| `PATCH`| `/obras-sociales/{id}` | Actualizar obra social (parcial; `{"activa": true}` la reactiva) |
| `DELETE`| `/obras-sociales/{id}` | **Baja lógica** de la cobertura (`activa = false`, conserva a los pacientes) |
| `POST` | `/turnos` | Crear turno (valida `400` paciente ya agendado en otra agenda y `409` solapamiento del odontólogo) |
| `GET`  | `/turnos` | Listar turnos (`?doctor_id=&paciente_id=&skip=&limit=`) |
| `GET`  | `/turnos/{id}` | Obtener turno |
| `PATCH`| `/turnos/{id}` | Actualizar turno (reprogramar, reasignar, cancelar o reactivar) con las mismas validaciones |
| `POST` | `/tareas/enviar-recordatorios` | **Tarea programada** de recordatorios WhatsApp |
| `GET`  | `/whatsapp/recordatorios-pendientes` | **Recordatorios pendientes** con enlace Click-to-Chat (`?dias=1..30`, por defecto 3) |
| `GET`  | `/whatsapp/webhook` | Verificación del webhook (Meta) |
| `POST` | `/whatsapp/webhook` | **Webhook de entrada** de WhatsApp |
| `POST` | `/prestaciones` | **Módulo 3:** crear prestación (código de nomenclador único → `409`) |
| `GET`  | `/prestaciones` | Listar el catálogo de prácticas (`?activas=true/false`, `?buscar=`) |
| `GET`  | `/prestaciones/{id}` | Obtener prestación |
| `PUT`  | `/prestaciones/{id}` | Reemplazar prestación (payload completo) |
| `PATCH`| `/prestaciones/{id}` | Actualizar prestación (parcial; `{"activa": true}` la reactiva) |
| `DELETE`| `/prestaciones/{id}` | **Baja lógica** de la práctica (`activa = false`) |
| `GET`  | `/obras-sociales/{id}/aranceles` | **Módulo 3:** aranceles de la cobertura (`?prestacion_id=`) |
| `POST` | `/obras-sociales/{id}/aranceles` | Cargar el arancel de una práctica (par repetido → `409`) |
| `GET`  | `/aranceles/{id}` | Obtener arancel |
| `PUT`  | `/aranceles/{id}` | Reemplazar los montos (cambiar la práctica → `422`) |
| `PATCH`| `/aranceles/{id}` | Actualizar los montos (parcial) |
| `DELETE`| `/aranceles/{id}` | Eliminar el arancel (`204`) |
| `GET`  | `/obras-sociales/{id}/convenio` | **Módulo 3:** grilla completa del nomenclador con la cobertura, el coseguro guardado y el **coseguro sugerido** |
| `PUT`  | `/obras-sociales/{id}/convenio` | Guardar la grilla en lote (si no se manda el coseguro, se sugiere `arancel base − monto`) |
| `GET`  | `/obras-sociales/{id}/convenio/plantilla` | Descargar la **plantilla de ejemplo** (`?formato=csv` —default— o `xlsx`) |
| `POST` | `/obras-sociales/{id}/importar-aranceles` | **Importador masivo**: procesa la planilla (`contenido`/`contenido_base64`, `simular`) y devuelve el reporte por fila |
| `POST` | `/turnos/{id}/facturar` | **Cerrar la visita**: calcula coseguro + saldo OS y crea el movimiento de caja |
| `POST` | `/turnos/{id}/presupuesto` | Calcula el **cartel del mostrador** sin escribir nada |
| `GET`  | `/turnos/{id}/prestaciones-disponibles` | Prácticas con el arancel resuelto para el paciente del turno |
| `GET`  | `/turnos/{id}/facturacion` | Estado de facturación del turno (y su movimiento) |
| `GET`  | `/movimientos-caja` | Caja con filtros (`?desde=&hasta=&obra_social_id=&doctor_id=&estado_pago=&turno_id=`) |
| `GET`  | `/movimientos-caja/resumen` | Totales del día (`?desde=&hasta=`, por defecto hoy) |
| `GET`  | `/movimientos-caja/{id}` | Obtener el cierre de una visita |
| `POST` | `/movimientos-caja/{id}/cobrar` | Marcar el **cobro** de la OS (`monto_cobrado_os` opcional; idempotente) |
| `POST` | `/movimientos-caja/{id}/debitar` | Marcar el **débito** de la OS (motivo obligatorio) |
| `GET`  | `/liquidaciones/obras-sociales` | **Presentación del período** de una cobertura (`?obra_social_id=&anio=&mes=`) |
| `POST` | `/liquidaciones/obras-sociales` | **Generar la presentación**: `PENDIENTE` → `EN_PROCESO_LIQUIDACION` |
| `GET`  | `/liquidaciones/profesionales` | **Liquidación del mes** por odontólogo (`?anio=&mes=&doctor_id=`) |
| `GET`  | `/liquidaciones/obras-sociales/pdf` | La **presentación en PDF** para la cobertura (`application/pdf`, descarga) |
| `GET`  | `/liquidaciones/profesionales/pdf` | La **liquidación en PDF** para el profesional (`application/pdf`, descarga) |

> **Métodos soportados.** Cada ruta acepta únicamente los métodos de esta tabla;
> cualquier otro responde `405 Method Not Allowed` (p. ej. `PUT` o `DELETE` sobre
> `/turnos/{id}`, porque reprogramar, cancelar y reactivar se hacen con `PATCH`).
> En cambio, `/doctores/{id}` y `/obras-sociales/{id}` **sí** exponen `PUT`,
> `PATCH` y `DELETE` (este último como baja lógica), y la agenda del odontólogo
> (`/doctores/{id}/disponibilidad`) usa `PUT`/`DELETE` para sus rangos.
>
> **Inmutabilidad de la historia clínica (Módulo 2).** Las evoluciones médicas
> son registros con valor legal: **sólo** se exponen `POST` (alta) y `GET`
> (lectura). No hay `PUT`, `PATCH` ni `DELETE` para
> `/pacientes/{id}/evoluciones`, `/pacientes/{id}/historia-clinica` ni
> `/evoluciones/{id}`: cualquier intento responde **405**. La regla se controla en
> tres capas (rutas, servicio y eventos del ORM → `403`
> `RegistroInmutableError`) y
> `tests/test_historias_clinicas.py` la vigila en cada corrida de las pruebas.
>
> **Reglas del cierre de caja (Módulo 3).** Un turno se factura **una sola vez**
> (`POST /turnos/{id}/facturar`; el segundo intento responde **409** y la base lo
> garantiza con `uq_movimiento_caja_turno`) y un turno cancelado no se factura
> (**422**). Si el paciente tiene cobertura y falta el arancel de una práctica, la
> facturación se rechaza con **422** salvo `permitir_particular: true`. Los montos
> **no se editan**: se corrigen registrando el cobro o el débito con su
> observación (`/movimientos-caja/{id}/cobrar` y `/debitar`), de modo que el
> circuito del dinero quede trazado. `POST /liquidaciones/obras-sociales` sin
> movimientos `PENDIENTE` responde **422** (no se emite una presentación vacía).
>
> **Reportes en PDF (Módulo 3).** Los dos cierres de mes se bajan también como
> **PDF** (`/liquidaciones/obras-sociales/pdf` y
> `/liquidaciones/profesionales/pdf`), además del CSV que arma el panel: son los
> que se imprimen o se adjuntan. Se generan en el backend con `app/core/pdf.py`,
> un generador propio del formato PDF 1.4 (Helvetica con `WinAnsiEncoding`,
> tablas con encabezado repetido y pie «Página X de Y») que **no agrega
> dependencias** al despliegue, y se alimentan con los **mismos servicios** que
> la pantalla: los importes no se recalculan. El archivo se entrega como
> descarga (`Content-Disposition`); una cobertura inexistente responde **404**.
>
> **CORS.** `app/main.py` registra `CORSMiddleware` con
> `allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]` (la lista
> se declara explícitamente porque es lo que responde al *preflight* del
> navegador). Los orígenes se configuran con `CORS_ORIGINS` (`["*"]` por
> defecto) y el envío de credenciales con `CORS_ALLOW_CREDENTIALS` (`false`).
> El panel principal se sirve desde el mismo origen, así que no depende de CORS; queda
> habilitado para clientes externos (SPA, apps móviles, pruebas locales en otro
> puerto).

### Ejemplos con `curl`

```bash
API=http://127.0.0.1:8000/api/v1

# Doctor
curl -X POST $API/doctores -H "Content-Type: application/json" \
  -d '{"nombre":"Dra. Ana Gómez","especialidad":"Ortodoncia"}'

# Editar un doctor (PUT = reemplazo total; PATCH = sólo los campos enviados)
curl -X PUT $API/doctores/1 -H "Content-Type: application/json" \
  -d '{"nombre":"Dra. Ana Gómez","especialidad":"Endodoncia","activo":true}'

curl -X PATCH $API/doctores/1 -H "Content-Type: application/json" \
  -d '{"especialidad":"Ortodoncia"}'

# Dar de baja / reactivar al doctor (baja lógica: conserva sus turnos)
curl -X DELETE $API/doctores/1
curl -X PATCH $API/doctores/1 -H "Content-Type: application/json" -d '{"activo":true}'

# Catálogo de obras sociales (alimenta el desplegable del formulario de pacientes)
curl -X POST $API/obras-sociales -H "Content-Type: application/json" \
  -d '{"nombre":"OSDE","codigo":"OSDE-01"}'

curl "$API/obras-sociales?activas=true"          # sólo las que se ofrecen
curl -X PATCH $API/obras-sociales/1 -H "Content-Type: application/json" \
  -d '{"nombre":"OSDE Binario"}'

# Paciente (nombre y teléfono son obligatorios; el resto es opcional)
curl -X POST $API/pacientes -H "Content-Type: application/json" \
  -d '{"nombre":"Juan Pérez","telefono":"+5491122334455","email":"juan@example.com",
       "dni":"30123456","obra_social_id":1,"numero_afiliado":"610012345678"}'

# La cobertura también se acepta como texto heredado: se resuelve contra el
# catálogo (vincula la existente o la crea) y nunca se guarda como texto.
curl -X POST $API/pacientes -H "Content-Type: application/json" \
  -d '{"nombre":"Ana Suárez","telefono":"+5491155667788","obra_social":"OSDE"}'

# Actualizar los datos administrativos de un paciente (null quita la cobertura)
curl -X PATCH $API/pacientes/1 -H "Content-Type: application/json" \
  -d '{"obra_social_id":2,"numero_afiliado":"SM-998877"}'

# --- Módulo 2: historia clínica y evoluciones médicas -----------------------

# Buscar al paciente por nombre (sin acentos) o por DNI (con o sin puntos)
curl "$API/pacientes?buscar=suarez"
curl "$API/pacientes?buscar=30.123.456"

# Registrar una evolución: crea la ficha del paciente si todavía no la tenía
# (la ficha se numera sola: HC-000123) y guarda la radiografía de la atención.
curl -X POST $API/pacientes/1/evoluciones -H "Content-Type: application/json" -d '{
  "doctor_id": 1,
  "motivo_consulta": "Dolor en el molar inferior derecho",
  "diagnostico": "Pulpitis irreversible",
  "tratamiento_efectuado": "Endodoncia pieza 46",
  "observaciones": "Indicado ibuprofeno 400 mg cada 8 h",
  "archivos": [
    {"url_archivo": "https://estudios.local/rx-1082.png", "tipo_archivo": "RADIOGRAFIA"}
  ]}'

# Línea de tiempo del paciente (de la atención más nueva a la más vieja)
curl "$API/pacientes/1/evoluciones?limit=20"

# Ficha completa (número de ficha + todas las atenciones): lo que muestra el
# celular del odontólogo y el Panel Principal de la secretaría (sólo lectura)
curl "$API/pacientes/1/historia-clinica"

# Una evolución puntual
curl "$API/evoluciones/1"

# NO EXISTEN (responden 405 Method Not Allowed): los registros clínicos son
# inmutables. No hay forma de editar ni de borrar una evolución por la API.
curl -X PATCH $API/evoluciones/1 -H "Content-Type: application/json" -d '{"diagnostico":"Otro"}'
curl -X DELETE $API/evoluciones/1

# --- Módulo 3: prestaciones, aranceles, facturación y liquidaciones ----------

# Catálogo de prácticas (el código de nomenclador es único)
curl -X POST $API/prestaciones -H "Content-Type: application/json" \
  -d '{"codigo_nomenclador":"01.01","descripcion":"Consulta y diagnóstico","valor_base":18000}'

# Arancel de esa práctica para la obra social 1 (lo que cubre + el coseguro)
curl -X POST $API/obras-sociales/1/aranceles -H "Content-Type: application/json" \
  -d '{"prestacion_id":1,"monto_cubre_os":12000,"monto_coseguro_paciente":6000}'

curl "$API/obras-sociales/1/aranceles"          # tabla de precios de la cobertura

# Grilla de convenios: todo el nomenclador con lo que cubre la OS y el coseguro
curl "$API/obras-sociales/1/convenio"

# Guardar el monto que cubre la OS (el coseguro se sugiere: arancel base - monto)
curl -X PUT $API/obras-sociales/1/convenio -H "Content-Type: application/json" \
  -d '{"filas":[{"prestacion_id":1,"monto_cubre_os":12000}]}'

# Plantilla de ejemplo de dos columnas (codigo / precio_cobertura) para Excel
curl -OJ "$API/obras-sociales/1/convenio/plantilla?formato=csv"   # o formato=xlsx

# Importar la planilla completada ("simular": true para ver el reporte sin guardar)
curl -X POST "$API/obras-sociales/1/importar-aranceles" \
  -H "Content-Type: application/json" \
  -d '{"nombre_archivo":"convenio.csv","simular":true,
       "contenido":"codigo;precio_cobertura\n10100;12000\n"}'

# Qué le corresponde al paciente del turno 1 (arancel ya resuelto por práctica)
curl "$API/turnos/1/prestaciones-disponibles"

# Cartel del mostrador SIN escribir nada (es la misma cuenta que la facturación)
curl -X POST $API/turnos/1/presupuesto -H "Content-Type: application/json" \
  -d '{"prestaciones":[{"prestacion_id":1,"cantidad":1}]}'

# Cerrar la visita: separa el coseguro (efectivo) del saldo de la obra social
curl -X POST $API/turnos/1/facturar -H "Content-Type: application/json" -d '{
  "prestaciones": [{"prestacion_id": 1, "cantidad": 1}],
  "observaciones": "Se cobró el coseguro en efectivo"}'

# Si falta el arancel de una práctica la facturación responde 422; para cobrarla
# a valor de referencia (particular) sólo en esa visita: "permitir_particular": true

# Caja del día y su resumen
curl "$API/movimientos-caja?desde=2026-10-15&hasta=2026-10-15"
curl "$API/movimientos-caja/resumen?desde=2026-10-15&hasta=2026-10-15"

# Presentación del período de la obra social 1 (octubre 2026): sólo lectura
curl "$API/liquidaciones/obras-sociales?obra_social_id=1&anio=2026&mes=10"

# Generar la presentación: los movimientos PENDIENTE pasan a EN_PROCESO_LIQUIDACION
curl -X POST $API/liquidaciones/obras-sociales -H "Content-Type: application/json" \
  -d '{"obra_social_id":1,"anio":2026,"mes":10}'

# Cuando entra el depósito: cobrar una orden (con el importe realmente acreditado)
curl -X POST $API/movimientos-caja/1/cobrar -H "Content-Type: application/json" \
  -d '{"monto_cobrado_os":11500,"observaciones":"Depósito 44821"}'

# O debitarla (el motivo es obligatorio; admite débito parcial con monto_cobrado_os)
curl -X POST $API/movimientos-caja/2/debitar -H "Content-Type: application/json" \
  -d '{"observaciones":"Práctica rechazada por auditoría"}'

# Liquidación de profesionales del mes (dueño / comisión / sueldo)
curl "$API/liquidaciones/profesionales?anio=2026&mes=10"
curl "$API/liquidaciones/profesionales?anio=2026&mes=10&doctor_id=1"

# Los mismos dos reportes, en PDF ($API es la URL de la app; -o guarda el archivo)
curl -o presentacion-os1-202610.pdf \
  "$API/liquidaciones/obras-sociales/pdf?obra_social_id=1&anio=2026&mes=10"
curl -o liquidacion-profesionales-2026-10.pdf \
  "$API/liquidaciones/profesionales/pdf?anio=2026&mes=10"

# Modalidad de pago del odontólogo (dueño, comisión del 40 % o sueldo fijo)
curl -X PATCH $API/doctores/1 -H "Content-Type: application/json" \
  -d '{"tipo_pago":"COMISION","valor_pago":40}'

# Agenda semanal del odontólogo 1: lunes con dos tramos (mañana y tarde)
curl -X POST $API/doctores/1/disponibilidad -H "Content-Type: application/json" \
  -d '{"dia_semana":1,"hora_inicio":"08:00","hora_fin":"12:00"}'

curl -X POST $API/doctores/1/disponibilidad -H "Content-Type: application/json" \
  -d '{"dia_semana":1,"hora_inicio":"16:00","hora_fin":"20:00"}'

# Reemplazar la semana completa (lunes a viernes con doble turno)
curl -X PUT $API/doctores/1/disponibilidad -H "Content-Type: application/json" -d '{
  "rangos":[
    {"dia_semana":1,"hora_inicio":"09:00","hora_fin":"13:00"},
    {"dia_semana":1,"hora_inicio":"15:00","hora_fin":"19:00"},
    {"dia_semana":2,"hora_inicio":"09:00","hora_fin":"13:00"}
  ]}'

# Bloques libres del lunes siguiente (cada 30 minutos, sin los turnos agendados)
curl "$API/doctores/1/horarios-disponibles?fecha=2026-10-19&duracion_minutos=30"

# Quitar un rango de la agenda
curl -X DELETE $API/doctores/1/disponibilidad/1

# Turno (a 3 días vista)
curl -X POST $API/turnos -H "Content-Type: application/json" \
  -d '{"paciente_id":1,"doctor_id":1,
       "fecha_hora_inicio":"2026-10-15T10:00:00",
       "fecha_hora_fin":"2026-10-15T10:30:00"}'

# Ejecutar la tarea programada de recordatorios
curl -X POST $API/tareas/enviar-recordatorios

# Webhook (respuesta de botón "Confirmar")
curl -X POST $API/whatsapp/webhook -H "Content-Type: application/json" -d '{
  "object":"whatsapp_business_account",
  "entry":[{"id":"WABA","changes":[{"field":"messages","value":{
    "messages":[{
      "from":"5491122334455","id":"wamid.IN","type":"interactive",
      "context":{"id":"wamid.SIMULADO.XXXX"},
      "interactive":{"type":"button_reply",
                     "button_reply":{"id":"confirmar_turno","title":"Confirmar"}}
    }]}}]}]}'

# Recordatorios pendientes con su enlace Click-to-Chat (ventana de 7 días)
curl "$API/whatsapp/recordatorios-pendientes?dias=7"
```

---

## 🤖 Automatización de WhatsApp

### Tarea programada (`whatsapp_service.enviar_recordatorios`)

- Calcula la fecha objetivo con `datetime` + `timedelta`:
  `fecha_objetivo = (ahora + timedelta(days=dias)).date()` (por defecto `dias=3`).
- Filtra turnos con `fecha_hora_inicio` dentro de ese día **y**
  `estado_turno == PENDIENTE` **y** `estado_whatsapp == PENDIENTE_ENVIO`.
- Por cada turno:
  - Registra el log: **`Enviando WhatsApp a <telefono>`**.
  - Dispara el envío: si hay credenciales (`WHATSAPP_API_URL`,
    `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`) realiza un `POST`
    **real** con `httpx`; si no, **simula** el `POST` HTTP y genera un `wamid`
    ficticio (`wamid.SIMULADO...`).
  - Actualiza el turno: `estado_whatsapp = ENVIADO` y guarda
    `whatsapp_mensaje_id` (o `FALLIDO` si hubo error).

> **Programación real:** el endpoint actúa como disparador. En producción se
> puede invocar mediante un *cron*, el Task Scheduler de Windows o `APScheduler`,
> por ejemplo `POST /api/v1/tareas/enviar-recordatorios` una vez al día.

### Webhook de entrada (`POST /api/v1/whatsapp/webhook`)

1. Parsea el JSON con esquemas Pydantic (tolerante a campos extra).
2. Extrae el mensaje con `interactive.button_reply`.
3. Toma `context.id` → id (`wamid`) del mensaje saliente original.
4. Busca el `Turno` por `whatsapp_mensaje_id`.
5. Mapea el botón: `confirmar_turno` → `CONFIRMADO`, `cancelar_turno` → `CANCELADO`.
6. Actualiza `estado_turno` y marca `estado_whatsapp = RESPONDIDO`.

**Códigos de error:** `400` payload inválido / botón desconocido / sin
`context.id`; `404` no existe turno con ese `wamid`.

### Recordatorios por enlace *Click-to-Chat*

Cuando no se quieren (o no se pueden) usar las credenciales de la Cloud API, el
panel ofrece la vía gratuita: el backend **arma el enlace y la secretaría envía**.

`GET /api/v1/whatsapp/recordatorios-pendientes?dias=N` (`1 ≤ N ≤ 30`, por defecto
`RECORDATORIO_DIAS`) devuelve, ordenados por fecha, los turnos que caen dentro de
la ventana, con `estado_turno == PENDIENTE` y `estado_whatsapp ==
PENDIENTE_ENVIO`. Cada elemento (`RecordatorioPendiente`) trae el teléfono
normalizado, los días restantes, el mensaje ya redactado y el enlace:

```
https://wa.me/5491122334455?text=Hola%20Juan%20P%C3%A9rez%2C%20te%20recordamos...
```

`whatsapp_service` construye todo con funciones puras y testeables:
`normalizar_telefono_whatsapp` (sólo dígitos + `WHATSAPP_CODIGO_PAIS`),
`formatear_fecha_recordatorio` (fecha "en letras", sin depender del *locale*),
`profesional_con_articulo`, `construir_mensaje_recordatorio` y
`construir_enlace_whatsapp` (`urllib.parse.quote(..., safe="")`). El texto del
mensaje se define en `PLANTILLA_RECORDATORIO`.

> **Sin teléfono no hay enlace.** Si el paciente no tiene teléfono válido,
> `telefono_whatsapp` y `enlace_whatsapp` vuelven vacíos y el panel deshabilita
> el botón (el turno sigue en la lista, recordándole a la secretaría completar
> el dato desde *Pacientes → Editar*).


---

## ✅ Pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Cubren (**461 pruebas**): creación y validación de turnos, solapamientos (exacto,
parcial y contiguo), la regla de **paciente ubicuo** (`test_turnos_paciente_ubicuo.py`:
mismo paciente en otra agenda → `400`, turnos contiguos y cancelados permitidos,
reprogramación y reactivación revalidadas), actualización de turnos, constraint de
rango, **disponibilidad semanal por odontólogo** (`test_disponibilidad.py`),
pacientes (alta, edición y DNI único), **gestión de doctores**
(`test_doctores.py`: `PUT` de reemplazo, `PATCH` parcial, baja lógica `DELETE` con
sus turnos conservados, filtro `?activos=`, `404` al agendar con un profesional
dado de baja y el modal/botones de la vista), **la forma de pago del
profesional** (`test_doctores_pago.py`: tipo y valor de pago persistidos y
validados en el alta, el `PUT` del modal y el `PATCH`, la liquidación del mes que
sale de esa regla y los controles del panel que habilitan el campo numérico),
**catálogo de obras sociales**
(`test_obras_sociales.py`: CRUD, nombre único `409` sin distinguir mayúsculas,
baja lógica y reactivación, filtros, vínculo por `obra_social_id`, compatibilidad
con el texto heredado y la cobertura del paciente que sobrevive a la baja), la
tarea de recordatorios (incluida la verificación del log de envío), el webhook
(confirmar / cancelar / no encontrado / inválido) y la interfaz web
(`test_web.py`: panel principal en `/`, el **encabezado** —el título de la
operación elegida arriba de todo, a todo el ancho, fuera de las `<section>` que
oculta la SPA, sin rastros de «Dashboard» y con *Panel Principal* primero en el
menú de operaciones—, estáticos CSS/JS, pestañas de disponibilidad y obras
sociales, **la agenda del día del Panel Principal en una columna por odontólogo**,
colores por estado y salud de la API).

El **contrato HTTP del panel** se verifica en
`tests/test_contrato_http_turnos.py`: que `PATCH /api/v1/turnos/{id}` cancele y
libere el horario, que `PUT` y `DELETE` respondan `405` en la ruta de turnos, que
los archivos JavaScript usen exactamente los métodos que declara `app.openapi()`
(los `PUT`/`DELETE` del panel sólo apuntan a rutas `/doctores/`: la agenda semanal
y la gestión del profesional, declaradas por la API) y que el *preflight* de
CORS autorice `GET, POST, PUT, PATCH, DELETE, OPTIONS`.

La **disponibilidad flexible** se cubre en `tests/test_disponibilidad.py`
(*52 pruebas*): alta de **varios rangos el mismo día**, rangos contiguos
permitidos, solape con uno existente → `409`, rango invertido y `dia_semana`
fuera de `0..6` → `422`, reemplazo total de la semana (`PUT`, incluida la lista
vacía) con validación de solapes en el payload → `422`, borrado `204` (`404` si el
rango no existe o es de otro odontólogo) y, sobre todo, los **bloques ofrecidos**:
30 minutos por defecto y duración configurable (45 min → 5 bloques), huecos entre
rangos que no se ofrecen, días sin rangos con lista vacía, descuento de los turnos
agendados (incluido uno largo que cruza varios bloques), liberación del horario al
cancelar el turno y que la agenda de otro profesional no descuente nada.

Los **días personalizados por fecha** (`/doctores/{id}/disponibilidad-especifica`,
la tarjeta *Gestionar un día específico* del panel) también se cubren ahí:
precedencia `ESPECIFICO` sobre `SEMANAL` (lo que devuelve `origen`), un día con
sólo tramos `BLOQUEADO` que queda cerrado, el reemplazo del día completo (`PUT`,
incluido el bloqueo `00:00–23:59` y la lista vacía que devuelve el día a la
semana), el borrado `204` y **la baja repetida de un tramo ya eliminado → `404`**:
es el caso del doble clic en la cruz del chip. La baja se resuelve por `id` **y**
`doctor_id`, así que nunca alcanza el tramo de otro profesional, y el contrato del
panel se fija en los mismos tests: el chip inyecta el `id` real que devolvió la
API, el `DELETE` apunta a la excepción concreta, el día se recarga del servidor
después de cada cambio (con `forzar`, sin depender del cache) y un `404` se
informa como aviso «ya estaba eliminado» en lugar de como error.

Los **recordatorios por enlace** se cubren en
`tests/test_recordatorios_click_to_chat.py`: normalización de los teléfonos
(incluido el `549` de móvil), fecha "en letras", artículo del profesional, el
endpoint (orden cronológico, ventana de días, exclusión de turnos cancelados y
de los ya enviados, paciente sin teléfono, validación de `dias` con `422` y
lista vacía), la codificación de la URL y el flujo completo (abrir el enlace
marca `ENVIADO`, no altera el estado clínico y el turno sale de la lista).

La **facturación mixta del Módulo 3** se cubre en `tests/test_facturacion.py`
(*59 pruebas*): el catálogo de prestaciones (código único `409`, normalización y
baja lógica), los aranceles por cobertura (`409` al repetir el par, `PUT`/`PATCH`
de montos, borrado `204` y `422` al intentar cambiar la práctica), el **cierre de
la visita** (separación de coseguro y saldo de la OS, cantidad multiplicada,
particular que nace `COBRADO`, `422` sin arancel y con `permitir_particular`
cobrando a valor de referencia con la advertencia registrada, `409` al facturar
dos veces, `422` con un turno cancelado, confirmación del turno pendiente,
importes congelados en el detalle y `404` de la práctica dada de baja), la **caja**
(listado con filtros por día, cobertura, estado y turno; resumen del día;
cobro con importe distinto al presentado e idempotente; débito con motivo
obligatorio y débito parcial; `409` al cruzar cobro y débito), la **presentación
del período** (agrupación por cobertura y mes, `PENDIENTE` →
`EN_PROCESO_LIQUIDACION`, `422` sin pendientes, cierre individual de cobros y
débitos con sus totales) y la **liquidación de profesionales** (dueño 100 %,
comisión 40 %, sueldo fijo, producción con coseguros y obras sociales, mes
aislado y `422` de una comisión sin porcentaje). También verifica el **esquema**
(el DDL de las tablas nuevas compila con el dialecto de PostgreSQL, los enums son
`VARCHAR` —no tipos nativos— y los índices únicos existen) y el **panel**: la
pestaña *Facturación y Caja* con sus cinco paneles, el cartel del coseguro con
los tres importes del mostrador (arancel, cubre la OS y coseguro), el buscador de
prácticas, la tabla de subtotales de la liquidación de médicos, los
endpoints que consume `app.js` y un chequeo de contrato HTML ↔ JS (todos los
`getElementById` de `app.js` existen como `id` en `index.html`).

Los **convenios por obra social y el importador masivo de precios** se cubren en
`tests/test_convenios.py` (*36 pruebas*): la **grilla del convenio** (todo el
nomenclador con el arancel base, el coseguro sugerido y los totales; `404` si la
cobertura no existe), el **guardado en lote** (persistencia real en
`aranceles_obra_social`, coseguro sugerido cuando no se manda, altas y
actualizaciones, `sin_cambios`, atomicidad ante una práctica inexistente,
`422` con montos negativos o filas repetidas y `404` con una práctica dada de
baja), la **plantilla** (dos columnas exactas, BOM, precios precargados, `.xlsx`
válido, `422` con un formato desconocido), el **importador** (planilla `;`/`,` con
y sin cabecera, códigos con puntos, importes es-AR y en-US, miles con coma,
creación y actualización de aranceles, **simulación que no escribe**, códigos
desconocidos y precios ilegibles reportados por línea sin cortar la importación,
filas sin precio ignoradas, archivo vacío/roto/`.xls` con `422`, `.xlsx` con
`sharedStrings`, ida y vuelta de la plantilla descargada) y el **panel** (la
pestaña *Convenios* con su grilla y sus totales, el botón *Descargar Plantilla
Excel de Ejemplo*, el recuadro de arrastrar y soltar, el cálculo del coseguro en
JavaScript, los endpoints que consume y los estilos). Cierra con la
**integración**: el precio importado es el que cobra el mostrador y el saldo de la
obra social queda `PENDIENTE` en `movimientos_caja` para la liquidación del
período.

La **forma de pago del odontólogo y su liquidación** se cubren en
`tests/test_doctores_pago.py` (*28 pruebas*): el alta guarda la comisión con su
porcentaje, el sueldo fijo mensual y hasta la retención parcial de un dueño (con
round-trip por `GET` y listado), los casos incoherentes responden **422**
(comisión sin porcentaje, sueldo sin importe, porcentaje fuera de 0-100), el
`PATCH` cambia la modalidad sin tocar los datos personales, el `PUT` del modal
guarda la que se eligió y `PATCH {"tipo_pago": "DUENO", "valor_pago": null}` la
devuelve al 100 %; la **liquidación** del mes calcula los honorarios con la regla
vigente (100 % dueño, porcentaje de comisión, fijo del sueldo) y cambia cuando
cambia la configuración; y el **panel** ofrece el desplegable *Tipo de pago* con
las tres opciones del enum, el campo numérico que se habilita por tipo, el error
de la modalidad mostrado dentro del formulario, la columna *Forma de pago* en la
tabla de profesionales, la tabla de subtotales de la liquidación de médicos, el
buscador rápido del formulario de cobro y el **PDF** que respeta el profesional
elegido.

Los **reportes en PDF del Módulo 3** se cubren en `tests/test_reportes_pdf.py`
(*21 pruebas*): el generador `app/core/pdf.py` (firma del archivo, tabla `xref`
con el offset **real** de cada objeto, `/Length` de cada contenido —si el stream
miente sobre su largo ningún visor abre el PDF—, paginado con el encabezado de
la tabla repetido, alineación a la derecha de los totales, codificación WinAnsi
de los acentos y degradación de los símbolos que la codificación no tiene), los
dos endpoints (tipo `application/pdf`, `Content-Disposition` con el nombre del
archivo, **404** de una cobertura inexistente, **422** del período inválido y que
las rutas JSON sigan respondiendo igual), el contenido del reporte (la cobertura,
el paciente **con acentos**, el DNI, los importes en formato `$ 15.000,00` y los
estados traducidos al castellano, no el enum) y que los datos duros (fecha, DNI,
importes, estado) entren en **una sola línea** con los anchos reales de las
columnas.


---

## ⚙️ Decisiones técnicas

- **SQLAlchemy 2.0** con tipado `Mapped[...]` / `mapped_column`.
- **fechas UTC "naive"** (ver `app/core/datetime_utils.py`) para un
  comportamiento idéntico en SQLite y PostgreSQL.
- **Excepciones de dominio** (`AppError`) traducidas a HTTP por un manejador
  global, manteniendo los endpoints limpios.
- **Portabilidad** `Enum(native_enum=False)`: usa `VARCHAR` + `CHECK` en lugar de
  tipos ENUM nativos, válido para SQLite y PostgreSQL.
- **PostgreSQL**: descomentar `psycopg[binary]` en `requirements.txt` y ajustar
  `DATABASE_URL="postgresql+psycopg://usuario:password@host:5432/db"`.
- **Interfaz web sin build**: HTML + Tailwind por CDN + `fetch` (vanilla JS).
  Evita añadir dependencias (`jinja2`, bundlers) y se sirve directamente con
  `StaticFiles` + `FileResponse`, por lo que los tests siguen usando el mismo
  `app.main:app` sin cambios.
- **Disponibilidad en la base, no en el código**: la agenda semanal se administra
  por API (`disponibilidad_doctores`) y el frontend sólo la consume; el cálculo de
  los bloques vive en `turno_service.horarios_disponibles`, una única
  implementación que usan tanto la API como la vista previa del panel.
  `dia_semana` usa **0 = domingo … 6 = sábado** para coincidir con
  `Date.getDay()` del navegador y no convertir nada en el cliente.
- **CORS explícito** (`CORSMiddleware`) con la lista completa de métodos verbales
  —incluidos `PUT`, `PATCH` y `DELETE`— y orígenes configurables por entorno
  (`CORS_ORIGINS`). La interfaz se sirve desde el mismo origen y no lo necesita,
  pero así la API queda consumible desde otros clientes sin tocar código.
- **Facturación con importes congelados (Módulo 3)**: el cierre de una visita
  guarda en `detalles_movimiento_caja` una **copia** del código, la descripción y
  los importes de cada práctica. Editar el arancel o el catálogo después **no**
  cambia una factura ni una presentación ya emitida (trazabilidad contable), y la
  factura sobrevive a la baja lógica de la práctica.
- **Un solo motor de cálculo**: el cartel del mostrador
  (`POST /turnos/{id}/presupuesto`) y la facturación real
  (`POST /turnos/{id}/facturar`) usan la misma función
  (`facturacion_service._resolver_prestaciones`), así que el importe que ve la
  secretaría antes de confirmar es exactamente el que queda guardado. La ayuda de
  carga (`/turnos/{id}/prestaciones-disponibles`) expone el arancel ya resuelto
  por práctica para que el formulario no tenga que calcular nada.
- **Aritmética decimal para el dinero**: los importes se guardan como `Float`
  (decisión del módulo) pero se suman y redondean a dos decimales con
  `app.core.money` (`Decimal` + `ROUND_HALF_UP`), evitando los típicos
  `0.30000000000000004` en un total de caja.
- **Presentación determinística**: el número de presentación del período
  (`PRES-OS3-202611`) se **deriva** de la cobertura y el mes en lugar de guardarse
  en una tabla propia. La presentación se reconstruye desde los movimientos (una
  única fuente de verdad) y el estado de cada orden vive en el propio movimiento
  (`PENDIENTE` → `EN_PROCESO_LIQUIDACION` → `COBRADO` / `DEBITADO_OS`).
- **Cobros y débitos sin editar la factura**: corregir el circuito del dinero se
  hace marcando el cobro (con el importe realmente depositado si difiere) o el
  débito (con el motivo obligatorio) del movimiento, nunca reescribiendo los
  montos: así queda trazado qué pasó con cada orden presentada.
- **Baja lógica vs. configuración**: las prácticas y las coberturas usan borrado
  lógico (`activa`) porque los registros históricos las referencian, mientras que
  el **arancel** (una tabla de precios) se puede borrar y volver a cargar sin
  afectar ninguna liquidación pasada.


