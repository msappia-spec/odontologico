/* =========================================================================
   OdontoCare - Dashboard de la Secretaría
   Conexión real con la API FastAPI (prefijo /api/v1).
   Vanilla JS + Fetch. Sin dependencias.
   ========================================================================= */
(function () {
  "use strict";

  const API = "/api/v1";

  /* ------------------------------- Estado ------------------------------- */
  const state = {
    doctores: [],
    pacientes: [],
    turnos: [],
    // Recordatorios pendientes (GET /whatsapp/recordatorios-pendientes):
    // la lista y la ventana de días que muestra la pestaña "Recordatorios".
    recordatorios: [],
    recDias: 3,
    recError: null,
    vista: "calendario",
    agendaFecha: hoyISO(),
    agendaDoctor: "",
    turnoEditId: null,
    pacienteEditId: null,
    // Agenda semanal de los odontólogos (GET /doctores/{id}/disponibilidad):
    // `disponibilidad` es la lista plana de rangos y `dispPorDoctor` el mapa por
    // profesional que consume calendar.js para armar los bloques del día.
    // `dispDoctor` es el profesional que se edita en la pestaña Disponibilidad.
    disponibilidad: [],
    dispPorDoctor: {},
    dispCargada: false,
    dispDoctor: "",
  };

  /* --------------------------- Utilidades fecha -------------------------- */
  function isoDe(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${dd}`;
  }
  function hoyISO() { return isoDe(new Date()); }
  function ahoraISO() {
    const d = new Date();
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    return `${isoDe(d)}T${hh}:${mm}:00`;
  }
  function sumarDiasISO(iso, n) {
    const [y, m, d] = iso.split("-").map(Number);
    const dt = new Date(y, m - 1, d);
    dt.setDate(dt.getDate() + n);
    return isoDe(dt);
  }
  function parteFecha(iso) { return String(iso || "").slice(0, 10); }
  function parteHora(iso) { return String(iso || "").slice(11, 16); }
  function fechaLarga(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("es-AR", {
      weekday: "long", day: "numeric", month: "long", year: "numeric",
    });
  }
  function fechaCorta(iso) {
    const f = parteFecha(iso);
    const [y, m, d] = f.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("es-AR", { day: "2-digit", month: "short" });
  }
  function duracionTexto(inicio, fin) {
    const min = Math.round((new Date(fin) - new Date(inicio)) / 60000);
    if (min <= 0) return "";
    if (min < 60) return `${min} min`;
    const h = Math.floor(min / 60);
    const r = min % 60;
    return r ? `${h} h ${r} min` : `${h} h`;
  }

  /* ------------------------------- Helpers ------------------------------- */
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function porId(lista, id) { return lista.find((x) => x.id === id) || null; }
  function nombreDoctor(id) {
    const d = porId(state.doctores, id);
    return d ? d.nombre : `Doctor #${id}`;
  }
  function nombrePaciente(id) {
    const p = porId(state.pacientes, id);
    return p ? p.nombre : `Paciente #${id}`;
  }

  const PALETA = ["#0d8578", "#3b82f6", "#8b5cf6", "#f59e0b", "#ef4444", "#0ea5e9", "#ec4899", "#14b8a6"];
  function colorDe(id) { return PALETA[Math.abs(Number(id) || 0) % PALETA.length]; }
  function iniciales(nombre) {
    const partes = String(nombre || "?").trim().split(/\s+/).filter(Boolean);
    if (!partes.length) return "?";
    if (partes.length === 1) return partes[0].slice(0, 2).toUpperCase();
    return (partes[0][0] + partes[partes.length - 1][0]).toUpperCase();
  }
  function avatar(nombre, id) {
    return `<span class="avatar" style="background:${colorDe(id)}">${esc(iniciales(nombre))}</span>`;
  }

  /* ------------------------------ Íconos SVG ----------------------------- */
  const svg = (inner) =>
    `<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
    `stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;

  const ICON = {
    reloj: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    arena: svg('<path d="M6 2h12M6 22h12"/><path d="M6 2c0 5 6 6 6 10s-6 5-6 10M18 2c0 5-6 6-6 10s6 5 6 10"/>'),
    check: svg('<path d="M20 6 9 17l-5-5"/>'),
    x: svg('<path d="M18 6 6 18M6 6l12 12"/>'),
    alerta: svg('<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>'),
    // Glifo oficial de WhatsApp: el <symbol id="ico-whatsapp"> vive en index.html
    // y se reutiliza con <use> para no duplicar el trazado del ícono.
    whatsapp:
      '<svg class="w-4 h-4" viewBox="0 0 24 24" aria-hidden="true"><use href="#ico-whatsapp"/></svg>',
    enviar: svg('<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>'),
    refrescar: svg('<path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/>'),
  };

  /* --------------------------- Estados visuales -------------------------- */
  const ESTADO_WA = {
    PENDIENTE_ENVIO: { texto: "Sin enviar", clase: "badge-slate", icono: ICON.arena },
    ENVIADO: { texto: "Enviado", clase: "badge-amber", icono: ICON.reloj },
    FALLIDO: { texto: "Falló el envío", clase: "badge-red", icono: ICON.alerta },
    RESPONDIDO: { texto: "Respondido", clase: "badge-green", icono: ICON.check },
  };
  const ESTADO_TURNO = {
    PENDIENTE: { texto: "Pendiente", clase: "badge-amber", icono: ICON.reloj },
    CONFIRMADO: { texto: "Confirmado", clase: "badge-green", icono: ICON.check },
    CANCELADO: { texto: "Cancelado", clase: "badge-red", icono: ICON.x },
  };

  function badge(meta) {
    return `<span class="badge ${meta.clase}">${meta.icono}${esc(meta.texto)}</span>`;
  }
  function badgeTurno(e) {
    return badge(ESTADO_TURNO[e] || { texto: e, clase: "badge-slate", icono: "" });
  }
  function badgeWhatsApp(t) {
    if (t.estado_whatsapp === "RESPONDIDO") {
      return t.estado_turno === "CANCELADO"
        ? badge({ texto: "Respondió: Canceló", clase: "badge-red", icono: ICON.x })
        : badge({ texto: "Respondió: Confirmó", clase: "badge-green", icono: ICON.check });
    }
    return badge(ESTADO_WA[t.estado_whatsapp] || { texto: t.estado_whatsapp, clase: "badge-slate", icono: "" });
  }

  /* ----------------------------- Cliente HTTP ---------------------------- */
  async function api(path, options) {
    const opts = Object.assign({ headers: { "Content-Type": "application/json" } }, options || {});
    const res = await fetch(API + path, opts);
    const texto = await res.text();
    let data = null;
    if (texto) { try { data = JSON.parse(texto); } catch (_) { data = texto; } }
    if (!res.ok) {
      // El error conserva el status y el cuerpo de la respuesta para que la UI
      // pueda reaccionar distinto según el tipo de conflicto (p. ej. HTTP 400
      // cuando el paciente ya está agendado en la agenda de otro odontólogo).
      const err = new Error(mensajeError(data, res.status));
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function mensajeError(data, status) {
    // 405: la ruta existe pero el método HTTP no está registrado en el backend.
    // Es lo que ocurre cuando el servidor corre una versión anterior del código
    // (p. ej. sin el PATCH de /turnos/{id}); el mensaje guía a la solución real.
    if (status === 405) {
      return "El servidor no admite esta operación (HTTP 405 Method Not Allowed). " +
        "Reiniciá el backend para que cargue los endpoints actuales e intentá de nuevo.";
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

  /* -------------------------------- Toasts ------------------------------- */
  function toast(tipo, titulo, mensaje) {
    const cont = document.getElementById("toasts");
    const estilos = {
      exito: { borde: "#10b981", bg: "#ecfdf5", color: "#047857", icono: ICON.check },
      error: { borde: "#ef4444", bg: "#fef2f2", color: "#b91c1c", icono: ICON.x },
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
    }, 4400);
  }

  /* --------------------------- Render de apoyo --------------------------- */
  function vacio(titulo, sub) {
    return (
      `<div class="empty-state">` +
      svg('<circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/>').replace("w-3.5 h-3.5", "w-9 h-9") +
      `<p class="font-semibold text-slate-500">${esc(titulo)}</p>` +
      (sub ? `<p class="text-xs max-w-xs">${esc(sub)}</p>` : "") +
      `</div>`
    );
  }

  /* Acciones rápidas de una tarjeta de turno (editar / cancelar / reactivar).
     Los clics se resuelven por delegación en calendar.js. */
  function panelAccionesTurno(t) {
    const editar = `<button class="btn btn-ghost btn-sm" data-editar-turno="${t.id}">Editar</button>`;
    const alterno =
      t.estado_turno === "CANCELADO"
        ? `<button class="btn btn-soft btn-sm" data-reactivar-turno="${t.id}">Reactivar</button>`
        : `<button class="btn btn-danger-soft btn-sm" data-cancelar-turno="${t.id}">Cancelar</button>`;
    return `<div class="flex flex-wrap items-center gap-2 mt-2">${editar}${alterno}</div>`;
  }

  /* Color del bloque horario de la tarjeta según el estado del turno:
       * CONFIRMADO -> verde, con prioridad sobre el color del odontólogo;
       * CANCELADO  -> gris atenuado (la tarjeta queda en gris sutil);
       * PENDIENTE  -> color asignado al odontólogo.                        */
  function clasesHora(t) {
    const color = colorDe(t.doctor_id);
    if (t.estado_turno === "CONFIRMADO") {
      return {
        fila: "turno-row turno-confirmado",
        hora: "turno-hora hora-confirmada bg-green-500 text-white",
        estilo: "border-right-color:#15803d",
      };
    }
    if (t.estado_turno === "CANCELADO") {
      return {
        fila: "turno-row turno-cancelado",
        hora: "turno-hora hora-cancelada bg-slate-200 text-slate-500",
        estilo: "border-right-color:#cbd5e1",
      };
    }
    return {
      fila: "turno-row",
      hora: "turno-hora",
      estilo: `background:${color}1f;border-right-color:${color}`,
    };
  }

  function turnoCard(t) {
    const paciente = nombrePaciente(t.paciente_id);
    const doctor = porId(state.doctores, t.doctor_id);
    const esp = doctor ? doctor.especialidad : "";
    const dur = duracionTexto(t.fecha_hora_inicio, t.fecha_hora_fin);
    const cancelado = t.estado_turno === "CANCELADO";
    const puedeResponder =
      t.whatsapp_mensaje_id && t.estado_whatsapp === "ENVIADO" && !cancelado;

    const color = clasesHora(t);
    const accionesPanel = panelAccionesTurno(t);
    const acciones = puedeResponder
      ? `<div class="flex flex-wrap items-center gap-2 mt-3">
           <button class="btn btn-soft btn-sm" data-simular="confirmar_turno" data-wamid="${esc(t.whatsapp_mensaje_id)}">
             Responder: Confirma
           </button>
           <button class="btn btn-danger-soft btn-sm" data-simular="cancelar_turno" data-wamid="${esc(t.whatsapp_mensaje_id)}">
             Responder: Cancela
           </button>
         </div>`
      : "";

    return (
      `<div class="${color.fila}">` +
      `<div class="${color.hora}" style="${color.estilo}">` +
      `<div class="h">${esc(parteHora(t.fecha_hora_inicio))}</div>` +
      `<div class="d">${esc(fechaCorta(t.fecha_hora_inicio))}</div>` +
      (dur ? `<div class="d mt-0.5">${esc(dur)}</div>` : "") +
      `</div>` +
      `<div class="flex-1 min-w-0">` +
      `<div class="flex items-start gap-3">` +
      avatar(paciente, t.paciente_id) +
      `<div class="flex-1 min-w-0">` +
      `<p class="font-semibold truncate ${cancelado ? "line-through text-slate-400" : "text-slate-900"}">${esc(paciente)}</p>` +
      `<p class="text-xs text-slate-500 truncate">${esc(nombreDoctor(t.doctor_id))}` +
      (esp ? " &middot; " + esc(esp) : "") +
      `</p>` +
      `</div></div>` +
      `<div class="flex flex-wrap items-center gap-2 mt-2.5">` +
      badgeTurno(t.estado_turno) +
      badgeWhatsApp(t) +
      (t.whatsapp_mensaje_id
        ? `<span class="text-[10px] text-slate-400 font-mono truncate max-w-[170px]" title="${esc(t.whatsapp_mensaje_id)}">${esc(t.whatsapp_mensaje_id)}</span>`
        : "") +
      `</div>` +
      accionesPanel +
      acciones +
      `</div></div>`
    );
  }

  function turnoCompacto(t) {
    return (
      `<div class="flex items-center gap-3 p-2.5 rounded-xl hover:bg-slate-50 transition">` +
      avatar(nombrePaciente(t.paciente_id), t.paciente_id) +
      `<div class="flex-1 min-w-0">` +
      `<p class="text-sm font-semibold text-slate-800 truncate">${esc(nombrePaciente(t.paciente_id))}</p>` +
      `<p class="text-[11px] text-slate-500 truncate">${esc(fechaCorta(t.fecha_hora_inicio))} &middot; ` +
      `${esc(parteHora(t.fecha_hora_inicio))} &middot; ${esc(nombreDoctor(t.doctor_id))}</p>` +
      `</div>${badgeWhatsApp(t)}</div>`
    );
  }

  /* ------------------------------- Vistas -------------------------------- */
  function renderKPIs() {
    const hoy = hoyISO();
    const delDia = state.turnos.filter((t) => parteFecha(t.fecha_hora_inicio) === hoy);
    const contar = (e) => state.turnos.filter((t) => t.estado_turno === e).length;

    document.getElementById("kpi-hoy").textContent = delDia.length;
    document.getElementById("kpi-confirmados").textContent = contar("CONFIRMADO");
    document.getElementById("kpi-pendientes").textContent = contar("PENDIENTE");
    document.getElementById("kpi-cancelados").textContent = contar("CANCELADO");
  }

  function renderDashboard() {
    renderKPIs();
    const hoy = hoyISO();
    document.getElementById("dash-hoy-fecha").textContent = fechaLarga(hoy);

    const delDia = state.turnos
      .filter((t) => parteFecha(t.fecha_hora_inicio) === hoy)
      .sort((a, b) => a.fecha_hora_inicio.localeCompare(b.fecha_hora_inicio));

    document.getElementById("dash-agenda-hoy").innerHTML = delDia.length
      ? delDia.map(turnoCard).join("")
      : vacio("No hay turnos agendados para hoy", "Usá el botón «Nuevo turno» para agendar una cita.");

    const ahora = ahoraISO();
    const proximos = state.turnos
      .filter((t) => t.fecha_hora_inicio >= ahora && t.estado_turno !== "CANCELADO")
      .sort((a, b) => a.fecha_hora_inicio.localeCompare(b.fecha_hora_inicio))
      .slice(0, 5);

    document.getElementById("dash-proximos").innerHTML = proximos.length
      ? proximos.map(turnoCompacto).join("")
      : vacio("Sin turnos próximos");
  }

  function renderAgenda() {
    const fecha = state.agendaFecha;
    document.getElementById("agenda-fecha").value = fecha;
    document.getElementById("agenda-titulo").textContent = fechaLarga(fecha);

    let lista = state.turnos.filter((t) => parteFecha(t.fecha_hora_inicio) === fecha);
    if (state.agendaDoctor) {
      lista = lista.filter((t) => String(t.doctor_id) === String(state.agendaDoctor));
    }
    lista.sort((a, b) => a.fecha_hora_inicio.localeCompare(b.fecha_hora_inicio));

    document.getElementById("agenda-lista").innerHTML = lista.length
      ? lista.map(turnoCard).join("")
      : vacio("No hay turnos para este día", "Probá con otra fecha o agendá un nuevo turno.");

    document.getElementById("agenda-resumen").textContent =
      `${lista.length} turno${lista.length === 1 ? "" : "s"}`;
  }

  function renderDoctores() {
    document.getElementById("count-doctores").textContent = state.doctores.length;
    const tbody = document.getElementById("lista-doctores");
    if (!state.doctores.length) {
      tbody.innerHTML = `<tr><td colspan="4">${vacio("Aún no hay doctores registrados")}</td></tr>`;
      return;
    }
    tbody.innerHTML = state.doctores
      .map((d) => {
        const n = state.turnos.filter((t) => t.doctor_id === d.id).length;
        const estado = d.activo
          ? `<span class="badge badge-green">${ICON.check}Activo</span>`
          : `<span class="badge badge-slate">Inactivo</span>`;
        return (
          `<tr><td><div class="flex items-center gap-3">${avatar(d.nombre, d.id)}` +
          `<span class="font-semibold text-slate-800">${esc(d.nombre)}</span></div></td>` +
          `<td>${esc(d.especialidad)}</td><td>${estado}</td>` +
          `<td><span class="badge badge-brand">${n}</span></td></tr>`
        );
      })
      .join("");
  }

  function renderPacientes() {
    document.getElementById("count-pacientes").textContent = state.pacientes.length;
    const tbody = document.getElementById("lista-pacientes");
    if (!state.pacientes.length) {
      tbody.innerHTML = `<tr><td colspan="7">${vacio("Aún no hay pacientes registrados")}</td></tr>`;
      return;
    }
    const guion = '<span class="text-slate-400">—</span>';
    tbody.innerHTML = state.pacientes
      .map((p) => {
        const n = state.turnos.filter((t) => t.paciente_id === p.id).length;
        const cobertura =
          p.obra_social || p.numero_afiliado
            ? `<p class="font-semibold text-slate-800">${p.obra_social ? esc(p.obra_social) : "Obra social s/d"}</p>` +
              (p.numero_afiliado
                ? `<p class="text-[11px] text-slate-400 font-mono">Nº ${esc(p.numero_afiliado)}</p>`
                : "")
            : guion;
        return (
          `<tr><td><div class="flex items-center gap-3">${avatar(p.nombre, p.id)}` +
          `<span class="font-semibold text-slate-800">${esc(p.nombre)}</span></div></td>` +
          `<td class="font-mono text-xs">${p.dni ? esc(p.dni) : guion}</td>` +
          `<td class="font-mono text-xs">${esc(p.telefono)}</td>` +
          `<td>${cobertura}</td>` +
          `<td>${p.email ? esc(p.email) : guion}</td>` +
          `<td><span class="badge badge-brand">${n}</span></td>` +
          `<td class="text-right">` +
          `<button type="button" class="btn btn-ghost btn-sm" data-editar-paciente="${p.id}" ` +
          `title="Editar los datos de ${esc(p.nombre)}">Editar</button>` +
          `</td></tr>`
        );
      })
      .join("");
  }

  function cargarSelectores() {
    // Filtro de agenda (preserva la selección actual)
    const filtro = document.getElementById("agenda-doctor");
    filtro.innerHTML =
      `<option value="">Todos los doctores</option>` +
      state.doctores.map((d) => `<option value="${d.id}">${esc(d.nombre)}</option>`).join("");
    filtro.value = state.agendaDoctor;

    // Modal de nuevo turno
    const selPac = document.getElementById("turno-paciente");
    const selDoc = document.getElementById("turno-doctor");
    const valPac = selPac.value;
    const valDoc = selDoc.value;

    selPac.innerHTML = state.pacientes.length
      ? state.pacientes
          .map(
            (p) =>
              `<option value="${p.id}">${esc(p.nombre)} · ${esc(p.telefono)}` +
              (p.dni ? ` · DNI ${esc(p.dni)}` : "") +
              `</option>`
          )
          .join("")
      : `<option value="">— Registrá un paciente primero —</option>`;

    const activos = state.doctores.filter((d) => d.activo);
    selDoc.innerHTML = activos.length
      ? activos.map((d) => `<option value="${d.id}">${esc(d.nombre)} — ${esc(d.especialidad)}</option>`).join("")
      : `<option value="">— Registrá un doctor activo primero —</option>`;

    if (valPac) selPac.value = valPac;
    if (valDoc) selDoc.value = valDoc;
  }

  function renderTodo() {
    cargarSelectores();
    renderDashboard();
    renderAgenda();
    renderRecordatorios();
    renderDoctores();
    renderPacientes();
    renderDisponibilidad();
    // Notifica a los módulos externos (calendar.js) que llegaron datos nuevos.
    document.dispatchEvent(new CustomEvent("odonto:datos"));
  }

  /* --------------------------- Carga de datos ---------------------------- */
  async function cargarTodo() {
    try {
      const datos = await Promise.all([
        api("/doctores"),
        api("/pacientes"),
        api("/turnos?limit=500"),
      ]);
      state.doctores = datos[0] || [];
      state.pacientes = datos[1] || [];
      state.turnos = datos[2] || [];
      renderTodo();
      estadoApi(true);
      // Los recordatorios pendientes se piden aparte para no demorar el panel.
      cargarRecordatorios();
      // La agenda semanal de cada odontólogo también llega en su propia petición.
      cargarDisponibilidad();
    } catch (err) {
      estadoApi(false);
      toast("error", "No se pudo conectar con el servidor", err.message);
    }
  }

  function estadoApi(ok) {
    const dot = document.getElementById("api-status-dot");
    const txt = document.getElementById("api-status-text");
    dot.className = "w-2.5 h-2.5 rounded-full " + (ok ? "bg-emerald-400" : "bg-rose-500");
    txt.textContent = ok ? "API conectada" : "Sin conexión";
  }

  /* --------------------- Disponibilidad de los odontólogos ---------------- */
  // La agenda semanal de cada profesional vive en el backend
  // (`disponibilidad_doctores`, endpoints /doctores/{id}/disponibilidad). Acá se
  // carga una sola vez y se mantiene en `state` para que el calendario arme los
  // bloques del día (calendar.js lee `state.dispPorDoctor` vía `rangosDe`).
  const DIAS_SEMANA = [
    [1, "Lunes"], [2, "Martes"], [3, "Miércoles"], [4, "Jueves"],
    [5, "Viernes"], [6, "Sábado"], [0, "Domingo"],
  ];

  /* Plantilla sugerida para el botón "Restaurar plantilla": lunes a viernes con
     doble turno (mañana y tarde) y sábado por la mañana. */
  const PLANTILLA_SEMANAL = (function () {
    const rangos = [];
    [1, 2, 3, 4, 5].forEach(function (dia) {
      [["09:00", "13:00"], ["15:00", "19:00"]].forEach(function (tramo) {
        rangos.push({ dia_semana: dia, hora_inicio: tramo[0], hora_fin: tramo[1] });
      });
    });
    rangos.push({ dia_semana: 6, hora_inicio: "09:00", hora_fin: "13:00" });
    return rangos;
  })();

  function nombreDia(dia) {
    const par = DIAS_SEMANA.find((d) => d[0] === Number(dia));
    return par ? par[1] : `Día ${dia}`;
  }

  /* Rangos semanales configurados de un odontólogo ([] si no tiene ninguno). */
  function rangosDe(doctorId) {
    return state.dispPorDoctor[String(doctorId)] || [];
  }

  async function cargarDisponibilidad() {
    try {
      const listas = await Promise.all(
        state.doctores.map((d) => api(`/doctores/${d.id}/disponibilidad`))
      );
      const mapa = {};
      const plana = [];
      state.doctores.forEach(function (d, i) {
        const rangos = listas[i] || [];
        mapa[String(d.id)] = rangos;
        rangos.forEach((r) => plana.push(r));
      });
      state.dispPorDoctor = mapa;
      state.disponibilidad = plana;
      state.dispCargada = true;
      renderDisponibilidad();
      // calendar.js vuelve a dibujar la grilla y el panel del día.
      document.dispatchEvent(new CustomEvent("odonto:disponibilidad"));
    } catch (err) {
      toast("error", "No se pudo cargar la disponibilidad", err.message);
    }
  }

  /* Odontólogo que se está editando en la pestaña "Disponibilidad". */
  function doctorDisponibilidad() {
    if (!state.dispDoctor && state.doctores.length) {
      state.dispDoctor = String(state.doctores[0].id);
    }
    return porId(state.doctores, Number(state.dispDoctor)) || null;
  }

  /* Grilla semanal editable del odontólogo seleccionado: cada día lista sus
     rangos con un botón para borrarlos y un formulario para agregar otro. */
  function renderDisponibilidad() {
    const sel = document.getElementById("disp-doctor");
    const cont = document.getElementById("disp-semana");
    const resumen = document.getElementById("disp-resumen");
    if (!sel || !cont) return;

    sel.innerHTML = state.doctores.length
      ? state.doctores
          .map(
            (d) =>
              `<option value="${d.id}">${esc(d.nombre)} &middot; ${esc(d.especialidad)}</option>`
          )
          .join("")
      : `<option value="">&mdash; Registrá un odontólogo primero &mdash;</option>`;

    const doctor = doctorDisponibilidad();
    sel.value = doctor ? String(doctor.id) : "";

    if (!doctor) {
      cont.innerHTML = vacio(
        "Aún no hay odontólogos registrados",
        "Cargá un profesional para configurar su agenda semanal."
      );
      if (resumen) resumen.textContent = "0";
      return;
    }
    if (!state.dispCargada) {
      cont.innerHTML = vacio(
        "Cargando la agenda semanal…",
        "Se está leyendo la disponibilidad del servidor."
      );
      return;
    }

    const rangos = rangosDe(doctor.id);
    if (resumen) {
      resumen.textContent = `${rangos.length} rango${rangos.length === 1 ? "" : "s"}`;
    }

    cont.innerHTML = DIAS_SEMANA.map(function (par) {
      const dia = par[0];
      const delDia = rangos
        .filter((r) => Number(r.dia_semana) === dia)
        .sort((a, b) => a.hora_inicio.localeCompare(b.hora_inicio));

      const chips = delDia.length
        ? delDia
            .map(function (r) {
              return (
                `<span class="disp-rango">` +
                `<span class="disp-rango-hora">${esc(r.hora_inicio)} &ndash; ${esc(r.hora_fin)}</span>` +
                `<button type="button" class="disp-rango-x" data-borrar-rango="${r.id}" ` +
                `data-borrar-doctor="${doctor.id}" title="Eliminar este rango" ` +
                `aria-label="Eliminar el rango de ${esc(r.hora_inicio)} a ${esc(r.hora_fin)}">` +
                `<svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
                `stroke-width="2.6" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>` +
                `</button></span>`
              );
            })
            .join("")
        : `<span class="disp-sin-rangos">Sin atención</span>`;

      return (
        `<div class="disp-dia${delDia.length ? "" : " cerrado"}" data-disp-dia="${dia}">` +
        `<div class="disp-dia-cab">` +
        `<span class="disp-dia-nombre">${esc(par[1])}</span>` +
        (delDia.length
          ? `<span class="badge badge-slate">${delDia.length} rango${delDia.length === 1 ? "" : "s"}</span>`
          : "") +
        `</div>` +
        `<div class="disp-rangos">${chips}</div>` +
        `<form class="disp-form" data-disp-form="${dia}">` +
        `<input type="time" class="field disp-field" data-disp-desde value="09:00" step="300" ` +
        `required aria-label="Hora de inicio del ${esc(par[1])}" />` +
        `<span class="disp-form-sep">a</span>` +
        `<input type="time" class="field disp-field" data-disp-hasta value="13:00" step="300" ` +
        `required aria-label="Hora de fin del ${esc(par[1])}" />` +
        `<button type="submit" class="btn btn-soft btn-sm" title="Agregar el rango al ${esc(par[1])}">Agregar</button>` +
        `</form>` +
        `</div>`
      );
    }).join("");
  }

  async function agregarRangoDisponibilidad(dia, desde, hasta) {
    const doctor = doctorDisponibilidad();
    if (!doctor) return;
    if (!desde || !hasta) {
      toast("info", "Faltan horarios", "Indicá la hora de inicio y de fin del rango.");
      return;
    }
    try {
      await api(`/doctores/${doctor.id}/disponibilidad`, {
        method: "POST",
        body: JSON.stringify({
          dia_semana: Number(dia),
          hora_inicio: desde,
          hora_fin: hasta,
        }),
      });
      toast(
        "exito",
        "Rango agregado",
        `${nombreDia(dia)} de ${desde} a ${hasta} · ${doctor.nombre}`
      );
      await cargarDisponibilidad();
    } catch (err) {
      toast("error", "No se pudo agregar el rango", err.message);
    }
  }

  async function borrarRangoDisponibilidad(doctorId, rangoId) {
    try {
      await api(`/doctores/${doctorId}/disponibilidad/${rangoId}`, { method: "DELETE" });
      toast("exito", "Rango eliminado", nombreDoctor(Number(doctorId)));
      await cargarDisponibilidad();
    } catch (err) {
      toast("error", "No se pudo eliminar el rango", err.message);
    }
  }

  /* Reemplazo total de la semana del odontólogo (PUT): lo usan el botón de
     plantilla sugerida y el vaciado completo de la agenda. */
  async function reemplazarSemana(rangos, mensaje) {
    const doctor = doctorDisponibilidad();
    if (!doctor) return;
    try {
      await api(`/doctores/${doctor.id}/disponibilidad`, {
        method: "PUT",
        body: JSON.stringify({ rangos: rangos }),
      });
      toast("exito", "Agenda actualizada", `${mensaje} · ${doctor.nombre}`);
      await cargarDisponibilidad();
    } catch (err) {
      toast("error", "No se pudo guardar la agenda", err.message);
    }
  }

  /* Vista previa de los bloques que el backend ofrece para una fecha concreta:
     usa el mismo cálculo que alimenta el calendario
     (GET /doctores/{id}/horarios-disponibles?fecha=&duracion_minutos=). */
  async function probarBloquesDisponibilidad() {
    const doctor = doctorDisponibilidad();
    const cont = document.getElementById("disp-bloques");
    if (!doctor || !cont) return;

    const fechaInput = document.getElementById("disp-fecha");
    const duracionInput = document.getElementById("disp-duracion");
    const fecha = (fechaInput && fechaInput.value) || hoyISO();
    const duracion = Number((duracionInput && duracionInput.value) || 30) || 30;

    cont.innerHTML = `<p class="text-xs text-slate-400">Calculando bloques…</p>`;
    try {
      const r = await api(
        `/doctores/${doctor.id}/horarios-disponibles?fecha=${encodeURIComponent(fecha)}` +
          `&duracion_minutos=${duracion}`
      );
      const bloques = r.bloques || [];
      const cabecera =
        `<p class="text-xs text-slate-500 mb-2">` +
        `${esc(r.dia)} ${esc(r.fecha)} · ${bloques.length} bloque${bloques.length === 1 ? "" : "s"} ` +
        `de ${r.duracion_minutos} min para ${esc(doctor.nombre)}</p>`;
      cont.innerHTML =
        cabecera +
        (bloques.length
          ? `<div class="disp-bloques-grid">` +
            bloques
              .map(
                (b) =>
                  `<span class="disp-bloque-chip">${esc(b.hora_inicio)} &ndash; ${esc(b.hora_fin)}</span>`
              )
              .join("") +
            `</div>`
          : vacio(
              "Sin bloques libres ese día",
              "Revisá los rangos configurados o los turnos que ya ocupan el día."
            ));
    } catch (err) {
      cont.innerHTML = `<p class="text-xs text-rose-600">${esc(err.message)}</p>`;
    }
  }

  /* ------------------------------ Navegación ----------------------------- */
  const VISTAS = {
    dashboard: ["Dashboard", "Resumen del consultorio y estado de los recordatorios"],
    calendario: ["Calendario", "Calendario mensual de turnos y disponibilidad por día"],
    disponibilidad: [
      "Disponibilidad médica",
      "Agenda semanal por odontólogo: rangos de mañana y tarde para cada día",
    ],
    recordatorios: ["Recordatorios pendientes", "Turnos próximos que todavía no recibieron el aviso de WhatsApp"],
    doctores: ["Doctores", "Registro de profesionales del consultorio"],
    pacientes: ["Pacientes", "Registro de pacientes y sus teléfonos de WhatsApp"],
  };

  function cambiarVista(v) {
    if (!VISTAS[v]) return;
    state.vista = v;
    document.querySelectorAll("main > section").forEach((s) => s.classList.add("hide"));
    const sec = document.getElementById("view-" + v);
    if (sec) sec.classList.remove("hide");
    document.querySelectorAll(".nav-link[data-view]").forEach((a) =>
      a.classList.toggle("active", a.dataset.view === v));
    document.getElementById("page-title").textContent = VISTAS[v][0];
    document.getElementById("page-subtitle").textContent = VISTAS[v][1];
    document.getElementById("sidebar").classList.remove("open");
    if (v === "recordatorios") {
      // Al entrar a la pestaña se refresca la lista y el subtítulo con la
      // ventana de días vigente.
      actualizarSubtituloRecordatorios();
      cargarRecordatorios();
    }
    if (v === "disponibilidad") {
      // La grilla semanal se redibuja con lo que ya haya en memoria; si todavía
      // no llegó la disposición del servidor, muestra el estado de carga.
      renderDisponibilidad();
    }
  }

  /* --------------------------- Helpers de UI ----------------------------- */
  function bloquear(btn, on) {
    if (!btn) return;
    btn.disabled = on;
    if (on) {
      if (!btn.dataset.txt) btn.dataset.txt = btn.innerHTML;
      btn.innerHTML =
        '<svg class="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">' +
        '<circle cx="12" cy="12" r="10" stroke="currentColor" stroke-opacity=".25" stroke-width="4"/>' +
        '<path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg> Guardando…';
    } else if (btn.dataset.txt) {
      btn.innerHTML = btn.dataset.txt;
    }
  }

  /* ------------------------------- Acciones ------------------------------ */
  async function crearDoctor(ev) {
    ev.preventDefault();
    const form = ev.target;
    const btn = form.querySelector("button[type=submit]");
    const payload = {
      nombre: document.getElementById("doc-nombre").value.trim(),
      especialidad: document.getElementById("doc-especialidad").value.trim(),
      activo: document.getElementById("doc-activo").checked,
    };
    try {
      bloquear(btn, true);
      const nuevo = await api("/doctores", { method: "POST", body: JSON.stringify(payload) });
      form.reset();
      document.getElementById("doc-activo").checked = true;
      toast("exito", "Doctor guardado", `${nuevo.nombre} · ${nuevo.especialidad}`);
      await cargarTodo();
    } catch (err) {
      toast("error", "No se pudo guardar el doctor", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* ------------------- Formulario de pacientes (alta/edición) ------------- */
  /* Un único formulario cumple las dos funciones: `state.pacienteEditId` guarda
     el id del paciente en edición (``null`` = alta). */
  function modoEdicionPaciente(paciente) {
    const titulo = document.getElementById("pac-form-titulo");
    const sub = document.getElementById("pac-form-sub");
    const texto = document.getElementById("pac-guardar-texto");
    const cancelar = document.getElementById("pac-cancelar-edicion");

    if (paciente) {
      document.getElementById("pac-nombre").value = paciente.nombre || "";
      document.getElementById("pac-telefono").value = paciente.telefono || "";
      document.getElementById("pac-email").value = paciente.email || "";
      document.getElementById("pac-dni").value = paciente.dni || "";
      document.getElementById("pac-obra-social").value = paciente.obra_social || "";
      document.getElementById("pac-numero-afiliado").value = paciente.numero_afiliado || "";
      state.pacienteEditId = paciente.id;
      if (titulo) titulo.textContent = "Editar paciente";
      if (sub) sub.textContent = "Modificá los datos del paciente y guardá los cambios.";
      if (texto) texto.textContent = "Guardar cambios";
      if (cancelar) cancelar.classList.remove("hide");
    } else {
      state.pacienteEditId = null;
      if (titulo) titulo.textContent = "Registrar paciente";
      if (sub) sub.textContent = "El teléfono se usa para los avisos de WhatsApp.";
      if (texto) texto.textContent = "Guardar paciente";
      if (cancelar) cancelar.classList.add("hide");
    }
  }

  function editarPaciente(id) {
    const paciente = porId(state.pacientes, Number(id));
    if (!paciente) return;
    cambiarVista("pacientes");
    modoEdicionPaciente(paciente);
    const form = document.getElementById("form-paciente");
    if (form && form.scrollIntoView) form.scrollIntoView({ behavior: "smooth", block: "center" });
    const nombre = document.getElementById("pac-nombre");
    if (nombre && nombre.focus) nombre.focus();
  }

  function cancelarEdicionPaciente() {
    const form = document.getElementById("form-paciente");
    if (form) form.reset();
    modoEdicionPaciente(null);
  }

  async function crearPaciente(ev) {
    ev.preventDefault();
    const form = ev.target;
    const btn = form.querySelector("button[type=submit]");
    const payload = {
      nombre: document.getElementById("pac-nombre").value.trim(),
      telefono: document.getElementById("pac-telefono").value.trim(),
      email: document.getElementById("pac-email").value.trim() || null,
      dni: document.getElementById("pac-dni").value.trim() || null,
      obra_social: document.getElementById("pac-obra-social").value.trim() || null,
      numero_afiliado: document.getElementById("pac-numero-afiliado").value.trim() || null,
    };

    const editando = Boolean(state.pacienteEditId);
    try {
      bloquear(btn, true);
      const guardado = await api(
        editando ? `/pacientes/${state.pacienteEditId}` : "/pacientes",
        { method: editando ? "PATCH" : "POST", body: JSON.stringify(payload) }
      );
      form.reset();
      modoEdicionPaciente(null);
      toast(
        "exito",
        editando ? "Paciente actualizado" : "Paciente guardado",
        `${guardado.nombre} · ${guardado.telefono}`
      );
      await cargarTodo();
    } catch (err) {
      toast(
        "error",
        editando ? "No se pudo actualizar el paciente" : "No se pudo guardar el paciente",
        err.message
      );
    } finally {
      bloquear(btn, false);
    }
  }

  /* -------------------------- Turnos (modal) ----------------------------- */
  function isoLocal(d) {
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    return `${isoDe(d)}T${hh}:${mm}:00`;
  }

  function abrirModal() {
    const hayDoctor = state.doctores.some((d) => d.activo);
    if (!state.pacientes.length || !hayDoctor) {
      toast("info", "Faltan datos", "Registrá al menos un paciente y un doctor activo para agendar.");
      return;
    }
    document.getElementById("turno-fecha").value = state.agendaFecha || hoyISO();
    document.getElementById("turno-error").classList.add("hide");
    // El modal se abre con el selector de odontólogo libre; el calendario lo
    // bloquea después si el turno proviene de una agenda filtrada. Si el panel
    // del día está filtrado, se preselecciona ese profesional.
    fijarDoctorTurno(state.agendaDoctor || "", false);
    document.getElementById("modal-turno").classList.remove("hide");
  }

  function cerrarModal() {
    // Restablece el modal al modo "crear" por si quedó abierto en modo edición.
    state.turnoEditId = null;
    const titulo = document.getElementById("modal-turno-titulo");
    if (titulo) titulo.textContent = "Nuevo turno";
    const guardar = document.getElementById("modal-turno-save");
    if (guardar) {
      guardar.textContent = "Guardar turno";
      delete guardar.dataset.txt;
    }
    fijarDoctorTurno("", false);   // desbloquea el odontólogo para el próximo turno
    document.getElementById("modal-turno").classList.remove("anim-pop");
    document.getElementById("modal-turno").classList.add("hide");
  }

  /* Fija el odontólogo del modal y, si corresponde, bloquea el selector para
     que el turno no se agende por error en otra agenda. Lo usa calendar.js
     cuando hay un filtro por profesional activo en el calendario. */
  function fijarDoctorTurno(doctorId, bloqueado) {
    const sel = document.getElementById("turno-doctor");
    if (!sel) return;
    if (doctorId !== undefined && doctorId !== null && doctorId !== "") {
      sel.value = String(doctorId);
    }
    const on = Boolean(bloqueado);
    sel.disabled = on;
    const hint = document.getElementById("turno-doctor-hint");
    const nombre = document.getElementById("turno-doctor-hint-nombre");
    if (nombre) nombre.textContent = on ? nombreDoctor(parseInt(sel.value, 10)) : "";
    if (hint) hint.classList.toggle("hide", !on);
  }

  function mostrarErrorTurno(msg, titulo, detalle) {
    const box = document.getElementById("turno-error");
    if (!box) return;
    box.innerHTML =
      `<div class="flex items-start gap-2.5">` +
      `<span class="flex-none mt-0.5">${ICON.alerta.replace("w-3.5 h-3.5", "w-4 h-4")}</span>` +
      `<div class="min-w-0 flex-1">` +
      `<p class="font-bold text-rose-800">${esc(titulo || "No se pudo guardar el turno")}</p>` +
      `<p class="mt-0.5 text-rose-700">${esc(msg)}</p>` +
      (detalle ? `<p class="mt-1 text-[11px] text-rose-600">${esc(detalle)}</p>` : "") +
      `</div></div>`;
    box.classList.remove("hide");
    box.classList.remove("anim-pop");
    void box.offsetWidth;   // reinicia la animación si la alerta ya estaba visible
    box.classList.add("anim-pop");
  }

  /* Busca, entre los turnos ya cargados, el del paciente que se cruza con el
     rango pedido. Sirve para explicarle a la secretaría dónde está ese paciente
     cuando el backend rechaza el alta por estar citado en otra agenda. */
  function turnoEnConflicto(pacienteId, inicio, fin, turnoIdExcluido) {
    const i = new Date(inicio).getTime();
    const f = new Date(fin).getTime();
    return state.turnos.find(function (t) {
      if (t.paciente_id !== pacienteId) return false;
      if (String(t.id) === String(turnoIdExcluido)) return false;
      if (t.estado_turno === "CANCELADO") return false;
      return (
        new Date(t.fecha_hora_inicio).getTime() < f &&
        new Date(t.fecha_hora_fin).getTime() > i
      );
    }) || null;
  }

  /* Detalle del turno que genera el conflicto: se prefiere el que devuelve el
     backend (``turno_en_conflicto``) y, si no viene, se busca en el estado local. */
  function detallePacienteOcupado(err, payload) {
    const datos = err && err.data;
    const turno =
      (datos && datos.turno_en_conflicto) ||
      turnoEnConflicto(
        payload.paciente_id,
        payload.fecha_hora_inicio,
        payload.fecha_hora_fin,
        state.turnoEditId
      );
    if (!turno) return "";
    return (
      `Coincide con el turno #${turno.id} del ${fechaCorta(turno.fecha_hora_inicio)} ` +
      `a las ${parteHora(turno.fecha_hora_inicio)} con ${nombreDoctor(turno.doctor_id)}.`
    );
  }

  /* Alerta en pantalla del rechazo al guardar un turno. El conflicto de agenda
     del paciente (HTTP 400) se explica con el detalle del otro turno y, además,
     se avisa con un toast para que no pase desapercibido. */
  function alertaErrorTurno(err, payload) {
    const mensaje = (err && err.message) || "Error al guardar el turno.";
    if (err && err.status === 400) {
      mostrarErrorTurno(
        mensaje,
        "Ese paciente ya tiene un turno en este horario",
        detallePacienteOcupado(err, payload)
      );
      toast("error", "No se pudo agendar", mensaje);
      return;
    }
    mostrarErrorTurno(mensaje);
  }

  async function crearTurno(ev) {
    ev.preventDefault();
    const btn = document.getElementById("modal-turno-save");
    document.getElementById("turno-error").classList.add("hide");

    const pacienteId = parseInt(document.getElementById("turno-paciente").value, 10);
    const doctorId = parseInt(document.getElementById("turno-doctor").value, 10);
    const fecha = document.getElementById("turno-fecha").value;
    const hora = document.getElementById("turno-hora").value;
    const dur = parseInt(document.getElementById("turno-duracion").value, 10) || 30;

    if (!pacienteId || !doctorId) {
      mostrarErrorTurno("Seleccioná un paciente y un doctor válidos.", "Faltan datos del turno");
      return;
    }
    if (!fecha || !hora) {
      mostrarErrorTurno("Indicá la fecha y la hora de inicio del turno.", "Faltan datos del turno");
      return;
    }

    const partes = hora.split(":").map(Number);
    const inicio = new Date(fecha + "T00:00:00");
    inicio.setHours(partes[0], partes[1], 0, 0);
    const fin = new Date(inicio.getTime() + dur * 60000);

    const payload = {
      paciente_id: pacienteId,
      doctor_id: doctorId,
      fecha_hora_inicio: isoLocal(inicio),
      fecha_hora_fin: isoLocal(fin),
    };

    try {
      bloquear(btn, true);
      const esEdicion = Boolean(state.turnoEditId);
      const t = await api(esEdicion ? `/turnos/${state.turnoEditId}` : "/turnos", {
        method: esEdicion ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      cerrarModal();
      state.agendaFecha = parteFecha(t.fecha_hora_inicio);
      toast(
        "exito",
        esEdicion ? "Turno actualizado" : "Turno agendado",
        `${nombrePaciente(t.paciente_id)} · ${fechaCorta(t.fecha_hora_inicio)} ${parteHora(t.fecha_hora_inicio)}`
      );
      await cargarTodo();
      if (state.vista === "calendario") cambiarVista("calendario");
    } catch (err) {
      alertaErrorTurno(err, payload);
    } finally {
      bloquear(btn, false);
    }
  }

  /* ---------------------------- Recordatorios ---------------------------- */
  // Recordatorios por WhatsApp con enlaces Click-to-Chat (wa.me). El backend ya
  // entrega el mensaje redactado y el enlace armado (GET
  // /whatsapp/recordatorios-pendientes); acá sólo se listan los turnos de los
  // próximos N días que siguen en PENDIENTE_ENVIO y, al abrir WhatsApp, se marca
  // el turno como ENVIADO para que salga de la lista.

  function diasRecordatorio() {
    return Number(state.recDias) || 3;
  }
  function textoDias(n) {
    return `${n} día${Number(n) === 1 ? "" : "s"}`;
  }
  function diasHasta(iso) {
    const hoy = new Date(parteFecha(hoyISO()) + "T00:00:00");
    const destino = new Date(parteFecha(iso) + "T00:00:00");
    return Math.round((destino - hoy) / 86400000);
  }
  function etiquetaDias(iso) {
    const n = diasHasta(iso);
    if (n <= 0) return "Es hoy";
    if (n === 1) return "Mañana";
    return `En ${n} días`;
  }

  function actualizarSubtituloRecordatorios() {
    const texto =
      `Turnos de los próximos ${textoDias(diasRecordatorio())} que todavía no ` +
      "recibieron el aviso de WhatsApp.";
    const sub = document.getElementById("rec-subtitulo");
    if (sub) sub.textContent = texto;
    // Si la vista activa es la de recordatorios, el encabezado de la página
    // también refleja la ventana de días elegida.
    if (state.vista === "recordatorios") {
      document.getElementById("page-subtitle").textContent = texto;
    }
  }

  function actualizarContadoresRecordatorios() {
    const n = state.recordatorios.length;
    ["nav-rec-pendientes", "header-rec-pendientes"].forEach(function (id) {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = String(n);
      el.classList.toggle("hide", n === 0);
    });
    const contador = document.getElementById("rec-contador");
    if (contador) contador.textContent = `${n} pendiente${n === 1 ? "" : "s"}`;
    const resumen = document.getElementById("dash-rec-resumen");
    if (resumen) {
      resumen.textContent = n
        ? `${n} turno${n === 1 ? "" : "s"} de los próximos ${textoDias(diasRecordatorio())} esperan el aviso de WhatsApp.`
        : `Sin recordatorios pendientes para los próximos ${textoDias(diasRecordatorio())}.`;
    }
    actualizarSubtituloRecordatorios();
  }

  function filaRecordatorio(r) {
    const enlace = r.enlace_whatsapp || "";
    const boton = enlace
      ? `<a class="btn-wa" href="${esc(enlace)}" target="_blank" rel="noopener" ` +
        `data-rec-enviar="${r.turno_id}" title="Abrir WhatsApp con el mensaje ya escrito y marcar el turno como enviado">` +
        `${ICON.whatsapp}<span>Enviar por WhatsApp</span></a>`
      : `<span class="btn-wa btn-wa-off" title="El paciente no tiene un teléfono válido cargado">` +
        `${ICON.whatsapp}<span>Sin teléfono</span></span>`;

    return (
      `<div class="turno-row rec-row" data-rec-fila="${r.turno_id}">` +
        `<div class="turno-hora">` +
          `<div class="h">${esc(parteHora(r.fecha_hora_inicio))}</div>` +
          `<div class="d">${esc(fechaCorta(r.fecha_hora_inicio))}</div>` +
          `<div class="d mt-0.5">${esc(etiquetaDias(r.fecha_hora_inicio))}</div>` +
        `</div>` +
        `<div class="flex-1 min-w-0">` +
          `<div class="flex items-start gap-3">${avatar(r.paciente, r.paciente_id)}` +
            `<div class="flex-1 min-w-0">` +
              `<p class="font-semibold text-slate-900 truncate">${esc(r.paciente)}</p>` +
              `<p class="text-xs text-slate-500 truncate">${esc(r.telefono || "Sin teléfono cargado")} &middot; ${esc(r.doctor)}</p>` +
              `<p class="text-[11px] text-slate-400 truncate">${esc(fechaLarga(parteFecha(r.fecha_hora_inicio)))} &middot; ` +
                `de ${esc(parteHora(r.fecha_hora_inicio))} a ${esc(parteHora(r.fecha_hora_fin))}</p>` +
            `</div>` +
          `</div>` +
          `<div class="flex flex-wrap items-center gap-2 mt-2.5">` +
            badgeTurno(r.estado_turno) +
            badgeWhatsApp(r) +
            `<span class="badge badge-slate">Turno #${esc(r.turno_id)}</span>` +
          `</div>` +
          `<p class="rec-mensaje" title="Mensaje que se enviará">${esc(r.mensaje)}</p>` +
        `</div>` +
        `<div class="rec-acciones">${boton}` +
          `<span class="text-[10px] text-slate-400 text-right leading-tight">Al abrirlo se marca<br>como enviado</span>` +
        `</div>` +
      `</div>`
    );
  }

  function renderRecordatorios() {
    const cont = document.getElementById("rec-lista");
    if (!cont) return;
    actualizarContadoresRecordatorios();

    if (state.recError) {
      cont.innerHTML = `<div class="card">${vacio("No se pudieron cargar los recordatorios", state.recError)}</div>`;
      return;
    }
    if (!state.recordatorios.length) {
      cont.innerHTML = `<div class="card">${vacio(
        "No hay recordatorios pendientes",
        `Todos los turnos de los próximos ${textoDias(diasRecordatorio())} ya tienen su aviso de WhatsApp enviado.`
      )}</div>`;
      return;
    }
    cont.innerHTML = state.recordatorios.map(filaRecordatorio).join("");
  }

  async function cargarRecordatorios(avisar) {
    try {
      const datos = await api(
        `/whatsapp/recordatorios-pendientes?dias=${diasRecordatorio()}`
      );
      state.recordatorios = datos || [];
      state.recError = null;
      if (avisar) {
        toast(
          "info",
          "Lista de recordatorios actualizada",
          `${state.recordatorios.length} pendiente(s) en los próximos ${textoDias(diasRecordatorio())}.`
        );
      }
    } catch (err) {
      state.recError = err.message;
      if (avisar) toast("error", "No se pudieron cargar los recordatorios", err.message);
    }
    renderRecordatorios();
  }

  // Marca el turno como ENVIADO después de abrir el enlace de WhatsApp. El
  // backend acepta estado_whatsapp en el PATCH /turnos/{id}, así que la fila se
  // quita de la lista sin recargar todo el panel.
  async function marcarRecordatorioEnviado(turnoId, paciente) {
    const fila = document.querySelector(`[data-rec-fila="${turnoId}"]`);
    if (fila) fila.classList.add("enviando");
    try {
      await api(`/turnos/${turnoId}`, {
        method: "PATCH",
        body: JSON.stringify({ estado_whatsapp: "ENVIADO" }),
      });
      state.recordatorios = state.recordatorios.filter(
        (r) => String(r.turno_id) !== String(turnoId)
      );
      const turno = state.turnos.find((t) => String(t.id) === String(turnoId));
      if (turno) turno.estado_whatsapp = "ENVIADO";
      renderRecordatorios();
      renderDashboard();
      toast(
        "exito",
        "WhatsApp abierto",
        `${paciente || "Turno #" + turnoId} · recordatorio marcado como enviado.`
      );
    } catch (err) {
      if (fila) fila.classList.remove("enviando");
      toast("error", "No se pudo marcar el recordatorio como enviado", err.message);
    }
  }

  /* --------------------- Webhook: respuesta del paciente ------------------ */
  async function responderWhatsApp(wamid, botonId) {
    const esConfirmar = botonId === "confirmar_turno";
    const payload = {
      object: "whatsapp_business_account",
      entry: [
        {
          id: "SIMULADO",
          changes: [
            {
              field: "messages",
              value: {
                messaging_product: "whatsapp",
                messages: [
                  {
                    from: "+5491100000000",
                    id: "wamid.SIM." + Date.now(),
                    timestamp: String(Math.floor(Date.now() / 1000)),
                    type: "interactive",
                    context: { id: wamid },
                    interactive: {
                      type: "button_reply",
                      button_reply: {
                        id: botonId,
                        title: esConfirmar ? "Confirmar" : "Cancelar",
                      },
                    },
                  },
                ],
              },
            },
          ],
        },
      ],
    };

    try {
      const r = await api("/whatsapp/webhook", { method: "POST", body: JSON.stringify(payload) });
      toast(
        r.estado_turno === "CONFIRMADO" ? "exito" : "info",
        "Respuesta de WhatsApp procesada",
        `Turno #${r.turno_id} → ${r.estado_turno}`
      );
      await cargarTodo();
    } catch (err) {
      toast("error", "No se pudo procesar la respuesta", err.message);
    }
  }

  /* ------------------------------- Eventos ------------------------------- */
  function registrarEventos() {
    document.querySelectorAll(".nav-link[data-view]").forEach((a) =>
      a.addEventListener("click", () => cambiarVista(a.dataset.view)));

    document.querySelectorAll("[data-goto]").forEach((b) =>
      b.addEventListener("click", () => cambiarVista(b.dataset.goto)));

    document.getElementById("btn-menu").addEventListener("click", () =>
      document.getElementById("sidebar").classList.toggle("open"));

    document.getElementById("btn-nuevo-turno").addEventListener("click", abrirModal);
    document.getElementById("agenda-nuevo").addEventListener("click", abrirModal);
    document.getElementById("modal-turno-close").addEventListener("click", cerrarModal);
    document.getElementById("modal-turno-cancel").addEventListener("click", cerrarModal);
    document.getElementById("modal-turno").addEventListener("click", function (e) {
      if (e.target.id === "modal-turno") cerrarModal();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") cerrarModal();
    });

    document.getElementById("form-doctor").addEventListener("submit", crearDoctor);
    document.getElementById("form-paciente").addEventListener("submit", crearPaciente);
    document.getElementById("pac-cancelar-edicion").addEventListener("click", cancelarEdicionPaciente);
    document.getElementById("form-turno").addEventListener("submit", crearTurno);

    document.getElementById("btn-recordatorios").addEventListener("click", function () {
      cambiarVista("recordatorios");
    });
    // La tarjeta del Dashboard usa data-goto="recordatorios" (manejador global).

    document.getElementById("rec-actualizar").addEventListener("click", function () {
      cargarRecordatorios(true);
    });
    const selectorDias = document.getElementById("rec-dias");
    if (selectorDias) {
      selectorDias.value = String(diasRecordatorio());
      selectorDias.addEventListener("change", function (e) {
        state.recDias = parseInt(e.target.value, 10) || 3;
        cargarRecordatorios(true);
      });
    }

    // Disponibilidad: selector de odontólogo, alta y baja de rangos, plantilla
    // sugerida, vaciado de la semana y vista previa de bloques de un día.
    const selDisp = document.getElementById("disp-doctor");
    if (selDisp) {
      selDisp.addEventListener("change", function (e) {
        state.dispDoctor = e.target.value;
        renderDisponibilidad();
      });
    }
    const semanaDisp = document.getElementById("disp-semana");
    if (semanaDisp) {
      semanaDisp.addEventListener("submit", function (e) {
        const form = e.target.closest("[data-disp-form]");
        if (!form) return;
        e.preventDefault();
        const desde = form.querySelector("[data-disp-desde]").value;
        const hasta = form.querySelector("[data-disp-hasta]").value;
        agregarRangoDisponibilidad(form.dataset.dispForm, desde, hasta);
      });
      semanaDisp.addEventListener("click", function (e) {
        const btn = e.target.closest("[data-borrar-rango]");
        if (!btn) return;
        const doctorId = Number(btn.dataset.borrarDoctor);
        const rango = rangosDe(doctorId).find(
          (r) => String(r.id) === String(btn.dataset.borrarRango)
        );
        const detalle = rango
          ? `${nombreDia(rango.dia_semana)} de ${rango.hora_inicio} a ${rango.hora_fin}`
          : "este rango";
        if (!window.confirm(`¿Eliminar ${detalle} de la agenda de ${nombreDoctor(doctorId)}?`)) return;
        borrarRangoDisponibilidad(doctorId, btn.dataset.borrarRango);
      });
    }
    const btnPlantilla = document.getElementById("disp-plantilla");
    if (btnPlantilla) {
      btnPlantilla.addEventListener("click", function () {
        const doctor = doctorDisponibilidad();
        if (!doctor) return;
        const ok = window.confirm(
          `¿Reemplazar la agenda de ${doctor.nombre} por la plantilla (lunes a viernes ` +
            "de 09:00 a 13:00 y de 15:00 a 19:00, sábado sólo por la mañana)?"
        );
        if (ok) reemplazarSemana(PLANTILLA_SEMANAL, "Plantilla semanal aplicada");
      });
    }
    const btnVaciar = document.getElementById("disp-vaciar");
    if (btnVaciar) {
      btnVaciar.addEventListener("click", function () {
        const doctor = doctorDisponibilidad();
        if (!doctor) return;
        const ok = window.confirm(
          `¿Vaciar la agenda semanal de ${doctor.nombre}? Sin rangos no ofrecerá horarios en el calendario.`
        );
        if (ok) reemplazarSemana([], "Agenda vaciada");
      });
    }
    const btnProbar = document.getElementById("disp-probar");
    if (btnProbar) btnProbar.addEventListener("click", probarBloquesDisponibilidad);
    const fechaDisp = document.getElementById("disp-fecha");
    if (fechaDisp) {
      if (!fechaDisp.value) fechaDisp.value = hoyISO();
      fechaDisp.addEventListener("change", probarBloquesDisponibilidad);
    }

    document.getElementById("agenda-prev").addEventListener("click", function () {
      state.agendaFecha = sumarDiasISO(state.agendaFecha, -1);
      renderAgenda();
    });
    document.getElementById("agenda-next").addEventListener("click", function () {
      state.agendaFecha = sumarDiasISO(state.agendaFecha, 1);
      renderAgenda();
    });
    document.getElementById("agenda-hoy").addEventListener("click", function () {
      state.agendaFecha = hoyISO();
      renderAgenda();
    });
    document.getElementById("agenda-fecha").addEventListener("change", function (e) {
      state.agendaFecha = e.target.value || hoyISO();
      renderAgenda();
    });
    document.getElementById("agenda-doctor").addEventListener("change", function (e) {
      state.agendaDoctor = e.target.value;
      renderAgenda();
    });

    // Delegación: botón "Editar" de la tabla de pacientes.
    document.addEventListener("click", function (e) {
      const btn = e.target.closest("[data-editar-paciente]");
      if (btn) editarPaciente(btn.dataset.editarPaciente);
    });

    // Delegación: botones "Responder: Confirma/Cancela" de las tarjetas de turno.
    document.addEventListener("click", function (e) {
      const btn = e.target.closest("[data-simular]");
      if (btn) responderWhatsApp(btn.dataset.wamid, btn.dataset.simular);
    });

    // Delegación: botón verde de WhatsApp de cada recordatorio pendiente. El <a>
    // abre wa.me en otra pestaña (navegación por defecto del enlace); este
    // manejador sólo marca el turno como ENVIADO, sin cancelar el clic.
    document.addEventListener("click", function (e) {
      const btn = e.target.closest("[data-rec-enviar]");
      if (!btn) return;
      const r = state.recordatorios.find(
        (x) => String(x.turno_id) === String(btn.dataset.recEnviar)
      );
      marcarRecordatorioEnviado(btn.dataset.recEnviar, r ? r.paciente : "");
    });
  }

  /* -------------------------------- Init --------------------------------- */
  document.addEventListener("DOMContentLoaded", function () {
    registrarEventos();
    document.getElementById("agenda-fecha").value = state.agendaFecha;
    cambiarVista(state.vista);
    cargarTodo();
  });
  /* --------------------- Puente para módulos externos -------------------- */
  // calendar.js (calendario mensual + panel de disponibilidad) consume esta API
  // pública en lugar de duplicar la lógica de datos, formato y render del sitio.
  window.OdontoApp = {
    API,
    state,
    VISTAS,
    api,
    toast,
    esc,
    porId,
    iniciales,
    avatar,
    colorDe,
    nombreDoctor,
    nombrePaciente,
    isoDe,
    hoyISO,
    ahoraISO,
    sumarDiasISO,
    parteFecha,
    parteHora,
    fechaLarga,
    fechaCorta,
    duracionTexto,
    badgeTurno,
    badgeWhatsApp,
    vacio,
    abrirModal,
    cerrarModal,
    fijarDoctorTurno,
    mostrarErrorTurno,
    bloquear,
    cambiarVista,
    cargarTodo,
    renderAgenda,
    renderRecordatorios,
    cargarRecordatorios,
    // Disponibilidad (agenda semanal por odontólogo)
    cargarDisponibilidad,
    renderDisponibilidad,
    rangosDe,
    probarBloquesDisponibilidad,
    DIAS_SEMANA,
    PLANTILLA_SEMANAL,
  };
})();






