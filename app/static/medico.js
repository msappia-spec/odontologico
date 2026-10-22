/* =========================================================================
   Franciosi Odontología Móvil - Agenda diaria del odontólogo (vista /medico)
   -------------------------------------------------------------------------
   Página *mobile-first* y autónoma: no carga app.js ni calendar.js, sólo
   consume la MISMA API FastAPI (``/api/v1``) que el panel de la secretaría.
   Por eso todo lo que se agenda o cancela acá aparece al instante en el
   Panel Principal de la recepción (y a la inversa).

   Flujo:
     1. Pantalla de selección del profesional (la elección se recuerda en
        ``localStorage``).
     2. Agenda del día elegido: turnos ocupados y horarios libres **intercalados**
        cronológicamente. Los ``CONFIRMADO`` se pintan en verde.
     3. **Navegación del calendario**: se consulta cualquier día —flechas de día y
        de semana, tira de los siete días con la cantidad de turnos, botón *Hoy* y
        selector de fecha—. Los turnos del día y de la semana los filtra el
        backend (``GET /turnos?fecha_desde=&fecha_hasta=``).
     4. Acciones táctiles:
        * agendar en un horario libre: se elige el paciente de la **lista
          completa** de fichas registradas (buscador por nombre, DNI o teléfono) o
          se carga uno nuevo (``POST /pacientes`` + ``POST /turnos``);
        * **editar** un turno (lápiz): cambiar paciente, día, hora, duración o
          estado con ``PATCH /turnos/{id}``;
        * cancelar (cesto): ``PATCH /turnos/{id}`` con estado ``CANCELADO``.

   Vanilla JS + Fetch, sin dependencias, igual que el resto del proyecto.
   ========================================================================= */
(function () {
  "use strict";

  const API = "/api/v1";

  /* Clave con la que se recuerda en el celular *quién* está atendiendo. */
  const CLAVE_DOCTOR = "odonto.medico.doctorId";

  /* Ancho de los bloques libres que ofrece el backend (mismo valor por defecto
     que ``turno_service.DURACION_BLOQUE_MIN``). */
  const DURACION_BLOQUE = 30;

  /* Refresco automático suave: la agenda se vuelve a leer para ver los cambios
     que haya hecho la secretaría desde el panel. */
  const REFRESCO_MS = 60000;

  /* Duración con la que se agenda si el usuario no elige otra cosa. */
  const DURACION_MIN = 30;

  /* Duraciones ofrecidas en la hoja de turno (el bloque elegido puede agregar
     una temporal, p. ej. 25 minutos, que se limpia al cerrar). */
  const DURACIONES = [15, 20, 30, 45, 60];

  /* Refresco máximo que se le pide a ``GET /turnos`` para una semana: siete días
     de agenda entran de sobra (el backend permite hasta 500). */
  const LIMITE_TURNOS = 300;

  const state = {
    doctores: [],   // GET /doctores?activos=true
    pacientes: [],  // GET /pacientes (lista completa: alimenta el buscador)
    turnosHoy: [],  // GET /turnos?fecha_desde=hoy&fecha_hasta=hoy (todos los doctores)
    turnosSemana: [], // turnos del odontólogo en la semana de ``fecha``
    turnosDia: [],  // turnos del odontólogo elegido para ``fecha`` (incluye cancelados)
    bloques: [],    // bloques libres del día (GET …/horarios-disponibles)
    origen: "SEMANAL", // SEMANAL | ESPECIFICO (el día tiene excepciones)
    fecha: "",      // día que se está consultando (ISO, hora local)
    doctorId: null,
    turnoEditId: null,   // turno que se está editando en la hoja
    pacienteId: null,    // ficha elegida para el turno en curso
    pacienteNuevo: false, // la hoja está cargando un paciente nuevo
    refrescoId: null,
    eventosListos: false,
    // Módulo 2: paciente cuya historia clínica se está viendo (null = agenda).
    histPaciente: null,
  };

  /* ------------------------------ Utilidades ----------------------------- */
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function isoDe(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${dd}`;
  }
  function hoyISO() { return isoDe(new Date()); }
  function parteFecha(iso) { return String(iso || "").slice(0, 10); }
  function parteHora(iso) { return String(iso || "").slice(11, 16); }
  function fechaLarga(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("es-AR", {
      weekday: "long", day: "numeric", month: "long", year: "numeric",
    });
  }
  /* ---------------------- Utilidades de calendario ------------------------ */
  /* Todas trabajan con la fecha *local* del consultorio (``YYYY-MM-DD``), que es
     el mismo criterio con el que el backend delimita los días. */
  function fechaDe(iso) {
    const [y, m, d] = parteFecha(iso).split("-").map(Number);
    return new Date(y, (m || 1) - 1, d || 1);
  }
  function sumarDias(iso, dias) {
    const d = fechaDe(iso);
    d.setDate(d.getDate() + Number(dias));
    return isoDe(d);
  }
  /* La semana de la tira arranca el lunes (convención del consultorio). */
  function inicioSemana(iso) {
    const d = fechaDe(iso);
    return sumarDias(iso, -((d.getDay() + 6) % 7));
  }
  function fechaCorta(iso) {
    return fechaDe(iso).toLocaleDateString("es-AR", { day: "2-digit", month: "2-digit" });
  }
  function nombreDiaCorto(iso) {
    return fechaDe(iso).toLocaleDateString("es-AR", { weekday: "short" }).replace(".", "");
  }
  function diasDeDiferencia(iso) {
    const hoy = fechaDe(hoyISO()).getTime();
    return Math.round((fechaDe(iso).getTime() - hoy) / 86400000);
  }
  /* «Hoy», «Mañana»… para que la cabecera diga de un vistazo qué día se ve. */
  function etiquetaRelativa(iso) {
    const n = diasDeDiferencia(iso);
    if (n === 0) return "Hoy";
    if (n === 1) return "Mañana";
    if (n === -1) return "Ayer";
    if (n > 0) return n < 7 ? `En ${n} días` : "Fecha futura";
    return "Fecha pasada";
  }
  function minutosEntre(inicio, fin) {
    const a = new Date(inicio).getTime();
    const b = new Date(fin).getTime();
    return Math.max(5, Math.round((b - a) / 60000) || DURACION_MIN);
  }
  function sumarMinutos(hora, minutos) {
    const [h, m] = String(hora || "00:00").split(":").map(Number);
    const total = (h * 60 + m + Number(minutos)) % (24 * 60);
    return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
  }
  /* ``datetime`` naive con el formato que espera la API (``AAAA-MM-DDTHH:MM:SS``). */
  function isoLocal(d) {
    return `${isoDe(d)}T${String(d.getHours()).padStart(2, "0")}:` +
      `${String(d.getMinutes()).padStart(2, "0")}:00`;
  }
  /* Inicio y fin del turno a partir del día, la hora y la duración elegidos. Se
     calcula con ``Date`` para que un turno que cruza la medianoche termine en el
     día siguiente (y no en una hora anterior al inicio). */
  function isoInicioFin(fecha, hora, minutos) {
    const [h, m] = String(hora || "00:00").split(":").map(Number);
    const inicio = new Date(fechaDe(fecha).getTime() + ((h || 0) * 60 + (m || 0)) * 60000);
    const fin = new Date(inicio.getTime() + Number(minutos || DURACION_MIN) * 60000);
    return { inicio: isoLocal(inicio), fin: isoLocal(fin) };
  }
  /* Búsqueda tolerante: sin mayúsculas ni acentos (para el buscador de fichas). */
  function normalizar(texto) {
    return String(texto ?? "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  }
  function porId(lista, id) { return lista.find((x) => x.id === Number(id)) || null; }
  function iniciales(nombre) {
    const partes = String(nombre || "?").trim().split(/\s+/).filter(Boolean);
    if (!partes.length) return "?";
    if (partes.length === 1) return partes[0].slice(0, 2).toUpperCase();
    return (partes[0][0] + partes[partes.length - 1][0]).toUpperCase();
  }

  /* La paleta de odontólogos es la misma del panel: el color del profesional
     no cambia al pasar del celular a la computadora. */
  const PALETA = ["#0d8578", "#3b82f6", "#8b5cf6", "#f59e0b", "#ef4444", "#0ea5e9", "#ec4899", "#14b8a6"];
  function colorDe(id) { return PALETA[Math.abs(Number(id) || 0) % PALETA.length]; }

  const ESTADO_TURNO = {
    PENDIENTE: { texto: "Pendiente", clase: "badge-amber" },
    CONFIRMADO: { texto: "Confirmado", clase: "badge-green" },
    CANCELADO: { texto: "Cancelado", clase: "badge-red" },
  };
  const ESTADO_WA = {
    PENDIENTE_ENVIO: { texto: "Aviso sin enviar", clase: "badge-slate" },
    ENVIADO: { texto: "Aviso enviado", clase: "badge-amber" },
    FALLIDO: { texto: "Falló el aviso", clase: "badge-red" },
    RESPONDIDO: { texto: "Respondió", clase: "badge-green" },
  };

  /* ------------------------- Preferencias del celular -------------------- */
  function leerDoctorElegido() {
    try {
      const valor = window.localStorage.getItem(CLAVE_DOCTOR);
      return valor ? Number(valor) : null;
    } catch (_) { return null; }
  }
  function guardarDoctorElegido(id) {
    try { window.localStorage.setItem(CLAVE_DOCTOR, String(id)); } catch (_) {}
  }
  function olvidarDoctorElegido() {
    try { window.localStorage.removeItem(CLAVE_DOCTOR); } catch (_) {}
  }
  /* ----------------------------- Cliente HTTP ---------------------------- */
  async function api(path, options) {
    const opts = Object.assign({ headers: { "Content-Type": "application/json" } }, options || {});
    const res = await fetch(API + path, opts);
    const texto = await res.text();
    let data = null;
    if (texto) { try { data = JSON.parse(texto); } catch (_) { data = texto; } }
    if (!res.ok) {
      // El error conserva el status y el cuerpo: la agenda distingue el 409 de
      // solapamiento del odontólogo del 400 de paciente ya agendado en otra
      // agenda, igual que el modal del panel principal.
      const err = new Error(mensajeError(data, res.status));
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function mensajeError(data, status) {
    if (status === 405) {
      return "El servidor no admite esta operación (HTTP 405). Reiniciá el backend y volvé a intentar.";
    }
    if (data && typeof data === "object" && data.detail !== undefined) {
      const d = data.detail;
      if (typeof d === "string") return d;
      if (Array.isArray(d)) {
        return d.map(function (e) {
          const loc = Array.isArray(e.loc) ? e.loc.filter((x) => x !== "body") : [];
          const campo = loc.join(".");
          return campo ? `${campo}: ${e.msg}` : (e.msg || "Dato inválido");
        }).join(" · ");
      }
    }
    return `Error ${status}`;
  }

  /* -------------------------------- Iconos ------------------------------- */
  function svg(contenido, clase) {
    return `<svg class="${clase || "w-5 h-5"}" viewBox="0 0 24 24" fill="none" ` +
      `stroke="currentColor" stroke-width="2" stroke-linecap="round" ` +
      `stroke-linejoin="round" aria-hidden="true">${contenido}</svg>`;
  }
  const ICON = {
    cesto: svg('<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v5M14 11v5"/>'),
    mas: svg('<path d="M12 5v14M5 12h14"/>'),
    check: svg('<path d="M20 6 9 17l-5-5"/>', "w-4 h-4"),
    reloj: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', "w-4 h-4"),
    alerta: svg('<circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/>', "w-4 h-4"),
    calendario: svg('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>'),
    relojGrande: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', "w-9 h-9"),
    lapiz: svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>'),
    buscar: svg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
  };

  /* -------------------------------- Toasts ------------------------------- */
  function toast(tipo, titulo, mensaje) {
    const cont = $("toasts");
    const estilos = {
      exito: { borde: "#10b981", bg: "#ecfdf5", color: "#047857", icono: ICON.check },
      error: { borde: "#ef4444", bg: "#fef2f2", color: "#b91c1c", icono: ICON.alerta },
      info: { borde: "#0ea5e9", bg: "#eff6ff", color: "#0369a1", icono: ICON.reloj },
    };
    const e = estilos[tipo] || estilos.info;
    const el = document.createElement("div");
    el.className = "toast";
    el.style.borderLeft = `4px solid ${e.borde}`;
    el.innerHTML =
      `<span class="w-8 h-8 rounded-full flex items-center justify-center flex-none" ` +
      `style="background:${e.bg};color:${e.color}">${e.icono}</span>` +
      `<div class="flex-1 min-w-0">` +
      `<p class="text-sm font-bold text-slate-900">${esc(titulo)}</p>` +
      (mensaje ? `<p class="text-xs text-slate-500 mt-0.5 break-words">${esc(mensaje)}</p>` : "") +
      `</div>`;
    cont.appendChild(el);
    setTimeout(function () {
      el.classList.add("leaving");
      setTimeout(function () { el.remove(); }, 260);
    }, 4200);
  }
  /* --------------------------- Render de apoyo --------------------------- */
  function pacienteDe(id) { return porId(state.pacientes, id); }
  function nombrePaciente(id) {
    const p = pacienteDe(id);
    return p ? p.nombre : `Paciente #${id}`;
  }
  function badge(meta) {
    return `<span class="badge ${meta.clase}">${esc(meta.texto)}</span>`;
  }
  function badgeTurno(estado) {
    return badge(ESTADO_TURNO[estado] || { texto: estado, clase: "badge-slate" });
  }
  function badgeWhatsApp(t) {
    if (t.estado_whatsapp === "RESPONDIDO") {
      return t.estado_turno === "CANCELADO"
        ? badge({ texto: "Respondió: canceló", clase: "badge-red" })
        : badge({ texto: "Respondió: confirmó", clase: "badge-green" });
    }
    return badge(ESTADO_WA[t.estado_whatsapp] || { texto: t.estado_whatsapp, clase: "badge-slate" });
  }
  function vacioHTML(titulo, sub, icono) {
    return (
      `<div class="m-vacio">` + (icono || ICON.relojGrande) +
      `<p class="m-vacio-titulo">${esc(titulo)}</p>` +
      (sub ? `<p class="m-vacio-sub">${esc(sub)}</p>` : "") +
      `</div>`
    );
  }
  function cargandoHTML(texto) {
    return `<p class="m-cargando">${esc(texto || "Cargando la agenda…")}</p>`;
  }

  /* Turno ocupado: paciente, horario, estado y los botones táctiles de edición
     (lápiz) y cancelación (cesto). El lápiz también aparece en los cancelados:
     es la vía para corregir o reactivar un turno dado de baja. */
  function turnoHTML(t) {
    const paciente = pacienteDe(t.paciente_id);
    const nombre = nombrePaciente(t.paciente_id);
    const cancelado = t.estado_turno === "CANCELADO";
    const clase = cancelado
      ? "m-item m-turno m-cancelado"
      : t.estado_turno === "CONFIRMADO"
        ? "m-item m-turno m-confirmado"
        : "m-item m-turno m-pendiente";
    const detalle =
      `${esc(parteHora(t.fecha_hora_inicio))} a ${esc(parteHora(t.fecha_hora_fin))}` +
      (paciente && paciente.telefono ? ` · ${esc(paciente.telefono)}` : "");
    const dni = paciente && paciente.dni ? `<p class="m-detalle">DNI ${esc(paciente.dni)}</p>` : "";
    const cancelar = cancelado
      ? ""
      : `<button type="button" class="m-cesto" data-cancelar="${t.id}" ` +
        `aria-label="Cancelar el turno de ${esc(nombre)}">${ICON.cesto}</button>`;
    /* Confirmar la asistencia en un toque: sólo mientras el turno está PENDIENTE
       (el confirmado ya está en verde y el cancelado no se atiende). */
    const confirmar =
      t.estado_turno === "PENDIENTE"
        ? `<button type="button" class="m-check" data-confirmar="${t.id}" ` +
          `aria-label="Confirmar la asistencia de ${esc(nombre)}" ` +
          `title="Confirmar asistencia">${ICON.check}</button>`
        : "";
    return (
      `<article class="${clase}" style="--m-color:${colorDe(t.doctor_id)}">` +
      `<span class="m-hora">${esc(parteHora(t.fecha_hora_inicio))}</span>` +
      `<div class="m-item-cuerpo">` +
      `<p class="m-paciente">${esc(nombre)}</p>` +
      `<p class="m-detalle">${detalle}</p>` + dni +
      `<div class="m-item-badges">${badgeTurno(t.estado_turno)}${badgeWhatsApp(t)}</div>` +
      `</div>` +
      `<div class="m-acciones-turno">` +
      confirmar +
      `<button type="button" class="m-editar" data-editar="${t.id}" ` +
      `aria-label="Editar el turno de ${esc(nombre)}" title="Editar turno">${ICON.lapiz}</button>` +
      cancelar +
      `</div>` +
      `</article>`
    );
  }

  /* Horario disponible: todo el bloque es un botón para agendar. */
  function bloqueHTML(b) {
    return (
      `<button type="button" class="m-item m-libre" data-agendar="${esc(b.hora_inicio)}" ` +
      `data-fin="${esc(b.hora_fin)}" aria-label="Agendar un turno a las ${esc(b.hora_inicio)}">` +
      `<span class="m-hora m-hora-libre">${esc(b.hora_inicio)}</span>` +
      `<span class="m-item-cuerpo">` +
      `<span class="m-paciente">Horario disponible</span>` +
      `<span class="m-detalle">${esc(b.hora_inicio)} a ${esc(b.hora_fin)} · tocá para agendar</span>` +
      `</span>` +
      `<span class="m-mas">${ICON.mas}</span>` +
      `</button>`
    );
  }

  /* Cantidad de turnos vigentes de hoy (el “X turnos hoy” del selector).
     Si es el profesional que está mirando la agenda y la fecha visible es hoy, lo
     recién cargado —que incluye lo agendado o cancelado desde el celular— es la
     fuente más fresca; si no, se usa el listado del día traído al iniciar. */
  function turnosDeHoy(doctorId) {
    const id = Number(doctorId);
    const fuente =
      state.fecha === hoyISO() && state.doctorId === id ? state.turnosDia : state.turnosHoy;
    return fuente.filter((t) => t.doctor_id === id && t.estado_turno !== "CANCELADO");
  }

  /* --------------------- Render · selector de profesional ----------------- */
  function renderProfesionales() {
    const cont = $("lista-profesionales");
    if (!state.doctores.length) {
      cont.innerHTML = vacioHTML(
        "No hay odontólogos activos",
        "Cargá profesionales desde el panel de la secretaría y volvé a tocar Actualizar.",
        ICON.alerta
      );
      return;
    }
    cont.innerHTML = state.doctores.map(function (d) {
      const color = colorDe(d.id);
      const cuantos = turnosDeHoy(d.id).length;
      const meta = cuantos === 1 ? "1 turno hoy" : `${cuantos} turnos hoy`;
      return (
        `<button type="button" class="m-doctor-card" data-doctor="${d.id}" style="--m-color:${color}">` +
        `<span class="m-doctor-avatar">${esc(iniciales(d.nombre))}</span>` +
        `<span class="m-doctor-datos">` +
        `<span class="m-doctor-nombre">${esc(d.nombre)}</span>` +
        (d.especialidad ? `<span class="m-doctor-esp">${esc(d.especialidad)}</span>` : "") +
        `<span class="m-doctor-meta">${esc(meta)}</span>` +
        `</span>` +
        `<span class="m-doctor-flecha" aria-hidden="true">&rsaquo;</span>` +
        `</button>`
      );
    }).join("");
  }
  /* ------------------------ Render · cabecera del día -------------------- */
  function renderCabecera(doctor, fecha) {
    if (!doctor) return;
    const avatar = $("m-doctor-avatar");
    avatar.textContent = iniciales(doctor.nombre);
    avatar.style.background = colorDe(doctor.id);
    $("m-doctor-nombre").textContent = doctor.nombre;
    $("m-doctor-especialidad").textContent = doctor.especialidad || "Odontología";
    $("m-fecha").textContent = fechaLarga(fecha);
    // «Hoy», «Mañana»… o el aviso de que se está mirando otro día del calendario.
    $("m-fecha-extra").textContent = etiquetaRelativa(fecha);
    const input = $("input-fecha");
    if (input) input.value = fecha;
  }

  /* ------------------- Render · tira de la semana (7 días) ---------------- */
  /* Un botón por día de la semana visible, con la cantidad de turnos vigentes:
     es el “calendario” del celular, con un toque para saltar a cualquier día. */
  function renderSemana() {
    const cont = $("tira-semana");
    if (!cont) return;
    const inicio = inicioSemana(state.fecha);
    const hoy = hoyISO();
    const chips = [];
    for (let i = 0; i < 7; i += 1) {
      const iso = sumarDias(inicio, i);
      const cuantos = turnosVigentesDe(iso).length;
      const clases = ["m-dia-chip"];
      if (iso === hoy) clases.push("m-dia-chip-hoy");
      if (iso === state.fecha) clases.push("m-dia-chip-activo");
      if (cuantos) clases.push("m-dia-chip-con-turnos");
      chips.push(
        `<button type="button" class="${clases.join(" ")}" data-fecha="${iso}" ` +
        `aria-pressed="${iso === state.fecha}" ` +
        `aria-label="${esc(fechaLarga(iso))}: ${cuantos} turno${cuantos === 1 ? "" : "s"}">` +
        `<span class="m-dia-chip-nombre">${esc(nombreDiaCorto(iso))}</span>` +
        `<span class="m-dia-chip-numero">${fechaDe(iso).getDate()}</span>` +
        (cuantos ? `<span class="m-dia-chip-punto">${cuantos}</span>` : "") +
        `</button>`
      );
    }
    cont.innerHTML = chips.join("");
  }

  /* Turnos (sin cancelados) del odontólogo elegido en la fecha indicada, según
     la semana que ya se trajo de la API. */
  function turnosVigentesDe(iso) {
    return state.turnosSemana.filter(
      (t) => t.estado_turno !== "CANCELADO" && parteFecha(t.fecha_hora_inicio) === iso
    );
  }

  /* -------------------------- Render · agenda del día --------------------- */
  function renderAgenda() {
    const ocupados = state.turnosDia.filter((t) => t.estado_turno !== "CANCELADO");
    const cancelados = state.turnosDia.filter((t) => t.estado_turno === "CANCELADO");
    const confirmados = ocupados.filter((t) => t.estado_turno === "CONFIRMADO").length;

    /* Resumen legible: turnos, confirmados y horarios todavía libres. */
    const partes = [
      ocupados.length === 1 ? "1 turno" : `${ocupados.length} turnos`,
      confirmados ? `${confirmados} confirmado${confirmados === 1 ? "" : "s"}` : "",
      state.bloques.length === 1 ? "1 horario libre" : `${state.bloques.length} horarios libres`,
    ].filter(Boolean);
    $("m-resumen").textContent = partes.join(" · ");

    const origen = $("m-origen");
    origen.classList.remove("hide");
    origen.textContent = state.origen === "ESPECIFICO" ? "Día personalizado" : "Agenda habitual";
    origen.title = state.origen === "ESPECIFICO"
      ? "El día tiene una agenda propia cargada desde el panel de la secretaría."
      : "El día se rige por la agenda semanal del odontólogo.";

    /* Una sola línea de tiempo: se intercalan los turnos ocupados con los
       bloques libres, ordenados por hora (HH:MM compara bien como texto). */
    const items = [];
    ocupados.forEach((t) => items.push({ hora: parteHora(t.fecha_hora_inicio), html: turnoHTML(t) }));
    state.bloques.forEach((b) => items.push({ hora: b.hora_inicio, html: bloqueHTML(b) }));
    items.sort((a, b) => a.hora.localeCompare(b.hora));

    const esHoy = state.fecha === hoyISO();
    $("agenda-lista").innerHTML = items.length
      ? items.map((i) => i.html).join("")
      : vacioHTML(
          "Sin turnos ni horarios libres",
          esHoy
            ? "Hoy no tenés atención configurada en la agenda o ya pasó el último horario disponible."
            : `${etiquetaRelativa(state.fecha).toLowerCase()} (${fechaLarga(state.fecha)}) no tenés atención configurada: probá con otro día de la semana.`,
          ICON.calendario
        );

    const bloqueCancelados = $("agenda-cancelados");
    bloqueCancelados.classList.toggle("hide", !cancelados.length);
    $("lista-cancelados").innerHTML = cancelados.map((t) => turnoHTML(t)).join("");

    // La tira de la semana se pinta con los mismos datos recién traídos.
    renderSemana();
  }

  /* --------------------------- Pantallas y refresco ----------------------- */
  function mostrarAgenda() {
    ocultarAlertaHistoria();
    $("pantalla-profesional").classList.add("hide");
    $("pantalla-agenda").classList.remove("hide");
    $("m-header").classList.remove("hide");
    iniciarRefresco();
    cargarAgenda(false);
  }

  function mostrarSelector() {
    detenerRefresco();
    $("pantalla-agenda").classList.add("hide");
    $("m-header").classList.add("hide");
    $("pantalla-profesional").classList.remove("hide");
  }

  function iniciarRefresco() {
    if (state.refrescoId) return;
    state.refrescoId = setInterval(function () {
      if (document.hidden || $("pantalla-agenda").classList.contains("hide")) return;
      cargarAgenda(true);
    }, REFRESCO_MS);
  }
  function detenerRefresco() {
    if (state.refrescoId) { clearInterval(state.refrescoId); state.refrescoId = null; }
  }

  /* ------------------------------ Datos de la API ------------------------- */
  async function cargarDatosBase() {
    const hoy = hoyISO();
    const [doctores, pacientes, turnosHoy] = await Promise.all([
      api("/doctores?activos=true"),
      // Lista completa de fichas: es la que alimenta el buscador de pacientes.
      api("/pacientes"),
      // Turnos de hoy de todos los profesionales (contador del selector).
      api(`/turnos?fecha_desde=${hoy}&fecha_hasta=${hoy}&limit=${LIMITE_TURNOS}`),
    ]);
    state.doctores = Array.isArray(doctores) ? doctores : [];
    state.pacientes = Array.isArray(pacientes) ? pacientes : [];
    state.turnosHoy = Array.isArray(turnosHoy) ? turnosHoy : [];
    if (!state.fecha) state.fecha = hoy;
  }

  function doctorActual() { return porId(state.doctores, state.doctorId); }

  async function cargarAgenda(silencioso) {
    const doctor = doctorActual();
    if (!doctor) { mostrarSelector(); renderProfesionales(); return; }
    if (!state.fecha) state.fecha = hoyISO();

    const fecha = state.fecha;
    const desde = inicioSemana(fecha);
    const hasta = sumarDias(desde, 6);
    const lista = $("agenda-lista");
    const ico = $("ico-refrescar");
    renderCabecera(doctor, fecha);
    if (ico) ico.classList.add("girando");
    if (!silencioso) lista.innerHTML = cargandoHTML();

    try {
      const [semana, disponibilidad] = await Promise.all([
        // Una sola lectura cubre el día visible y los siete días de la tira.
        api(`/turnos?doctor_id=${doctor.id}` +
            `&fecha_desde=${encodeURIComponent(desde)}` +
            `&fecha_hasta=${encodeURIComponent(hasta)}&limit=${LIMITE_TURNOS}`),
        api(`/doctores/${doctor.id}/horarios-disponibles` +
            `?fecha=${encodeURIComponent(fecha)}&duracion_minutos=${DURACION_BLOQUE}`),
      ]);
      state.turnosSemana = Array.isArray(semana) ? semana : [];
      state.turnosDia = state.turnosSemana.filter(
        (t) => parteFecha(t.fecha_hora_inicio) === fecha
      );
      state.bloques = Array.isArray(disponibilidad.bloques) ? disponibilidad.bloques : [];
      state.origen = disponibilidad.origen || "SEMANAL";
      // El selector de profesionales muestra “X turnos hoy”: lo recién leído del
      // profesional que está mirando el celular reemplaza lo traído al iniciar.
      if (fecha === hoyISO()) {
        state.turnosHoy = state.turnosHoy
          .filter((t) => t.doctor_id !== doctor.id)
          .concat(state.turnosDia);
      }
      renderAgenda();
    } catch (err) {
      renderSemana();
      lista.innerHTML = vacioHTML("No se pudo cargar tu agenda", err.message, ICON.alerta);
      toast("error", "No se pudo cargar la agenda", err.message);
    } finally {
      if (ico) ico.classList.remove("girando");
    }
  }
  /* ---------------------- Acciones · selección y cambio ------------------- */
  function seleccionarDoctor(id) {
    const doctor = porId(state.doctores, id);
    if (!doctor) return;
    state.doctorId = doctor.id;
    state.fecha = hoyISO();   // siempre se entra mirando el día de hoy
    guardarDoctorElegido(doctor.id); // el celular recuerda quién atiende
    toast("info", `Hola, ${doctor.nombre}`, "Esta es tu agenda del día.");
    mostrarAgenda();
  }

  function cambiarMedico() {
    olvidarDoctorElegido();
    state.doctorId = null;
    state.fecha = hoyISO();
    state.turnosDia = [];
    state.turnosSemana = [];
    state.bloques = [];
    mostrarSelector();
    renderProfesionales();
  }

  /* ------------------------- Navegación del calendario -------------------- */
  /* Cualquier día del calendario: día a día, de semana en semana, «Hoy» o
     salto directo con el selector de fecha. */
  function irAFecha(iso) {
    const destino = parteFecha(iso);
    if (!destino || destino === state.fecha) return;
    state.fecha = destino;
    cargarAgenda(false);
  }
  function moverDias(dias) { irAFecha(sumarDias(state.fecha || hoyISO(), dias)); }
  function irAHoy() { irAFecha(hoyISO()); }

  /* ================== Módulo 2 · Historia clínica del paciente ============= */
  /* El odontólogo abre la ficha desde el encabezado, busca al paciente por
     nombre o DNI (la búsqueda la resuelve el backend: ignora acentos y los
     puntos del documento) y ve su línea de tiempo —de la atención más nueva a
     la más vieja— con el doctor que atendió en cada una.

     La evolución se **crea** con ``POST``; no hay forma de editarla ni de
     borrarla (la API no expone esos métodos y el ORM los bloquea). */
  let histTimer = null;

  function abrirHojaHistoria() {
    const buscador = $("hist-buscar");
    if (buscador) buscador.value = "";
    resultadosHistoria([]);
    abrirHoja("hoja-historia");
    if (buscador && buscador.focus) setTimeout(function () { buscador.focus(); }, 200);
  }

  /* Resultados de la búsqueda en la hoja (fichas tocables). */
  function resultadosHistoria(pacientes) {
    const cont = $("hist-resultados");
    if (!cont) return;
    if (!pacientes.length) {
      cont.innerHTML =
        `<p class="m-ficha-nota">Escribí al menos dos letras del nombre, o el DNI, y elegí la ficha.</p>`;
      return;
    }
    cont.innerHTML = pacientes.map(fichaHistoriaHTML).join("");
  }

  function fichaHistoriaHTML(p) {
    return (
      `<button type="button" class="m-ficha" data-hist-paciente="${p.id}" role="option">` +
      `<span class="m-ficha-avatar">${esc(iniciales(p.nombre))}</span>` +
      `<span class="m-ficha-datos">` +
      `<span class="m-ficha-nombre">${esc(p.nombre)}</span>` +
      `<span class="m-ficha-detalle">${esc(detallePaciente(p) || "Sin datos extra")}</span>` +
      `</span></button>`
    );
  }

  /* Búsqueda de pacientes: ``GET /pacientes?buscar=`` (nombre sin acentos o DNI
     parcial). Se espera un instante para no disparar una consulta por tecla. */
  function programarBusquedaHistoria(texto) {
    if (histTimer) clearTimeout(histTimer);
    histTimer = setTimeout(function () { buscarHistoria(texto); }, 220);
  }

  async function buscarHistoria(texto) {
    const cont = $("hist-resultados");
    if (!cont) return;
    const consulta = String(texto || "").trim();
    if (consulta.length < 2) { resultadosHistoria([]); return; }
    cont.innerHTML = `<p class="m-ficha-nota">Buscando “${esc(consulta)}”…</p>`;
    try {
      const pacientes = await api(`/pacientes?buscar=${encodeURIComponent(consulta)}`);
      // Se muestran los primeros 20: alcanza para elegir sin llenar la pantalla.
      resultadosHistoria((Array.isArray(pacientes) ? pacientes : []).slice(0, 20));
    } catch (err) {
      cont.innerHTML = `<p class="m-ficha-nota">${esc(err.message)}</p>`;
    }
  }

  /* ================= Módulo 4 · Ficha de salud (sólo lectura) ============== */
  /* La anamnesis que completa el paciente desde el enlace público se muestra
     antes del formulario de evolución. Si declaró antecedentes de riesgo, se
     enciende un banner flotante que el odontólogo puede cerrar. */

  function fechaHoraSalud(iso) {
    return `${fechaLarga(parteFecha(iso))} · ${parteHora(iso)} hs`;
  }

  function saludTexto(valor, vacio) {
    const limpio = String(valor || "").trim();
    return limpio ? esc(limpio) : `<span class="salud-sin-dato">${esc(vacio || "No informado")}</span>`;
  }

  function saludBooleano(valor) {
    return valor
      ? `<span class="salud-booleano salud-booleano-si">Sí</span>`
      : `<span class="salud-booleano">No</span>`;
  }

  function antecedentesSaludHTML(detalle) {
    const a = detalle && detalle.antecedentes_salud;
    if (!a) {
      return (
        `<section class="salud-card salud-card-pendiente">` +
        `<div class="salud-cabecera">` +
        `<span class="salud-icono">` +
        `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">` +
        `<path d="M9 4H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2"/><path d="M9 4V3h6v1M12 10v6M9 13h6"/></svg>` +
        `</span>` +
        `<div class="salud-cabecera-texto">` +
        `<p class="salud-titulo">Ficha de salud</p>` +
        `<p class="salud-sub">Anamnesis pendiente del paciente.</p>` +
        `</div>` +
        `<span class="badge badge-slate">Pendiente</span>` +
        `</div>` +
        `<p class="salud-pendiente">Se completa desde el enlace que el paciente recibe por WhatsApp. Cuando la envíe, sus antecedentes aparecen acá.</p>` +
        `</section>`
      );
    }

    const motivos = Array.isArray(detalle.motivos_alerta)
      ? detalle.motivos_alerta
      : (Array.isArray(a.motivos_alerta) ? a.motivos_alerta : []);
    const alerta = Boolean(detalle.alerta_medica ?? a.alerta_medica ?? motivos.length);
    const actualizada = a.fecha_completado
      ? `<p class="salud-sub">Actualizada el ${esc(fechaHoraSalud(a.fecha_completado))}</p>`
      : "";

    return (
      `<section class="salud-card${alerta ? " salud-card-alerta" : ""}">` +
      `<div class="salud-cabecera">` +
      `<span class="salud-icono">` +
      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">` +
      `<path d="M9 4H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2"/><path d="M9 4V3h6v1M12 10v6M9 13h6"/></svg>` +
      `</span>` +
      `<div class="salud-cabecera-texto">` +
      `<p class="salud-titulo">Ficha de salud</p>` +
      actualizada +
      `</div>` +
      `<span class="badge ${alerta ? "badge-red" : "badge-green"}">${alerta ? "Alerta médica" : "Sin alertas"}</span>` +
      `</div>` +
      `<div class="salud-grid">` +
      `<div class="salud-dato salud-dato-ancho"><p class="salud-dato-titulo">Alergias a medicamentos</p>` +
      `<p class="salud-dato-texto">${saludTexto(a.alergias_medicamentos, "Sin alergias informadas")}</p></div>` +
      `<div class="salud-dato"><p class="salud-dato-titulo">Presión arterial alta</p>` +
      `<p class="salud-dato-texto">${saludBooleano(a.presion_alta)}</p></div>` +
      `<div class="salud-dato"><p class="salud-dato-titulo">Problemas cardíacos</p>` +
      `<p class="salud-dato-texto">${saludBooleano(a.problemas_cardiacos)}</p></div>` +
      `<div class="salud-dato salud-dato-ancho"><p class="salud-dato-titulo">Medicación actual</p>` +
      `<p class="salud-dato-texto">${saludTexto(a.medicacion_actual, "No informada")}</p></div>` +
      `<div class="salud-dato"><p class="salud-dato-titulo">Embarazo</p>` +
      `<p class="salud-dato-texto">${saludBooleano(a.embarazo)}</p></div>` +
      `</div>` +
      (alerta
        ? `<p class="salud-alerta-nota"><strong>Revisar antes de atender:</strong> ${esc(motivos.join(" · "))}</p>`
        : "") +
      `</section>`
    );
  }

  function ocultarAlertaHistoria() {
    const banner = $("hist-alerta");
    if (banner) banner.classList.add("hide");
  }

  function renderAntecedentes(detalle) {
    const cont = $("hist-antecedentes");
    if (cont) cont.innerHTML = antecedentesSaludHTML(detalle);

    const banner = $("hist-alerta");
    if (!banner) return;
    const motivos = Array.isArray(detalle.motivos_alerta)
      ? detalle.motivos_alerta
      : [];
    if (detalle.alerta_medica && motivos.length) {
      const texto = $("hist-alerta-motivos");
      if (texto) texto.textContent = motivos.join(" · ");
      banner.classList.remove("hide");
    } else {
      banner.classList.add("hide");
    }
  }

  function mostrarHistoria() {
    // Mientras se mira la ficha no hace falta refrescar la agenda cada minuto.
    detenerRefresco();
    $("pantalla-profesional").classList.add("hide");
    $("pantalla-agenda").classList.add("hide");
    $("pantalla-historia").classList.remove("hide");
    $("m-header").classList.remove("hide");
  }

  async function abrirHistoria(pacienteId) {
    const p = porId(state.pacientes, pacienteId) || {
      id: Number(pacienteId),
      nombre: `Paciente #${pacienteId}`,
    };
    state.histPaciente = p.id;
    cerrarHojas();
    mostrarHistoria();
    $("hist-paciente").textContent = p.nombre;
    $("hist-ficha").textContent = detallePaciente(p) || "Sin datos extra";
    const salud = $("hist-antecedentes");
    if (salud) {
      salud.classList.remove("hide");
      salud.innerHTML = `<p class="m-ficha-nota">Cargando la ficha de salud…</p>`;
    }
    ocultarAlertaHistoria();
    const doctor = doctorActual();
    $("evo-doctor-id").value = doctor ? String(doctor.id) : "";
    const fecha = $("evo-fecha");
    if (fecha && !fecha.value) fecha.value = hoyISO();
    await cargarHistoria();
  }

  async function cargarHistoria() {
    const cont = $("hist-timeline");
    if (!state.histPaciente || !cont) return;
    cont.innerHTML = cargandoHTML("Cargando la historia clínica…");
    try {
      const detalle = await api(`/pacientes/${state.histPaciente}/historia-clinica`);
      $("hist-ficha").textContent =
        `Ficha ${detalle.numero_ficha}` +
        (detalle.paciente_dni ? ` · DNI ${detalle.paciente_dni}` : "");
      $("hist-total").textContent = String(detalle.total_evoluciones);
      renderAntecedentes(detalle);
      const evoluciones = Array.isArray(detalle.evoluciones) ? detalle.evoluciones : [];
      cont.innerHTML = evoluciones.length
        ? evoluciones.map(evolucionHTML).join("")
        : vacioHTML(
            "Sin evoluciones registradas",
            "Cargá la primera atención de este paciente con el formulario de arriba.",
            ICON.alerta
          );
    } catch (err) {
      cont.innerHTML = `<p class="m-error">${esc(err.message)}</p>`;
      const salud = $("hist-antecedentes");
      if (salud) salud.innerHTML = `<p class="m-ficha-nota">No se pudo cargar la ficha de salud.</p>`;
      ocultarAlertaHistoria();
    }
  }

  /* Una atención de la línea de tiempo: fecha, quién atendió, los tres campos
     clínicos y los adjuntos (radiografías) enlazados. */
  function evolucionHTML(ev) {
    const campos = [
      ["Motivo de consulta", ev.motivo_consulta],
      ["Diagnóstico", ev.diagnostico],
      ["Tratamiento efectuado", ev.tratamiento_efectuado],
      ["Observaciones", ev.observaciones],
    ].filter(function (campo) { return campo[1]; });
    const archivos = Array.isArray(ev.archivos) ? ev.archivos : [];
    const hora = parteHora(ev.fecha_atencion);
    return (
      `<article class="m-hist-item"><div class="m-hist-cuerpo">` +
      `<p class="m-hist-fecha">${esc(fechaLarga(parteFecha(ev.fecha_atencion)))}` +
      (hora !== "00:00" ? ` · ${esc(hora)} hs` : "") +
      `</p>` +
      `<span class="m-hist-doctor">${esc(ev.doctor_nombre || `Odontólogo #${ev.doctor_id}`)}` +
      (ev.doctor_especialidad ? ` · ${esc(ev.doctor_especialidad)}` : "") +
      `</span>` +
      `<div class="m-hist-campos">` +
      campos
        .map(function (campo) {
          return (
            `<div><p class="m-hist-campo-titulo">${esc(campo[0])}</p>` +
            `<p class="m-hist-campo-texto">${esc(campo[1])}</p></div>`
          );
        })
        .join("") +
      `</div>` +
      (archivos.length
        ? `<div class="m-hist-archivos">` +
          archivos
            .map(function (a) {
              return (
                `<a class="m-hist-archivo" href="${esc(a.url_archivo)}" ` +
                `target="_blank" rel="noopener">${esc(a.tipo_archivo)}</a>`
              );
            })
            .join("") +
          `</div>`
        : "") +
      `</div></article>`
    );
  }

  function mostrarErrorEvolucion(mensaje) {
    const caja = $("evo-error");
    if (!caja) return;
    caja.textContent = mensaje || "";
    caja.classList.toggle("hide", !mensaje);
  }

  /* Alta de la evolución médica (``POST /pacientes/{id}/evoluciones``): el único
     punto de escritura del módulo. La historia clínica se crea sola si el
     paciente todavía no tenía ninguna atención registrada. */
  async function guardarEvolucion(ev) {
    ev.preventDefault();
    const btn = $("btn-guardar-evolucion");
    const doctor = doctorActual();
    if (!state.histPaciente) return;
    if (!doctor) {
      mostrarErrorEvolucion("Elegí primero el odontólogo que atiende en la pantalla inicial.");
      return;
    }
    const motivo = $("evo-motivo").value.trim();
    const diagnostico = $("evo-diagnostico").value.trim();
    const tratamiento = $("evo-tratamiento").value.trim();
    if (!motivo || !diagnostico || !tratamiento) {
      mostrarErrorEvolucion("Completá el motivo de consulta, el diagnóstico y el tratamiento.");
      return;
    }
    const payload = {
      doctor_id: doctor.id,
      motivo_consulta: motivo,
      diagnostico: diagnostico,
      tratamiento_efectuado: tratamiento,
      observaciones: $("evo-observaciones").value.trim() || null,
    };
    // La fecha viaja sólo si el odontólogo registra otro día: cuando la atención
    // es de hoy se usa el momento del alta, así la línea de tiempo guarda la hora.
    const fecha = $("evo-fecha").value;
    if (fecha && fecha !== hoyISO()) payload.fecha_atencion = `${fecha}T00:00:00`;

    try {
      bloquear(btn, true);
      mostrarErrorEvolucion("");
      await api(`/pacientes/${state.histPaciente}/evoluciones`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      $("form-evolucion").reset();
      $("evo-fecha").value = hoyISO();
      toast(
        "exito",
        "Evolución guardada",
        "Quedó registrada en la historia clínica: no se puede editar ni eliminar."
      );
      await cargarHistoria();
    } catch (err) {
      mostrarErrorEvolucion(err.message);
      toast("error", "No se pudo guardar la evolución", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* --------------------------- Hojas deslizantes ------------------------- */
  function abrirHoja(id) {
    $(id).classList.remove("hide");
    document.body.style.overflow = "hidden";
  }
  function cerrarHojas() {
    ["hoja-nuevo-turno", "hoja-cancelar", "hoja-historia"].forEach(function (id) {
      $(id).classList.add("hide");
    });
    document.body.style.overflow = "";
    ocultarErrorNuevo();
    // La hoja vuelve limpia al modo “agendar”: sin turno en edición, sin paciente
    // elegido y con el botón en su texto original.
    state.turnoEditId = null;
    state.pacienteId = null;
    state.pacienteNuevo = false;
    const btn = $("btn-agendar");
    if (btn) delete btn.dataset.txt;
    const form = $("form-nuevo-turno");
    if (form) form.reset();
    limpiarDuraciones();
  }
  function bloquear(btn, on) {
    if (!btn) return;
    if (!btn.dataset.txt) btn.dataset.txt = btn.textContent;
    btn.disabled = Boolean(on);
    btn.textContent = on ? "Guardando…" : btn.dataset.txt;
  }

  /* --------------------------- Cancelar un turno ------------------------- */
  function turnoEnAgenda(id) {
    return state.turnosDia.find((t) => t.id === Number(id)) || null;
  }

  function abrirHojaCancelar(id) {
    const turno = turnoEnAgenda(id);
    if (!turno) return;
    $("cancelar-turno-id").value = String(turno.id);
    $("cancelar-detalle").textContent =
      `${nombrePaciente(turno.paciente_id)} · ${fechaLarga(parteFecha(turno.fecha_hora_inicio))} ` +
      `de ${parteHora(turno.fecha_hora_inicio)} a ${parteHora(turno.fecha_hora_fin)}. ` +
      "El horario vuelve a quedar libre.";
    abrirHoja("hoja-cancelar");
  }

  async function confirmarCancelar() {
    const btn = $("btn-confirmar-cancelar");
    const id = Number($("cancelar-turno-id").value);
    const turno = state.turnosDia.find((t) => t.id === id);
    const detalle = turno
      ? `${nombrePaciente(turno.paciente_id)} · ${parteHora(turno.fecha_hora_inicio)} hs`
      : `Turno #${id}`;
    try {
      bloquear(btn, true);
      // Mismo endpoint que usa el panel principal: PATCH con el estado nuevo.
      await api(`/turnos/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ estado_turno: "CANCELADO" }),
      });
      cerrarHojas();
      toast("exito", "Turno cancelado", detalle);
      await cargarAgenda(true);
    } catch (err) {
      toast("error", "No se pudo cancelar el turno", err.message);
    } finally {
      bloquear(btn, false);
    }
  }
  /* ------------------- Confirmar la asistencia (un toque) ------------------ */
  /* Mismo ``PATCH`` que el botón *Confirmar asistencia* del panel principal:
     deja el turno en verde para el paciente que avisó por teléfono o ya llegó,
     sin esperar la respuesta del recordatorio de WhatsApp. */
  async function confirmarAsistencia(id) {
    const turno = turnoEnAgenda(id);
    const detalle = turno
      ? `${nombrePaciente(turno.paciente_id)} · ${parteHora(turno.fecha_hora_inicio)} hs`
      : `Turno #${id}`;
    try {
      await api(`/turnos/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ estado_turno: "CONFIRMADO" }),
      });
      toast("exito", "Asistencia confirmada", detalle);
      await cargarAgenda(true);
    } catch (err) {
      toast("error", "No se pudo confirmar la asistencia", err.message);
    }
  }

  /* -------------------- Buscador de pacientes (fichas) -------------------- */
  /* ``GET /pacientes`` trae la lista completa: acá se filtra por nombre, DNI o
     teléfono mientras se escribe, sin una request por tecla. Con el campo vacío
     se listan todas las fichas, que es lo que permite elegir al paciente. */
  function pacientesFiltrados(texto) {
    const q = normalizar(texto);
    const lista = q
      ? state.pacientes.filter(function (p) {
          return (
            normalizar(p.nombre).includes(q) ||
            normalizar(p.dni || "").includes(q) ||
            normalizar(p.telefono || "").includes(q)
          );
        })
      : state.pacientes.slice();
    return lista.sort((a, b) => String(a.nombre).localeCompare(String(b.nombre), "es"));
  }

  function detallePaciente(p) {
    return [p.dni ? `DNI ${p.dni}` : "", p.telefono || "", p.obra_social || ""]
      .filter(Boolean)
      .join(" · ");
  }

  function pacienteHTML(p) {
    return (
      `<button type="button" class="m-ficha" data-paciente="${p.id}" role="option">` +
      `<span class="m-ficha-avatar">${esc(iniciales(p.nombre))}</span>` +
      `<span class="m-ficha-datos">` +
      `<span class="m-ficha-nombre">${esc(p.nombre)}</span>` +
      `<span class="m-ficha-detalle">${esc(detallePaciente(p) || "Sin datos extra")}</span>` +
      `</span></button>`
    );
  }

  function renderPacientes() {
    const cont = $("lista-pacientes");
    if (!cont) return;
    const buscador = $("nuevo-buscar");
    const texto = buscador ? buscador.value : "";
    if (!state.pacientes.length) {
      cont.innerHTML =
        `<p class="m-ficha-nota">Todavía no hay fichas cargadas: registrá el paciente nuevo.</p>`;
      return;
    }
    const encontrados = pacientesFiltrados(texto);
    if (!encontrados.length) {
      cont.innerHTML =
        `<p class="m-ficha-nota">Ninguna ficha coincide con “${esc(texto)}”.</p>` +
        `<button type="button" class="m-btn m-btn-ghost m-btn-mini" data-alta-nuevo>` +
        `Registrar “${esc(texto)}” como paciente nuevo</button>`;
      return;
    }
    cont.innerHTML = encontrados.map(pacienteHTML).join("");
  }

  /* Ficha elegida: queda fijada en la hoja y se libera el espacio del buscador. */
  function elegirPaciente(id) {
    const p = porId(state.pacientes, id);
    if (!p) return;
    state.pacienteId = p.id;
    state.pacienteNuevo = false;
    $("elegido-iniciales").textContent = iniciales(p.nombre);
    $("elegido-nombre").textContent = p.nombre;
    $("elegido-detalle").textContent = detallePaciente(p);
    $("paciente-elegido").classList.remove("hide");
    $("bloque-buscar").classList.add("hide");
    $("bloque-paciente-nuevo").classList.add("hide");
  }

  /* Volver a la lista para elegir otra ficha. */
  function mostrarBuscadorPaciente() {
    state.pacienteId = null;
    state.pacienteNuevo = false;
    $("paciente-elegido").classList.add("hide");
    $("bloque-paciente-nuevo").classList.add("hide");
    $("bloque-buscar").classList.remove("hide");
    const buscador = $("nuevo-buscar");
    if (buscador) {
      buscador.value = "";
      renderPacientes();
      if (buscador.focus) setTimeout(function () { buscador.focus(); }, 160);
    }
  }

  /* Alta rápida: el paciente no está en las fichas y se carga en el momento. */
  function mostrarAltaPacienteNuevo(nombreSugerido) {
    state.pacienteId = null;
    state.pacienteNuevo = true;
    $("paciente-elegido").classList.add("hide");
    $("bloque-buscar").classList.add("hide");
    $("bloque-paciente-nuevo").classList.remove("hide");
    const nombre = $("nuevo-nombre");
    if (nombre) {
      if (nombreSugerido) nombre.value = nombreSugerido;
      if (nombre.focus) setTimeout(function () { nombre.focus(); }, 220);
    }
  }

  /* ------------------- Horarios libres del día (atajos) ------------------- */
  /* Se piden al backend para la fecha del formulario: tocar uno llena la hora y
     la duración. Si la consulta falla, el bloque se oculta y el turno se puede
     cargar igual escribiendo la hora a mano. */
  async function cargarLibres(fecha) {
    const caja = $("bloque-libres");
    const cont = $("nuevo-libres");
    const doctor = doctorActual();
    if (!caja || !cont || !doctor || !fecha) return;
    caja.classList.remove("hide");
    cont.innerHTML = `<span class="m-chip-nota">Buscando horarios libres…</span>`;
    try {
      const disp = await api(
        `/doctores/${doctor.id}/horarios-disponibles` +
        `?fecha=${encodeURIComponent(fecha)}&duracion_minutos=${DURACION_BLOQUE}`
      );
      const bloques = Array.isArray(disp.bloques) ? disp.bloques : [];
      cont.innerHTML = bloques.length
        ? bloques
            .map(function (b) {
              return (
                `<button type="button" class="m-chip-hora" data-libre="${esc(b.hora_inicio)}" ` +
                `data-libre-fin="${esc(b.hora_fin)}">${esc(b.hora_inicio)}</button>`
              );
            })
            .join("")
        : `<span class="m-chip-nota">Según la agenda no quedan horarios libres ese día.</span>`;
      marcarChipHora($("nuevo-hora") ? $("nuevo-hora").value : "");
    } catch (_err) {
      caja.classList.add("hide");
    }
  }

  function marcarChipHora(hora) {
    const cont = $("nuevo-libres");
    if (!cont) return;
    Array.from(cont.querySelectorAll(".m-chip-hora")).forEach(function (chip) {
      chip.classList.toggle("m-chip-hora-activo", chip.dataset.libre === hora);
    });
  }

  /* Tocar un horario libre: hora + duración del bloque elegido. */
  function elegirHoraLibre(inicio, fin) {
    const campo = $("nuevo-hora");
    if (campo) campo.value = inicio;
    marcarDuracion(minutosEntre(`2000-01-01T${inicio}:00`, `2000-01-01T${fin}:00`));
    marcarChipHora(inicio);
  }

  /* La duración del bloque puede no estar entre las opciones del selector (p. ej.
     un rango de 25 minutos): se agrega una opción temporal y se limpia al cerrar. */
  function marcarDuracion(minutos) {
    const selector = $("nuevo-duracion");
    if (!selector) return;
    const valor = String(minutos);
    const existe = Array.from(selector.options).some(function (o) { return o.value === valor; });
    if (!existe) {
      const opcion = document.createElement("option");
      opcion.value = valor;
      opcion.textContent = `${minutos} minutos`;
      selector.appendChild(opcion);
    }
    selector.value = valor;
  }

  /* Las opciones de duración agregadas al vuelo (bloques de otro ancho) no deben
     quedar en el selector cuando la hoja se cierra. */
  function limpiarDuraciones() {
    const selector = $("nuevo-duracion");
    if (!selector) return;
    Array.from(selector.options).forEach(function (o) {
      if (!DURACIONES.includes(Number(o.value))) o.remove();
    });
    selector.value = String(DURACION_MIN);
  }

  function mostrarErrorNuevo(mensaje) {
    const caja = $("nuevo-error");
    caja.textContent = mensaje;
    caja.classList.remove("hide");
  }
  function ocultarErrorNuevo() {
    const caja = $("nuevo-error");
    if (!caja) return;
    caja.textContent = "";
    caja.classList.add("hide");
  }

  /* --------------------- Abrir la hoja (agendar / editar) ----------------- */
  function prepararHoja(doctor) {
    ocultarErrorNuevo();
    $("nuevo-doctor-id").value = String(doctor.id);
    const btn = $("btn-agendar");
    if (btn) delete btn.dataset.txt;   // el texto se recalcula según el modo
    $("paciente-elegido").classList.add("hide");
    $("bloque-paciente-nuevo").classList.add("hide");
    $("bloque-buscar").classList.remove("hide");
    const buscador = $("nuevo-buscar");
    if (buscador) buscador.value = "";
    renderPacientes();
    abrirHoja("hoja-nuevo-turno");
  }

  /* Agendar en un horario libre: la fecha es la que se está mirando en la agenda. */
  function abrirHojaNuevoTurno(entrada, salida) {
    const doctor = doctorActual();
    if (!doctor) return;
    const fecha = state.fecha || hoyISO();
    state.turnoEditId = null;
    state.pacienteId = null;
    state.pacienteNuevo = false;
    prepararHoja(doctor);
    $("nuevo-turno-id").value = "";
    $("nuevo-titulo").textContent = "Agendar paciente";
    $("btn-agendar").textContent = "Agendar turno";
    $("bloque-estado").classList.add("hide");
    $("nuevo-fecha").value = fecha;
    $("nuevo-hora").value = entrada;
    marcarDuracion(minutosEntre(`${fecha}T${entrada}:00`, `${fecha}T${salida}:00`));
    $("nuevo-horario").textContent =
      `${fechaLarga(fecha)} de ${entrada} a ${salida} · ${doctor.nombre}`;
    // Con una sola ficha en el sistema no hay nada que elegir: queda puesta.
    if (state.pacientes.length === 1) elegirPaciente(state.pacientes[0].id);
    cargarLibres(fecha);
    const buscador = $("nuevo-buscar");
    if (buscador && buscador.focus && state.pacientes.length) {
      setTimeout(function () { buscador.focus(); }, 220);
    }
  }

  /* El DNI es la clave natural del paciente: si ya existe se reutiliza la ficha
     (el índice único responde 409 si se intenta crear de nuevo) y, si no, se
     da de alta con los datos del formulario móvil. */
  async function resolverPaciente(datos) {
    if (datos.dni) {
      const existentes = await api(`/pacientes?dni=${encodeURIComponent(datos.dni)}`);
      if (Array.isArray(existentes) && existentes.length) {
        toast("info", "Paciente ya registrado", `${existentes[0].nombre} · se usó su ficha.`);
        return existentes[0];
      }
    }
    return api("/pacientes", {
      method: "POST",
      body: JSON.stringify({
        nombre: datos.nombre,
        telefono: datos.telefono,
        dni: datos.dni || null,
      }),
    });
  }

  /* Editar un turno ya asignado: se precargan paciente, día, hora, duración y
     estado (el estado se puede cambiar: confirmar, reprogramar o cancelar). */
  function abrirHojaEditar(id) {
    const doctor = doctorActual();
    const turno = turnoEnAgenda(id);
    if (!doctor || !turno) return;
    const fecha = parteFecha(turno.fecha_hora_inicio);
    state.turnoEditId = turno.id;
    state.pacienteId = null;
    state.pacienteNuevo = false;
    prepararHoja(doctor);
    $("nuevo-turno-id").value = String(turno.id);
    $("nuevo-titulo").textContent = "Editar turno";
    $("btn-agendar").textContent = "Guardar cambios";
    $("bloque-estado").classList.remove("hide");
    $("nuevo-fecha").value = fecha;
    $("nuevo-hora").value = parteHora(turno.fecha_hora_inicio);
    marcarDuracion(minutosEntre(turno.fecha_hora_inicio, turno.fecha_hora_fin));
    $("nuevo-estado").value = turno.estado_turno;
    $("nuevo-horario").textContent =
      `${nombrePaciente(turno.paciente_id)} · ${fechaLarga(fecha)} ` +
      `${parteHora(turno.fecha_hora_inicio)} hs`;
    elegirPaciente(turno.paciente_id);
    cargarLibres(fecha);
  }

  /* Paciente del turno: la ficha elegida de la lista o el alta rápida. Devuelve
     ``null`` (con el error ya mostrado) si falta el dato obligatorio. */
  async function pacienteDelFormulario() {
    if (state.pacienteId) {
      return { id: state.pacienteId, nombre: nombrePaciente(state.pacienteId), nuevo: false };
    }
    const nombre = $("nuevo-nombre").value.trim();
    const telefono = $("nuevo-telefono").value.trim();
    const dni = $("nuevo-dni").value.trim();
    if (!nombre) {
      mostrarErrorNuevo("Elegí un paciente de la lista o escribí el nombre del nuevo.");
      $("nuevo-nombre").focus();
      return null;
    }
    if (telefono.replace(/\D/g, "").length < 6) {
      mostrarErrorNuevo("El teléfono es obligatorio (mínimo 6 dígitos) para poder avisarle.");
      $("nuevo-telefono").focus();
      return null;
    }
    const paciente = await resolverPaciente({ nombre, telefono, dni });
    paciente.nuevo = true;
    return paciente;
  }

  async function guardarTurno(ev) {
    ev.preventDefault();
    const btn = $("btn-agendar");
    const doctorId = Number($("nuevo-doctor-id").value) || state.doctorId;
    const fecha = $("nuevo-fecha").value;
    const hora = $("nuevo-hora").value;
    const dur = Number($("nuevo-duracion").value) || DURACION_MIN;
    const modoEdicion = Boolean(state.turnoEditId);
    ocultarErrorNuevo();

    if (!doctorId || !fecha || !hora) {
      mostrarErrorNuevo("Indicá la fecha y la hora de inicio del turno.");
      return;
    }

    try {
      bloquear(btn, true);
      const paciente = await pacienteDelFormulario();
      if (!paciente) return;
      // La ficha recién creada queda disponible en el buscador al instante.
      if (paciente.nuevo) state.pacientes = state.pacientes.concat([paciente]);

      const rango = isoInicioFin(fecha, hora, dur);
      const payload = {
        paciente_id: paciente.id,
        doctor_id: doctorId,
        // Convención del proyecto: datetime *naive* (sin zona) con el horario
        // del consultorio, igual que lo envía el modal del panel.
        fecha_hora_inicio: rango.inicio,
        fecha_hora_fin: rango.fin,
      };

      const turno = modoEdicion
        ? await api(`/turnos/${state.turnoEditId}`, {
            method: "PATCH",
            // También viaja el estado elegido: así se confirma o se reactiva.
            body: JSON.stringify(Object.assign({ estado_turno: $("nuevo-estado").value }, payload)),
          })
        : await api("/turnos", { method: "POST", body: JSON.stringify(payload) });

      cerrarHojas();
      // Si el turno quedó en otro día, la agenda salta a ese día para verlo.
      state.fecha = parteFecha(turno.fecha_hora_inicio);
      toast(
        "exito",
        modoEdicion ? "Turno actualizado" : "Turno agendado",
        `${nombrePaciente(turno.paciente_id)} · ${fechaLarga(state.fecha)} ` +
        `${parteHora(turno.fecha_hora_inicio)} hs`
      );
      await cargarAgenda(true);
    } catch (err) {
      mostrarErrorNuevo(err.message);
      toast("error", modoEdicion ? "No se pudo actualizar" : "No se pudo agendar", err.message);
      // Un 409 (agenda del odontólogo) o un 400 (paciente citado en otra
      // agenda) significan que el estado cambió: se refresca para no ofrecer
      // un horario que ya no está libre.
      if (err.status === 409 || err.status === 400) await cargarAgenda(true);
    } finally {
      bloquear(btn, false);
    }
  }
  /* ------------------------------- Eventos ------------------------------- */
  function registrarEventos() {
    // Los manejadores se registran una sola vez aunque ``iniciar()`` se vuelva
    // a ejecutar desde el botón *Reintentar*.
    if (state.eventosListos) return;
    state.eventosListos = true;
    /* Delegación de clics: la lista de profesionales, la tira de la semana, los
       bloques libres y los botones de cada turno se redibujan todo el tiempo, así
       que se resuelve el toque por atributos ``data-*`` (igual que calendar.js). */
    document.addEventListener("click", function (e) {
      const objetivo = e.target instanceof Element ? e.target : null;
      if (!objetivo) return;

      if (objetivo.closest("[data-cerrar-hoja]")) { cerrarHojas(); return; }
      if (objetivo.closest("[data-reintentar]")) { iniciar(); return; }
      if (objetivo.closest("[data-cerrar-alerta]")) { ocultarAlertaHistoria(); return; }

      const doctor = objetivo.closest("[data-doctor]");
      if (doctor) { seleccionarDoctor(doctor.dataset.doctor); return; }

      /* Navegación del calendario: un día de la tira o una semana completa. */
      const fecha = objetivo.closest("[data-fecha]");
      if (fecha) { irAFecha(fecha.dataset.fecha); return; }

      const semana = objetivo.closest("[data-semana]");
      if (semana) { moverDias(Number(semana.dataset.semana)); return; }

      const libre = objetivo.closest("[data-agendar]");
      if (libre) { abrirHojaNuevoTurno(libre.dataset.agendar, libre.dataset.fin); return; }

      /* Hoja de turno: fichas, alta rápida y atajos de horario. */
      const ficha = objetivo.closest("[data-paciente]");
      if (ficha) { elegirPaciente(ficha.dataset.paciente); return; }

      /* Hoja de la historia clínica: la ficha elegida abre su línea de tiempo. */
      const fichaHistoria = objetivo.closest("[data-hist-paciente]");
      if (fichaHistoria) { abrirHistoria(fichaHistoria.dataset.histPaciente); return; }

      const alta = objetivo.closest("[data-alta-nuevo]");
      if (alta) { mostrarAltaPacienteNuevo($("nuevo-buscar").value.trim()); return; }

      const chip = objetivo.closest("[data-libre]");
      if (chip) { elegirHoraLibre(chip.dataset.libre, chip.dataset.libreFin); return; }

      const editar = objetivo.closest("[data-editar]");
      if (editar) { abrirHojaEditar(editar.dataset.editar); return; }

      const confirmar = objetivo.closest("[data-confirmar]");
      if (confirmar) { confirmarAsistencia(confirmar.dataset.confirmar); return; }

      const cesto = objetivo.closest("[data-cancelar]");
      if (cesto) abrirHojaCancelar(cesto.dataset.cancelar);
    });

    $("btn-refrescar-agenda").addEventListener("click", function () { cargarAgenda(false); });
    $("btn-cambiar-medico").addEventListener("click", cambiarMedico);
    $("form-nuevo-turno").addEventListener("submit", guardarTurno);
    $("btn-confirmar-cancelar").addEventListener("click", confirmarCancelar);

    /* Módulo 2 · Historia clínica: apertura, búsqueda, vuelta a la agenda y alta
       de la evolución (no hay edición ni borrado: la API no los expone). */
    $("btn-historia-clinica").addEventListener("click", abrirHojaHistoria);
    $("btn-historia-volver").addEventListener("click", mostrarAgenda);
    $("form-evolucion").addEventListener("submit", guardarEvolucion);
    $("hist-buscar").addEventListener("input", function () {
      programarBusquedaHistoria(this.value);
    });

    /* Navegador de fechas: flechas de día y de semana, «Hoy» y selector directo. */
    $("btn-dia-anterior").addEventListener("click", function () { moverDias(-1); });
    $("btn-dia-siguiente").addEventListener("click", function () { moverDias(1); });
    $("btn-hoy").addEventListener("click", irAHoy);
    $("input-fecha").addEventListener("change", function () {
      if (this.value) irAFecha(this.value);
    });

    /* Buscador de pacientes: se filtra mientras se escribe (sin requests). */
    $("nuevo-buscar").addEventListener("input", renderPacientes);
    $("btn-paciente-nuevo").addEventListener("click", function () {
      mostrarAltaPacienteNuevo($("nuevo-buscar").value.trim());
    });
    $("btn-buscar-existente").addEventListener("click", mostrarBuscadorPaciente);
    $("btn-cambiar-paciente").addEventListener("click", mostrarBuscadorPaciente);

    /* Cambiar el día del formulario actualiza los atajos de horarios libres. */
    $("nuevo-fecha").addEventListener("change", function () {
      if (this.value) cargarLibres(this.value);
    });
    $("nuevo-hora").addEventListener("change", function () { marcarChipHora(this.value); });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") cerrarHojas();
    });

    /* Al volver a la app (o desbloquear el celular) se relee la agenda: la
       secretaría pudo haber agendado o cancelado algo mientras tanto. */
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) return;
      if ($("pantalla-agenda").classList.contains("hide")) return;
      cargarAgenda(true);
    });
  }

  /* -------------------------------- Init --------------------------------- */
  async function iniciar() {
    registrarEventos();
    try {
      await cargarDatosBase();
    } catch (err) {
      toast("error", "Sin conexión con el servidor", err.message);
      $("lista-profesionales").innerHTML =
        vacioHTML("No se pudieron cargar los datos", err.message, ICON.alerta) +
        `<button type="button" class="m-btn m-btn-primary" data-reintentar ` +
        `style="margin-top:.7rem">Reintentar</button>`;
      return;
    }

    // El profesional elegido la última vez se recuerda: si sigue activo, se
    // entra directo a su agenda sin pasar por la pantalla de selección.
    const recordado = leerDoctorElegido();
    const doctor = recordado ? porId(state.doctores, recordado) : null;
    if (doctor) {
      state.doctorId = doctor.id;
      mostrarAgenda();
      return;
    }
    if (recordado) olvidarDoctorElegido();
    renderProfesionales();
  }

  document.addEventListener("DOMContentLoaded", iniciar);
})();






