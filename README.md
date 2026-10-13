# Sistema Odontológico — Módulo 1

**Gestión de Turnos y Automatización de WhatsApp** — Backend en **FastAPI + SQLAlchemy 2.0**.

Este módulo implementa:

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

## 📁 Estructura del proyecto

```
Odontologico/
├── app/
│   ├── main.py                     # Instancia FastAPI, lifespan, manejador de errores
│   ├── core/
│   │   ├── config.py               # Configuración (pydantic-settings / .env)
│   │   ├── datetime_utils.py       # Utilidades de fecha/hora (UTC naive)
│   │   ├── exceptions.py           # Excepciones de dominio -> códigos HTTP
│   │   └── logging_config.py       # Configuración de logging
│   ├── db/
│   │   ├── base_class.py           # DeclarativeBase (SQLAlchemy 2.0)
│   │   ├── base.py                 # Agregador de metadatos (importa modelos)
│   │   ├── session.py              # Engine, SessionLocal, dependencia get_db
│   │   └── init_db.py              # create_all / drop_all
│   ├── models/
│   │   ├── enums.py                # EstadoTurno, EstadoWhatsApp
│   │   ├── doctor.py               # Modelo Doctor
│   │   ├── paciente.py             # Modelo Paciente (obra_social_id → catálogo)
│   │   ├── obra_social.py          # Modelo ObraSocial (catálogo de coberturas)
│   │   ├── disponibilidad.py       # Modelo DisponibilidadDoctor (agenda semanal)
│   │   └── turno.py                # Modelo Turno (+ constraints)
│   ├── schemas/                    # Esquemas Pydantic v2 (I/O de la API)
│   │   ├── doctor.py, paciente.py, turno.py, whatsapp.py
│   │   ├── obra_social.py          # Catálogo de obras sociales
│   │   └── disponibilidad.py       # Rangos semanales y bloques ofrecidos
│   ├── services/                   # Lógica de negocio
│   │   ├── turno_service.py        # Alta/consulta, solapamientos y bloques libres
│   │   ├── doctor_service.py       # Alta, edición (PUT/PATCH) y baja lógica
│   │   ├── paciente_service.py     # Alta/edición y resolución de la cobertura
│   │   ├── obra_social_service.py  # CRUD del catálogo (nombre único, baja lógica)
│   │   ├── disponibilidad_service.py # CRUD de la agenda semanal por odontólogo
│   │   └── whatsapp_service.py     # Recordatorios, webhook y enlaces wa.me
│   └── api/v1/
│       ├── router.py               # Router agregador
│       └── endpoints/
│           ├── doctores.py         # Doctor: CRUD + baja lógica (DELETE)
│           ├── pacientes.py, turnos.py
│           ├── obras_sociales.py   # Catálogo de obras sociales: CRUD + baja lógica
│           ├── disponibilidad.py   # Agenda semanal + horarios disponibles
│           ├── tareas.py           # Tarea programada de recordatorios
│           └── whatsapp.py         # Webhook + recordatorios Click-to-Chat
│   ├── web/
│   │   └── router.py               # Sirve el panel (raíz /) y la vista móvil (/medico)
│   └── static/                     # Interfaz web (panel de la secretaría + móvil)
│       ├── index.html              # Estructura + Tailwind (CDN)
│       ├── styles.css              # Componentes y animaciones
│       ├── app.js                  # Lógica: fetch contra /api/v1
│       ├── medico.html             # Vista móvil del odontólogo (agenda del día)
│       ├── medico.css              # Ajustes mobile-first (táctil, hojas, safe-area)
│       └── medico.js               # Selección del profesional + agenda + acciones
├── scripts/
│   ├── seed.py                          # Datos de ejemplo (odontólogas, pacientes y turnos)
│   ├── migrar_turnos_unique_parcial.py  # Migración al índice único parcial de turnos
│   ├── migrar_pacientes_datos.py        # Migración: DNI y cobertura del paciente
│   └── migrar_obra_social.py            # Migración: obra_social (texto) → catálogo
├── tests/
│   ├── conftest.py                 # Fixtures (SQLite en memoria + TestClient)
│   ├── test_contrato_http_turnos.py # Contrato HTTP: PATCH de turnos y CORS
│   ├── test_disponibilidad.py      # Agenda semanal y bloques ofrecidos
│   ├── test_doctores.py            # Edición, baja lógica y reactivación del doctor
│   ├── test_obras_sociales.py      # Catálogo de coberturas y su vínculo con pacientes
│   ├── test_pacientes.py
│   ├── test_recordatorios_click_to_chat.py # Enlaces wa.me y recordatorios pendientes
│   ├── test_turnos.py
│   ├── test_turnos_actualizacion.py # PATCH /turnos/{id} (reprogramar, cancelar…)
│   ├── test_turnos_paciente_ubicuo.py
│   ├── test_web.py                 # Panel Principal, estáticos y salud de la API
│   └── test_whatsapp_flow.py
├── requirements.txt
├── pytest.ini
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
| **Panel Principal** | KPIs (turnos de hoy, confirmados, pendientes, cancelados), **agenda del día en una columna por odontólogo** (cada columna trae el avatar, la especialidad y el cupo de turnos del profesional, con las tarjetas compactas del día y sus acciones *Editar* / *Cancelar* / *Reactivar*), próximos turnos, envío de recordatorios y tarjeta *Recordatorios por WhatsApp* con el contador de pendientes (lleva a esa pestaña). |
| **Calendario** | Grilla mensual de turnos con **color por estado** (`CONFIRMADO` en verde, `PENDIENTE` con el color del odontólogo y `CANCELADO` en gris atenuado), *tooltip* con el detalle del turno, **filtro por odontólogo** (*Todos los odontólogos* + un botón por profesional) y panel del día con turnos ocupados y horarios disponibles. Los horarios disponibles salen de la **agenda semanal configurada por profesional**, no de una lista fija. Al hacer clic en un **horario disponible con un odontólogo filtrado**, el modal se abre con ese profesional ya seleccionado y **bloqueado** (con un aviso en pantalla), para que el turno no se agende por error en otra agenda. |
| **Agenda** | Navegación por día (‹ / Hoy / › + selector de fecha), filtro por doctor y tarjetas de turno. |
| **Disponibilidad** | Editor de la **agenda semanal por odontólogo**: un panel por día (lunes a domingo) con sus rangos —se pueden cargar **varios por día**, p. ej. lunes 08:00-13:00 y 15:00-19:00—, botón para eliminar cada rango, formulario *desde / hasta* para agregar otro y contador de rangos. Incluye **Restaurar plantilla sugerida** (lun-vie doble turno y sábado por la mañana), **Vaciar la semana** y un probador que muestra los **bloques libres** que la API ofrece para la fecha y la duración elegidas. |
| **Doctores** | Formulario de alta estilizado + tabla con la cantidad de turnos, el **estado** (activo / dado de baja) y las acciones *Editar* (modal con `PUT`), *Dar de baja* (`DELETE` lógico) y *Reactivar* (`PATCH`). |
| **Pacientes** | Formulario de alta y **edición** (nombre, teléfono, email, DNI, obra social —**desplegable del catálogo**— y Nº de afiliado) + tabla de pacientes con acción *Editar*. Si el paciente tenía una cobertura ya dada de baja, el formulario reincorpora la opción con el rótulo *(dada de baja)* para no perderla al guardar otros cambios. |
| **Obras Sociales** | Catálogo de coberturas: nombre (único) y código interno opcional, con alta/edición en modal, baja lógica y reactivación, filtro **Mostrar las dadas de baja** y contador de activas en el menú. Sólo las activas alimentan el desplegable del formulario de pacientes. |
| **Recordatorios Pendientes** | Turnos de los próximos días que todavía no recibieron el aviso, con la vista previa del mensaje y el botón verde **Enviar por WhatsApp** (enlace Click-to-Chat). El contador se ve en el menú y en la cabecera. |

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
| **Doctor**   | `id`, `nombre`, `especialidad`, `activo` |
| **Paciente** | `id`, `nombre`, `telefono`, `email`, `dni`, `obra_social_id`, `numero_afiliado` |
| **ObraSocial** | `id`, `nombre`, `codigo`, `activa` |
| **Turno**    | `id`, `paciente_id`, `doctor_id`, `fecha_hora_inicio`, `fecha_hora_fin`, `estado_turno`, `estado_whatsapp`, `whatsapp_mensaje_id` |
| **DisponibilidadDoctor** | `id`, `doctor_id`, `dia_semana`, `hora_inicio`, `hora_fin` |

**Enumeraciones**

- `EstadoTurno`: `PENDIENTE`, `CONFIRMADO`, `CANCELADO`
- `EstadoWhatsApp`: `PENDIENTE_ENVIO`, `ENVIADO`, `FALLIDO`, `RESPONDIDO`

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
| `GET`  | `/pacientes` | Listar pacientes (`?telefono=...&dni=...`) |
| `GET`  | `/pacientes/{id}` | Obtener paciente |
| `PATCH`| `/pacientes/{id}` | Actualizar paciente (parcial: sólo los campos enviados) |
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

> **Métodos soportados.** Cada ruta acepta únicamente los métodos de esta tabla;
> cualquier otro responde `405 Method Not Allowed` (p. ej. `PUT` o `DELETE` sobre
> `/turnos/{id}`, porque reprogramar, cancelar y reactivar se hacen con `PATCH`).
> En cambio, `/doctores/{id}` y `/obras-sociales/{id}` **sí** exponen `PUT`,
> `PATCH` y `DELETE` (este último como baja lógica), y la agenda del odontólogo
> (`/doctores/{id}/disponibilidad`) usa `PUT`/`DELETE` para sus rangos.
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

Cubren (**169 pruebas**): creación y validación de turnos, solapamientos (exacto,
parcial y contiguo), la regla de **paciente ubicuo** (`test_turnos_paciente_ubicuo.py`:
mismo paciente en otra agenda → `400`, turnos contiguos y cancelados permitidos,
reprogramación y reactivación revalidadas), actualización de turnos, constraint de
rango, **disponibilidad semanal por odontólogo** (`test_disponibilidad.py`),
pacientes (alta, edición y DNI único), **gestión de doctores**
(`test_doctores.py`: `PUT` de reemplazo, `PATCH` parcial, baja lógica `DELETE` con
sus turnos conservados, filtro `?activos=`, `404` al agendar con un profesional
dado de baja y el modal/botones de la vista), **catálogo de obras sociales**
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


