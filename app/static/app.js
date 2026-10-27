/* =========================================================================
   Franciosi Odontología - Panel Principal de la Secretaría
   Conexión real con la API FastAPI (prefijo /api/v1).
   Vanilla JS + Fetch. Sin dependencias.
   ========================================================================= */
(function () {
  "use strict";

  const API = "/api/v1";

  /* Ruta del catálogo de obras sociales. Se arma con una función (una sola
     fuente de verdad) y se usa como argumento de ``api()`` en lugar de repetir
     el *template literal* en cada llamada. */
  const OBRAS_SOCIALES = "/obras-sociales";
  function rutaObraSocial(id) {
    return id ? `${OBRAS_SOCIALES}/${id}` : OBRAS_SOCIALES;
  }

  /* Menú configurable del FAQ Bot (panel «Bot WhatsApp»). */
  const MENU_WHATSAPP = "/whatsapp/menu";
  function rutaOpcionMenu(id) {
    return id ? `${MENU_WHATSAPP}/${id}` : MENU_WHATSAPP;
  }
  // Etiquetas del tipo de respuesta y el detalle que se muestra en la tabla
  // para los tipos que arma el bot con datos del sistema.
  const TIPOS_MENU_WA = {
    TEXTO: "Texto libre",
    PRECIO: "Precio de la consulta",
    OBRAS_SOCIALES: "Obras sociales",
    UBICACION: "Ubicación y horarios",
    SECRETARIA: "Deriva a secretaría",
  };
  const TIPOS_MENU_WA_DETALLE = {
    PRECIO: "Se arma con el precio vigente del nomenclador.",
    OBRAS_SOCIALES: "Lista las coberturas activas del catálogo.",
    UBICACION: "Envía la dirección y los horarios del consultorio.",
    SECRETARIA: "Avisa que una secretaria responde a la brevedad.",
  };

  /* ------------------------------- Estado ------------------------------- */
  const state = {
    doctores: [],
    pacientes: [],
    turnos: [],
    // Catálogo de obras sociales (GET /obras-sociales): alimenta la vista
    // "Gestión de Obras Sociales" y el desplegable del formulario de pacientes.
    obrasSociales: [],
    osMostrarInactivas: false,
    obraSocialEditId: null,
    // Menú configurable del FAQ Bot (GET /whatsapp/menu): alimenta la vista
    // «Bot WhatsApp». ``menuMostrarInactivas`` filtra localmente la tabla.
    menuWhatsApp: [],
    menuMostrarInactivas: false,
    opcionMenuEditId: null,
    doctorEditId: null,
    // Recordatorios pendientes (GET /whatsapp/recordatorios-pendientes):
    // la lista y la ventana de días que muestra la pestaña "Recordatorios".
    recordatorios: [],
    recDias: 3,
    recError: null,
    // Fichas de salud (GET /anamnesis/panel): turnos de los próximos días con
    // el estado de la anamnesis, la alerta médica y el enlace público firmado.
    fichasSalud: [],
    fichasDias: 30,
    fichasSoloPendientes: false,
    fichasError: null,
    // Turno cuya ficha se está cargando o corrigiendo desde el panel interno.
    fichaEditTurnoId: null,
    // Turno cuyo enlace de ficha se está preparando para mandar (modal).
    fichaEnvioTurnoId: null,
    vista: "calendario",
    agendaFecha: hoyISO(),
    agendaDoctor: "",
    turnoEditId: null,
    pacienteEditId: null,
    // Módulo 2: paciente cuya historia clínica se consulta en el Panel Principal
    // (sólo lectura; el alta de evoluciones vive en la vista móvil del odontólogo).
    histPaciente: null,
    // Agenda semanal de los odontólogos (GET /doctores/{id}/disponibilidad):
    // `disponibilidad` es la lista plana de rangos y `dispPorDoctor` el mapa por
    // profesional que consume calendar.js para armar los bloques del día.
    // `dispDoctor` es el profesional que se edita en la pestaña Disponibilidad.
    disponibilidad: [],
    dispPorDoctor: {},
    dispCargada: false,
    dispDoctor: "",
    // Excepciones por fecha de cada odontólogo (GET
    // /doctores/{id}/disponibilidad-especifica): mapa doctor → { fecha → [tramos] }.
    // Es la copia en memoria de lo que el backend aplica como precedencia, para
    // que el calendario no ofrezca bloques de un día bloqueado.
    excepcionesPorDoctor: {},
    /* --- Módulo 3: facturación, caja y liquidaciones --- */
    // Pestaña activa de la vista "Facturación y Caja" y día/caja consultados.
    factTab: "caja",
    factFecha: hoyISO(),
    factCaja: [],
    factResumen: null,
    // Catálogo de prestaciones (activas y dadas de baja) y aranceles de la
    // cobertura elegida en la pantalla de precios.
    factPrestaciones: [],
    factMostrarInactivas: false,
    factPrestacionEditId: null,
    // PDF de aranceles elegido para importar y su último reporte (la vista
    // previa no escribe: el panel muestra qué va a cambiar antes de confirmar).
    factPrestPdfArchivo: null,
    factPrestPdfReporte: null,
    factAranceles: [],
    factArancelEditId: null,
    factArrObraSocial: "",
    // Convenios: la grilla de precios de la cobertura (pantalla "Convenios"), las
    // ediciones todavía sin guardar (``prestacion_id -> monto que cubre la OS``) y
    // el archivo subido al importador masivo con su último reporte.
    factConvenio: null,
    convEdiciones: {},
    convBusqueda: "",
    convArchivo: null,
    convReporte: null,
    // Presentación del período de una obra social y las órdenes tildadas para
    // cerrarlas de a una (cobradas / debitadas).
    factPresentacion: null,
    factTildadas: [],
    // Reporte de producción del mes.
    factMedicos: null,
    factMedMes: mesISO(),
    factMedDoctor: "",
    // Turno que se está cobrando (respuesta de /turnos/{id}/prestaciones-disponibles)
    // y las prácticas tildadas con su cantidad (prestacion_id -> cantidad).
    cobro: null,
    cobroSeleccion: {},
    // Filtro del buscador rápido del modal de cobro (código o nombre de práctica).
    cobroBusqueda: "",
    // Movimiento que se está debitando desde el modal de débito.
    debitoMovimientoId: null,
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
  /* Mes actual en formato del input ``month`` (AAAA-MM). La liquidación de obras
     sociales y el reporte de profesionales trabajan por mes calendario. */
  function mesISO(fecha) {
    const d = fecha ? new Date(fecha) : new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  }
  /* Importes en pesos argentinos: "$ 15.000,00" (formato del mostrador). */
  const MONEDA = new Intl.NumberFormat("es-AR", {
    style: "currency",
    currency: "ARS",
    minimumFractionDigits: 2,
  });
  function fmtMoneda(valor) {
    return MONEDA.format(Number(valor) || 0);
  }
  /* Fecha y hora legibles de un instante de la API (UTC naive). */
  function fechaHora(iso) {
    if (!iso) return "—";
    return `${fechaLarga(parteFecha(iso))} · ${parteHora(iso)} h`;
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

  /* Acciones rápidas de una tarjeta de turno (confirmar asistencia / editar /
     cancelar / reactivar). Los clics se resuelven por delegación en calendar.js.
     *Confirmar asistencia* aparece mientras el turno está PENDIENTE: es la forma
     de dejarlo en verde cuando el paciente avisa por teléfono o llega al
     consultorio, sin depender de que responda el WhatsApp. */
  function panelAccionesTurno(t) {
    const editar = `<button class="btn btn-ghost btn-sm" data-editar-turno="${t.id}">Editar</button>`;
    /* "Cobrar" cierra la visita: elige las prestaciones realizadas, muestra el
       coseguro del mostrador y registra el ingreso en la caja del día. */
    const cobrar =
      `<button class="btn btn-soft btn-sm" data-cobrar-turno="${t.id}" ` +
      `title="Cerrar la visita y cobrar el coseguro">Cobrar</button>`;
    const confirmar =
      t.estado_turno === "PENDIENTE"
        ? `<button class="btn btn-soft btn-sm" data-confirmar-turno="${t.id}">Confirmar asistencia</button>`
        : "";
    const alterno =
      t.estado_turno === "CANCELADO"
        ? `<button class="btn btn-soft btn-sm" data-reactivar-turno="${t.id}">Reactivar</button>`
        : `<button class="btn btn-danger-soft btn-sm" data-cancelar-turno="${t.id}">Cancelar</button>`;
    return `<div class="flex flex-wrap items-center gap-2 mt-2">${editar}${confirmar}${cobrar}${alterno}</div>`;
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

  /* -------------------- Agenda del día por odontólogo -------------------- */
  /* Reparte los turnos del día en una columna por profesional. Entran siempre
     los doctores activos (aunque no tengan turnos, así la grilla muestra a todo
     el consultorio de un vistazo) y también los dados de baja que todavía
     conservan turnos, para no perder ningún renglón de la agenda. */
  function agendaPorDoctor(turnos) {
    const columnas = state.doctores
      .filter((d) => d.activo || turnos.some((t) => t.doctor_id === d.id))
      .map((d) => ({ doctor: d, turnos: turnos.filter((t) => t.doctor_id === d.id) }));
    // Un turno cuyo odontólogo ya no está en el listado igual se muestra.
    turnos.forEach((t) => {
      if (columnas.some((c) => c.doctor.id === t.doctor_id)) return;
      columnas.push({
        doctor: { id: t.doctor_id, nombre: `Doctor #${t.doctor_id}`, especialidad: "" },
        turnos: [t],
      });
    });
    return columnas;
  }

  /* Tarjeta compacta de la columna: la hora, el paciente, su estado y los
     atajos de edición/cancelación (los mismos data-* que resuelve calendar.js
     por delegación). El nombre del odontólogo ya está en la cabecera. */
  function turnoColumna(t) {
    const paciente = nombrePaciente(t.paciente_id);
    const color = clasesHora(t);
    const cancelado = t.estado_turno === "CANCELADO";
    const dur = duracionTexto(t.fecha_hora_inicio, t.fecha_hora_fin);

    return (
      `<div class="${color.fila}">` +
      `<div class="${color.hora}" style="${color.estilo}">` +
      `<div class="h">${esc(parteHora(t.fecha_hora_inicio))}</div>` +
      (dur ? `<div class="d">${esc(dur)}</div>` : "") +
      `</div>` +
      `<div class="flex-1 min-w-0">` +
      `<p class="font-semibold truncate ${cancelado ? "line-through text-slate-400" : "text-slate-900"}" ` +
      `title="${esc(paciente)}">${esc(paciente)}</p>` +
      `<div class="flex flex-wrap items-center gap-1.5 mt-1.5">` +
      badgeTurno(t.estado_turno) +
      badgeWhatsApp(t) +
      `</div>` +
      panelAccionesTurno(t) +
      `</div></div>`
    );
  }

  /* Cabecera y cuerpo de la columna de un odontólogo: avatar, nombre,
     especialidad, cuántos turnos tiene hoy y la lista (o el aviso de que no hay
     ninguno). La franja superior usa el color del profesional en el calendario. */
  function columnaDoctor(doctor, turnos) {
    const color = colorDe(doctor.id);
    const cupo = `${turnos.length} turno${turnos.length === 1 ? "" : "s"}`;

    return (
      `<div class="agenda-doctor">` +
      `<div class="agenda-doctor-cabecera" style="border-top-color:${color}">` +
      avatar(doctor.nombre, doctor.id) +
      `<div class="flex-1 min-w-0">` +
      `<p class="font-bold text-slate-900 truncate" title="${esc(doctor.nombre)}">${esc(doctor.nombre)}</p>` +
      `<p class="text-[11px] text-slate-500 truncate">${esc(doctor.especialidad || "Sin especialidad")}</p>` +
      `</div>` +
      `<span class="badge ${turnos.length ? "badge-brand" : "badge-slate"}">${esc(cupo)}</span>` +
      `</div>` +
      (turnos.length
        ? `<div class="agenda-doctor-turnos">${turnos.map(turnoColumna).join("")}</div>`
        : `<div class="agenda-doctor-vacio">Sin turnos agendados</div>`) +
      `</div>`
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

    document.getElementById("dash-hoy-total").textContent =
      `${delDia.length} turno${delDia.length === 1 ? "" : "s"}`;

    // Una columna por odontólogo con los turnos que tiene agendados hoy.
    const columnas = agendaPorDoctor(delDia);
    document.getElementById("dash-agenda-hoy").innerHTML = columnas.length
      ? columnas.map((c) => columnaDoctor(c.doctor, c.turnos)).join("")
      : vacio("Aún no hay odontólogos registrados", "Cargá un profesional para ver la agenda del día.");

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
      tbody.innerHTML = `<tr><td colspan="6">${vacio("Aún no hay doctores registrados")}</td></tr>`;
      return;
    }
    tbody.innerHTML = state.doctores
      .map((d) => {
        const n = state.turnos.filter((t) => t.doctor_id === d.id).length;
        const estado = d.activo
          ? `<span class="badge badge-green">${ICON.check}Activo</span>`
          : `<span class="badge badge-slate">Inactivo</span>`;
        /* Editar abre el modal (PUT completo) y el segundo botón da de baja
           (DELETE: borrado lógico) o reactiva al profesional (PATCH). */
        const alterno = d.activo
          ? `<button type="button" class="btn btn-danger-soft btn-sm" data-baja-doctor="${d.id}" ` +
            `title="Dar de baja a ${esc(d.nombre)} sin borrar su historial">Dar de baja</button>`
          : `<button type="button" class="btn btn-soft btn-sm" data-reactivar-doctor="${d.id}" ` +
            `title="Volver a ofrecer a ${esc(d.nombre)} en las agendas">Reactivar</button>`;
        return (
          `<tr${d.activo ? "" : ' class="opacity-70"'}>` +
          `<td><div class="flex items-center gap-3">${avatar(d.nombre, d.id)}` +
          `<span class="font-semibold text-slate-800">${esc(d.nombre)}</span></div></td>` +
          `<td>${esc(d.especialidad)}</td>` +
          `<td><span class="fact-med-tipo">${esc(textoPagoDoctor(d))}</span></td>` +
          `<td>${estado}</td>` +
          `<td><span class="badge badge-brand">${n}</span></td>` +
          `<td><div class="flex items-center justify-end gap-2">` +
          `<button type="button" class="btn btn-ghost btn-sm" data-editar-doctor="${d.id}" ` +
          `title="Editar los datos de ${esc(d.nombre)}">Editar</button>` +
          `${alterno}</div></td></tr>`
        );
      })
      .join("");
  }

  /* --------------------------- Obras sociales ---------------------------- */
  /* Catálogo que alimenta el desplegable del formulario de pacientes. La lista
     llega completa (activas y dadas de baja) en una sola petición; el filtro de
     la vista es local (``state.osMostrarInactivas``). */
  function obrasSocialesActivas() {
    return state.obrasSociales.filter((o) => o.activa);
  }

  function obraSocialPorId(id) {
    return porId(state.obrasSociales, Number(id)) || null;
  }

  function nombreObraSocial(id) {
    const obra = obraSocialPorId(id);
    return obra ? obra.nombre : "—";
  }

  function renderObrasSociales() {
    const contador = document.getElementById("count-obras-sociales");
    const activas = obrasSocialesActivas();
    if (contador) contador.textContent = `${activas.length} activa${activas.length === 1 ? "" : "s"}`;

    // Insignia del menú con la cantidad de coberturas ofrecidas.
    const navBadge = document.getElementById("nav-os-activas");
    if (navBadge) {
      navBadge.textContent = String(activas.length);
      navBadge.classList.toggle("hide", activas.length === 0);
    }

    const sub = document.getElementById("os-subtitulo");
    if (sub) {
      const total = state.obrasSociales.length;
      sub.textContent =
        `${activas.length} de ${total} cobertura${total === 1 ? "" : "s"} disponibles ` +
        "para asignar en el formulario de pacientes.";
    }

    const tbody = document.getElementById("lista-obras-sociales");
    if (!tbody) return;

    const visibles = state.osMostrarInactivas
      ? state.obrasSociales
      : obrasSocialesActivas();

    if (!visibles.length) {
      tbody.innerHTML =
        `<tr><td colspan="5">` +
        vacio(
          state.obrasSociales.length ? "No hay coberturas activas" : "Aún no hay obras sociales",
          state.obrasSociales.length
            ? "Activá el interruptor «Mostrar las dadas de baja» o registrá una nueva cobertura."
            : "Usá el botón «Nueva obra social» para cargar la primera cobertura."
        ) +
        `</td></tr>`;
      return;
    }

    const guion = '<span class="text-slate-400">—</span>';
    tbody.innerHTML = visibles
      .map((o) => {
        const n = state.pacientes.filter(
          (p) => String(p.obra_social_id) === String(o.id)
        ).length;
        const estado = o.activa
          ? `<span class="badge badge-green">${ICON.check}Activa</span>`
          : `<span class="badge badge-slate">Dada de baja</span>`;
        // La baja es lógica (DELETE → activa=false) y se revierte con PATCH.
        const alterno = o.activa
          ? `<button type="button" class="btn btn-danger-soft btn-sm" data-baja-os="${o.id}" ` +
            `title="Dejar de ofrecer ${esc(o.nombre)} sin borrar la cobertura">Desactivar</button>`
          : `<button type="button" class="btn btn-soft btn-sm" data-reactivar-os="${o.id}" ` +
            `title="Volver a ofrecer ${esc(o.nombre)} en el formulario de pacientes">Reactivar</button>`;
        return (
          `<tr${o.activa ? "" : ' class="opacity-70"'}>` +
          `<td><div class="flex items-center gap-3">` +
          `<span class="w-9 h-9 rounded-xl bg-brand-50 text-brand-700 flex items-center justify-center font-bold text-xs flex-none">` +
          `${esc(iniciales(o.nombre))}</span>` +
          `<span class="font-semibold text-slate-800">${esc(o.nombre)}</span></div></td>` +
          `<td class="font-mono text-xs">${o.codigo ? esc(o.codigo) : guion}</td>` +
          `<td>${estado}</td>` +
          `<td><span class="badge badge-brand">${n}</span></td>` +
          `<td><div class="flex items-center justify-end gap-2">` +
          `<button type="button" class="btn btn-ghost btn-sm" data-editar-os="${o.id}" ` +
          `title="Editar ${esc(o.nombre)}">Editar</button>` +
          `${alterno}</div></td></tr>`
        );
      })
      .join("");
  }

  /* ------------------ Menú configurable del Bot WhatsApp ----------------- */
  function opcionesMenuWaActivas() {
    return state.menuWhatsApp.filter((o) => o.activa);
  }

  function opcionMenuPorId(id) {
    return state.menuWhatsApp.find((o) => String(o.id) === String(id)) || null;
  }

  /* Texto corto para la columna «Respuesta»: el mensaje cargado a mano o la
     explicación de lo que arma el bot con los datos del sistema. */
  function resumenRespuestaMenu(opcion) {
    if (opcion.tipo === "TEXTO") {
      const texto = String(opcion.respuesta || "").trim();
      if (!texto) return '<span class="text-rose-600">Sin texto configurado</span>';
      return esc(texto.length > 90 ? `${texto.slice(0, 90)}…` : texto);
    }
    return `<span class="text-slate-500">${esc(TIPOS_MENU_WA_DETALLE[opcion.tipo] || "—")}</span>`;
  }

  function renderMenuWhatsApp() {
    const activas = opcionesMenuWaActivas();
    const total = state.menuWhatsApp.length;

    const contador = document.getElementById("count-menu-whatsapp");
    if (contador) {
      contador.textContent = `${activas.length} activa${activas.length === 1 ? "" : "s"}`;
    }

    // Insignia del menú lateral con la cantidad de opciones que ve el paciente.
    const navBadge = document.getElementById("nav-menu-wa-activas");
    if (navBadge) {
      navBadge.textContent = String(activas.length);
      navBadge.classList.toggle("hide", activas.length === 0);
    }

    const sub = document.getElementById("menu-wa-subtitulo");
    if (sub) {
      sub.textContent =
        `${activas.length} de ${total} opción${total === 1 ? "" : "es"} activa` +
        `${activas.length === 1 ? "" : "s"} en el menú que recibe el paciente al escribir «hola».`;
    }

    const tbody = document.getElementById("lista-menu-whatsapp");
    if (!tbody) return;

    const visibles = state.menuMostrarInactivas
      ? state.menuWhatsApp
      : opcionesMenuWaActivas();

    if (!visibles.length) {
      tbody.innerHTML =
        `<tr><td colspan="6">` +
        vacio(
          total ? "No hay opciones activas" : "El bot todavía no tiene un menú configurado",
          total
            ? "Activá el interruptor «Mostrar las dadas de baja» o creá una opción nueva."
            : "Usá «Nueva opción» para agregar la primera, o «Restaurar menú histórico» para volver a las cuatro de siempre."
        ) +
        `</td></tr>`;
      actualizarNumerosDisponiblesMenu();
      return;
    }

    tbody.innerHTML = visibles
      .map((o) => {
        const estado = o.activa
          ? `<span class="badge badge-green">${ICON.check}Activa</span>`
          : `<span class="badge badge-slate">Dada de baja</span>`;
        // DELETE /whatsapp/menu/{id} es baja lógica; PATCH {activa:true} reactiva.
        const alterno = o.activa
          ? `<button type="button" class="btn btn-danger-soft btn-sm" data-baja-menu="${o.id}" ` +
            `title="Dejar de mostrar «${esc(o.titulo)}» en el menú">Desactivar</button>`
          : `<button type="button" class="btn btn-soft btn-sm" data-reactivar-menu="${o.id}" ` +
            `title="Volver a mostrar «${esc(o.titulo)}» en el menú">Reactivar</button>`;
        return (
          `<tr${o.activa ? "" : ' class="opacity-70"'}>` +
          `<td><span class="w-8 h-8 rounded-lg bg-brand-50 text-brand-700 flex items-center justify-center font-extrabold flex-none">` +
          `${esc(o.numero)}</span></td>` +
          `<td><p class="font-semibold text-slate-800">${esc(o.titulo)}</p>` +
          `<p class="text-[11px] text-slate-400">Orden ${esc(o.orden)}</p></td>` +
          `<td><span class="badge badge-slate">${esc(TIPOS_MENU_WA[o.tipo] || o.tipo)}</span></td>` +
          `<td class="text-xs max-w-[240px]">${resumenRespuestaMenu(o)}</td>` +
          `<td>${estado}</td>` +
          `<td><div class="flex items-center justify-end gap-2">` +
          `<button type="button" class="btn btn-ghost btn-sm" data-editar-menu="${o.id}" ` +
          `title="Editar «${esc(o.titulo)}»">Editar</button>${alterno}</div></td></tr>`
        );
      })
      .join("");

    // Si el modal está abierto, refresca qué teclas quedan libres.
    actualizarNumerosDisponiblesMenu();
  }

  /* GET /whatsapp/menu: trae todas las opciones (activas y dadas de baja). */
  async function cargarMenuWhatsApp() {
    try {
      state.menuWhatsApp = (await api(MENU_WHATSAPP)) || [];
      renderMenuWhatsApp();
    } catch (err) {
      toast("error", "No se pudo cargar el menú del bot", err.message);
    }
  }

  /* GET /whatsapp/menu/vista-previa: el texto exacto que manda el webhook,
     armado por el backend con las mismas funciones que el bot. */
  async function cargarVistaPreviaMenu() {
    const salida = document.getElementById("menu-wa-preview");
    if (!salida) return;
    try {
      const previa = await api(`${MENU_WHATSAPP}/vista-previa`);
      salida.textContent = previa.texto || "(El menú no tiene opciones activas.)";
    } catch (err) {
      salida.textContent = `No se pudo cargar la vista previa: ${err.message}`;
    }
  }

  function renderPacientes() {
    document.getElementById("count-pacientes").textContent = state.pacientes.length;
    const tbody = document.getElementById("lista-pacientes");
    if (!state.pacientes.length) {
      tbody.innerHTML = `<tr><td colspan="7">${vacio("Aún no hay pacientes registrados")}</td></tr>`;
      return;
    }
    const guion = '<span class="text-slate-400">—</span>';
    /* Los filiatorios que carga la ficha médica se sincronizan en el paciente:
       la fecha de nacimiento (con la edad calculada por la API) se muestra
       debajo del nombre para no sumar otra columna a la tabla. */
    const fechaLegible = (iso) =>
      window.FICHA_MEDICA ? FICHA_MEDICA.fechaLegible(iso) : iso;
    tbody.innerHTML = state.pacientes
      .map((p) => {
        const n = state.turnos.filter((t) => t.paciente_id === p.id).length;
        const nacimiento = p.fecha_nacimiento
          ? `<p class="text-[11px] text-slate-400">Nac. ${esc(fechaLegible(p.fecha_nacimiento))}` +
            (p.edad === null || p.edad === undefined ? "" : ` · ${p.edad} años`) +
            `</p>`
          : "";
        const cobertura =
          p.obra_social || p.numero_afiliado
            ? `<p class="font-semibold text-slate-800">${p.obra_social ? esc(p.obra_social) : "Obra social s/d"}</p>` +
              (p.numero_afiliado
                ? `<p class="text-[11px] text-slate-400 font-mono">Nº ${esc(p.numero_afiliado)}</p>`
                : "")
            : guion;
        return (
          `<tr><td><div class="flex items-center gap-3">${avatar(p.nombre, p.id)}` +
          `<div><span class="font-semibold text-slate-800">${esc(p.nombre)}</span>${nacimiento}</div></div></td>` +
          `<td class="font-mono text-xs">${p.dni ? esc(p.dni) : guion}</td>` +
          `<td class="font-mono text-xs">${esc(p.telefono)}</td>` +
          `<td>${cobertura}</td>` +
          `<td>${p.email ? esc(p.email) : guion}</td>` +
          `<td><span class="badge badge-brand">${n}</span></td>` +
          `<td class="text-right">` +
          `<div class="flex items-center justify-end gap-2">` +
          `<button type="button" class="btn btn-ghost btn-sm" data-historia="${p.id}" ` +
          `title="Ver la historia clínica de ${esc(p.nombre)}">Historia</button>` +
          `<button type="button" class="btn btn-ghost btn-sm" data-editar-paciente="${p.id}" ` +
          `title="Editar los datos de ${esc(p.nombre)}">Editar</button>` +
          `</div></td></tr>`
        );
      })
      .join("");
  }

  /* ================== Módulo 2 · Historia clínica (sólo lectura) =========== */
  /* La secretaría consulta el historial de un paciente: busca por nombre o DNI
     (la búsqueda la resuelve el backend, sin acentos y con el DNI sin puntos) y
     arma la línea de tiempo de atenciones con ``GET
     /pacientes/{id}/historia-clinica``.

     Este panel **no escribe** evoluciones médicas: no hay ninguna llamada al
     endpoint de alta. Las evoluciones las carga el odontólogo desde su celular y
     son inmutables (la API no expone ``PUT``/``PATCH``/``DELETE``). */
  let histTimer = null;

  function fichaHistoriaHTML(p, activo) {
    return (
      `<button type="button" class="hist-ficha${activo ? " hist-ficha-activa" : ""}" ` +
      `data-hist-paciente="${p.id}">` +
      `<div class="min-w-0 flex-1">` +
      `<span class="hist-ficha-nombre">${esc(p.nombre)}</span>` +
      `<span class="hist-ficha-detalle">${esc(p.dni ? `DNI ${p.dni}` : p.telefono)}</span>` +
      `</div>` +
      `<svg class="w-4 h-4 flex-none text-slate-400" viewBox="0 0 24 24" fill="none" ` +
      `stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">` +
      `<path d="m9 18 6-6-6-6"/></svg>` +
      `</button>`
    );
  }

  /* Resultados del buscador: hasta 20 fichas, con la elegida marcada. */
  function renderResultadosHistoria(pacientes, consulta) {
    const cont = document.getElementById("hist-panel-resultados");
    if (!cont) return;
    if (!consulta) {
      cont.innerHTML =
        `<p class="hist-nota">Escribí al menos dos letras del nombre, o el DNI, y elegí el paciente.</p>`;
      return;
    }
    if (!pacientes.length) {
      cont.innerHTML = `<p class="hist-nota">Sin resultados para “${esc(consulta)}”.</p>`;
      return;
    }
    cont.innerHTML = pacientes
      .slice(0, 20)
      .map((p) => fichaHistoriaHTML(p, String(p.id) === String(state.histPaciente)))
      .join("");
  }

  function programarBusquedaHistoria(texto) {
    if (histTimer) clearTimeout(histTimer);
    histTimer = setTimeout(() => buscarHistoriaPanel(texto), 220);
  }

  async function buscarHistoriaPanel(texto) {
    const cont = document.getElementById("hist-panel-resultados");
    if (!cont) return;
    const consulta = String(texto || "").trim();
    if (consulta.length < 2) { renderResultadosHistoria([], ""); return; }
    cont.innerHTML = `<p class="hist-nota">Buscando…</p>`;
    try {
      const pacientes = await api(`/pacientes?buscar=${encodeURIComponent(consulta)}`);
      renderResultadosHistoria(Array.isArray(pacientes) ? pacientes : [], consulta);
    } catch (err) {
      cont.innerHTML = `<p class="hist-nota">${esc(err.message)}</p>`;
    }
  }

  function vacioHistoria(titulo, sub) {
    return (
      `<div class="hist-vacio">` +
      `<p class="font-semibold text-slate-600">${esc(titulo)}</p>` +
      (sub ? `<p class="mt-1">${esc(sub)}</p>` : "") +
      `</div>`
    );
  }

  /* Una atención de la línea de tiempo: fecha, odontólogo y campos clínicos. */
  function evolucionHistoriaHTML(ev) {
    const campos = [
      ["Motivo de consulta", ev.motivo_consulta],
      ["Diagnóstico", ev.diagnostico],
      ["Tratamiento efectuado", ev.tratamiento_efectuado],
      ["Observaciones", ev.observaciones],
    ].filter((campo) => campo[1]);
    const archivos = Array.isArray(ev.archivos) ? ev.archivos : [];
    const hora = parteHora(ev.fecha_atencion);
    return (
      `<article class="hist-item"><div class="hist-cuerpo">` +
      `<p class="hist-fecha">${esc(fechaLarga(parteFecha(ev.fecha_atencion)))}` +
      (hora !== "00:00" ? ` · ${esc(hora)} hs` : "") +
      `</p>` +
      `<span class="hist-doctor">${esc(ev.doctor_nombre || `Odontólogo #${ev.doctor_id}`)}` +
      (ev.doctor_especialidad ? ` · ${esc(ev.doctor_especialidad)}` : "") +
      `</span>` +
      `<div class="hist-campos">` +
      campos
        .map(
          (campo) =>
            `<div><p class="hist-campo-titulo">${esc(campo[0])}</p>` +
            `<p class="hist-campo-texto">${esc(campo[1])}</p></div>`
        )
        .join("") +
      `</div>` +
      (archivos.length
        ? `<div class="hist-archivos">` +
          archivos
            .map(
              (a) =>
                `<a class="hist-archivo" href="${esc(a.url_archivo)}" target="_blank" ` +
                `rel="noopener">${esc(a.tipo_archivo)}</a>`
            )
            .join("") +
          `</div>`
        : "") +
      `</div></article>`
    );
  }

  /* ================= Módulo 4 · Ficha de salud (sólo lectura) ============== */
  /* El paciente completa la ficha desde el enlace público de WhatsApp y el
     odontólogo la ve acá, junto a la línea de tiempo, antes de atender. Los
     antecedentes de riesgo se resaltan y disparan un aviso flotante que se
     puede cerrar. El detalle lo arma ``FICHA_MEDICA.renderResumen``. */

  /* Tarjeta con la declaración del paciente. Si todavía no la envió, se
     explica cómo se completa: el enlace sale en el recordatorio de WhatsApp. */
  function antecedentesSaludHTML(detalle) {
    const a = detalle && detalle.antecedentes_salud;
    const pacienteId = detalle && detalle.paciente_id;
    const botonOdonto = pacienteId
      ? `<button type="button" class="salud-btn-odonto"` +
        ` data-odontograma="${esc(pacienteId)}"` +
        ` data-odontograma-nombre="${esc((detalle && detalle.paciente_nombre) || "")}">` +
        `Odontograma</button>`
      : "";
    /* La Historia Clínica General se imprime sobre la plantilla de papel: el
       navegador abre el PDF en otra pestaña para imprimirlo o guardarlo. */
    const botonPdf = pacienteId
      ? `<button type="button" class="salud-btn-pdf"` +
        ` data-ficha-pdf="${esc(pacienteId)}"` +
        ` title="Imprimir la Historia Clínica General">` +
        `Imprimir</button>`
      : "";

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
        `<p class="salud-sub">El paciente todavía no completó la anamnesis.</p>` +
        `</div>` +
        `<div class="salud-acciones">` +
        `<span class="badge badge-slate">Pendiente</span>` +
        botonOdonto +
        botonPdf +
        `</div>` +
        `</div>` +
        `<p class="salud-pendiente">Se envía desde el enlace de la ficha que el paciente recibe por WhatsApp. Al completarla, sus antecedentes aparecen acá y en la vista móvil.</p>` +
        `</section>`
      );
    }

    const motivos = Array.isArray(detalle.motivos_alerta)
      ? detalle.motivos_alerta
      : (Array.isArray(a.motivos_alerta) ? a.motivos_alerta : []);
    const alerta = Boolean(detalle.alerta_medica ?? a.alerta_medica ?? motivos.length);
    const actualizada = a.fecha_completado
      ? `<p class="salud-sub">Actualizada el ${esc(fechaHora(a.fecha_completado))}</p>`
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
      `<div class="salud-acciones">` +
      `<span class="badge ${alerta ? "badge-red" : "badge-green"}">${alerta ? "Alerta médica" : "Sin alertas"}</span>` +
      botonOdonto +
      botonPdf +
      `</div>` +
      `</div>` +
      `<div class="salud-resumen" data-resumen-salud></div>` +
      (alerta
        ? `<p class="salud-alerta-nota"><strong>Revisar antes de atender:</strong> ${esc(motivos.join(" · "))}</p>`
        : "") +
      `</section>`
    );
  }

  function ocultarAlertaPanel() {
    const banner = document.getElementById("hist-panel-alerta");
    if (banner) banner.classList.add("hide");
  }

  function renderAntecedentesPanel(detalle) {
    const cont = document.getElementById("hist-panel-antecedentes");
    if (cont) {
      cont.innerHTML = antecedentesSaludHTML(detalle);
      const resumen = cont.querySelector("[data-resumen-salud]");
      if (resumen && window.FICHA_MEDICA) {
        FICHA_MEDICA.renderResumen(
          resumen,
          (detalle && detalle.antecedentes_salud) || {}
        );
      }
    }

    const banner = document.getElementById("hist-panel-alerta");
    if (!banner) return;
    const motivos = Array.isArray(detalle.motivos_alerta)
      ? detalle.motivos_alerta
      : [];
    if (detalle.alerta_medica && motivos.length) {
      const texto = document.getElementById("hist-panel-alerta-motivos");
      if (texto) texto.textContent = motivos.join(" · ");
      banner.classList.remove("hide");
    } else {
      banner.classList.add("hide");
    }
  }


  async function abrirHistoriaPanel(pacienteId) {
    const cont = document.getElementById("hist-panel-timeline");
    if (!cont) return;
    const paciente =
      state.pacientes.find((p) => String(p.id) === String(pacienteId)) || null;
    state.histPaciente = Number(pacienteId);
    document.getElementById("hist-panel-paciente").textContent = paciente
      ? paciente.nombre
      : `Paciente #${pacienteId}`;
    document.getElementById("hist-panel-total").textContent = "";
    document.getElementById("hist-panel-ficha").textContent = "Cargando…";
    // La ficha elegida queda fija como único resultado del buscador.
    renderResultadosHistoria(paciente ? [paciente] : [], paciente ? paciente.nombre : String(pacienteId));
    cont.innerHTML = `<p class="hist-nota">Cargando la historia clínica…</p>`;
    const salud = document.getElementById("hist-panel-antecedentes");
    if (salud) salud.innerHTML = `<p class="hist-nota">Cargando la ficha de salud…</p>`;
    ocultarAlertaPanel();
    try {
      const detalle = await api(`/pacientes/${pacienteId}/historia-clinica`);
      document.getElementById("hist-panel-paciente").textContent =
        detalle.paciente_nombre +
        (detalle.paciente_dni ? ` · DNI ${detalle.paciente_dni}` : "");
      document.getElementById("hist-panel-total").textContent =
        detalle.total_evoluciones === 1
          ? "1 atención registrada"
          : `${detalle.total_evoluciones} atenciones registradas`;
      document.getElementById("hist-panel-ficha").textContent = `Ficha ${detalle.numero_ficha}`;
      renderAntecedentesPanel(detalle);
      const evoluciones = Array.isArray(detalle.evoluciones) ? detalle.evoluciones : [];
      cont.innerHTML = evoluciones.length
        ? evoluciones.map(evolucionHistoriaHTML).join("")
        : vacioHistoria(
            "Sin atenciones registradas",
            "Cuando el odontólogo cargue la primera evolución, va a aparecer acá."
          );
    } catch (err) {
      document.getElementById("hist-panel-ficha").textContent = "Sin ficha abierta";
      cont.innerHTML = `<p class="hist-nota">${esc(err.message)}</p>`;
      if (salud) salud.innerHTML = `<p class="hist-nota">No se pudo cargar la ficha de salud.</p>`;
      ocultarAlertaPanel();
    }
  }

  /* Odontólogos que pueden recibir turnos: los que están activos. Las consultas
     de selección del panel (filtros, modal de turno y editor de agenda) se
     limitan a ellos; la vista "Doctores" sí lista también los dados de baja para
     poder reactivarlos. */
  function doctoresActivos() {
    return state.doctores.filter((d) => d.activo);
  }
  /* Desplegable de obras sociales del formulario de pacientes: sólo activas, más
     la opción "sin cobertura". Se preserva la selección actual. */
  function cargarObrasSocialesSelect() {
    const sel = document.getElementById("pac-obra-social");
    if (!sel) return;
    const valor = sel.value;
    const activas = obrasSocialesActivas();
    sel.innerHTML =
      `<option value="">Sin cobertura / particular</option>` +
      activas
        .map(
          (o) =>
            `<option value="${o.id}">${esc(o.nombre)}` +
            (o.codigo ? ` · ${esc(o.codigo)}` : "") +
            `</option>`
        )
        .join("");
    if (valor) sel.value = valor;
  }

  function cargarSelectores() {
    // Filtro de agenda (preserva la selección actual). Sólo profesionales
    // activos: los dados de baja no pueden recibir turnos.
    const filtro = document.getElementById("agenda-doctor");
    const activos = doctoresActivos();
    filtro.innerHTML =
      `<option value="">Todos los doctores</option>` +
      activos.map((d) => `<option value="${d.id}">${esc(d.nombre)}</option>`).join("");
    if (state.agendaDoctor && !porId(activos, Number(state.agendaDoctor))) {
      state.agendaDoctor = "";
    }
    filtro.value = state.agendaDoctor;

    // Desplegable de coberturas del formulario de pacientes.
    cargarObrasSocialesSelect();

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

    // El modal de nuevo turno ofrece los mismos profesionales activos que el
    // filtro de la agenda (los dados de baja no pueden recibir turnos).
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
    renderFichasSalud();
    renderDoctores();
    renderPacientes();
    renderObrasSociales();
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
        // El catálogo completo (activas y dadas de baja): la vista de gestión
        // filtra localmente y el desplegable sólo ofrece las activas.
        api(OBRAS_SOCIALES),
      ]);
      state.doctores = datos[0] || [];
      state.pacientes = datos[1] || [];
      state.turnos = datos[2] || [];
      state.obrasSociales = datos[3] || [];
      renderTodo();
      estadoApi(true);
      // Los recordatorios pendientes se piden aparte para no demorar el panel.
      cargarRecordatorios();
      // Las fichas de salud alimentan su propio panel y el contador del menú.
      cargarFichasSalud();
      // La agenda semanal de cada odontólogo también llega en su propia petición.
      cargarDisponibilidad();
      // El menú del bot se pide aparte para no frenar el panel si la consulta falla.
      cargarMenuWhatsApp();
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

  /* Las excepciones se traen de la API por rango de fechas: se pide con holgura
     (medio año atrás y un año adelante) para que el calendario pueda mostrar los
     días personalizados sin consultar al navegar; las fechas de afuera se
     completan on-demand (ver `cargarExcepcionesDeFecha`). */
  const EXCEPCIONES_DESDE = sumarDiasISO(hoyISO(), -186);
  const EXCEPCIONES_HASTA = sumarDiasISO(hoyISO(), 366);

  /* Rangos de fechas cuyas excepciones ya se pidieron a la API (siempre de todos
     los odontólogos a la vez). El calendario consulta los meses que se salen de
     la ventana inicial y así no repite peticiones en cada render. */
  let excepcionesCargadas = [];

  function rangoCargado(desde, hasta) {
    return excepcionesCargadas.some((r) => desde >= r.desde && hasta <= r.hasta);
  }

  /* Todas las fechas ISO entre dos extremos, inclusive (con tope de seguridad
     para no armar listas infinitas si llega un rango inesperado). */
  function fechasDelRango(desde, hasta) {
    const fechas = [];
    const fin = parteFecha(hasta);
    let iso = parteFecha(desde);
    for (let i = 0; iso <= fin && i < 400; i += 1) {
      fechas.push(iso);
      iso = sumarDiasISO(iso, 1);
    }
    return fechas;
  }

  /* --------------------- Excepciones de un día concreto ------------------- */
  /* La precedencia final la resuelve el backend, pero el calendario necesita
     saberla antes de pintar la grilla (si no, ofrecería bloques de la semana en
     un día bloqueado). Estas funciones replican la misma regla sobre los datos
     que ya están en memoria: si hay CUALQUIER excepción para esa fecha, la
     agenda semanal se ignora y el día se calcula con la unión de los
     HABILITADO menos la de los BLOQUEADO. */

  function aMinutos(hhmm) {
    const [h, m] = String(hhmm || "0:0").split(":").map(Number);
    return (h || 0) * 60 + (m || 0);
  }
  function aHora(minutos) {
    const h = Math.floor(minutos / 60);
    const m = minutos % 60;
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  }
  function esTramoHabilitado(e) {
    return String(e.tipo) === "HABILITADO";
  }
  /* Día de la semana de una fecha ISO en la convención del backend y de
     Date.getDay(): 0 = domingo … 6 = sábado. */
  function diaSemanaDe(iso) {
    const [y, m, d] = parteFecha(iso).split("-").map(Number);
    return new Date(y, m - 1, d).getDay();
  }

  /* Excepciones cargadas para un odontólogo en una fecha ([] si no hay). */
  function excepcionesDeFecha(doctorId, iso) {
    const mapa = state.excepcionesPorDoctor[String(doctorId)];
    const fecha = parteFecha(iso);
    return (mapa && mapa[fecha]) || [];
  }

  /* Une tramos solapados; los contiguos quedan separados a propósito (cada uno
     conserva su propio compás de bloques, igual que el backend). */
  function unirTramos(tramos) {
    const ordenados = tramos.slice().sort((a, b) => a.inicio - b.inicio);
    const unidos = [];
    ordenados.forEach(function (t) {
      const ultimo = unidos[unidos.length - 1];
      if (ultimo && t.inicio < ultimo.fin) ultimo.fin = Math.max(ultimo.fin, t.fin);
      else unidos.push({ inicio: t.inicio, fin: t.fin });
    });
    return unidos;
  }

  /* Devuelve `base` sin las porciones cubiertas por `quitar` (ambos ya unidos).
     Un tramo partido por un bloqueo se devuelve como dos tramos. */
  function restarTramos(base, quitar) {
    let restante = base.slice();
    quitar.forEach(function (bloque) {
      const recortado = [];
      restante.forEach(function (t) {
        if (bloque.fin <= t.inicio || bloque.inicio >= t.fin) {
          recortado.push(t); // sin contacto: queda igual
          return;
        }
        if (bloque.inicio > t.inicio) recortado.push({ inicio: t.inicio, fin: bloque.inicio });
        if (bloque.fin < t.fin) recortado.push({ inicio: bloque.fin, fin: t.fin });
      });
      restante = recortado;
    });
    return restante.filter((t) => t.inicio < t.fin);
  }

  /* Rangos que realmente rigen una fecha: las excepciones cargadas o, si no hay
     ninguna, la agenda semanal del día de la semana correspondiente. `origen`
     viaja con cada rango para que la UI aclare de dónde salió. */
  function rangosVigentesDeFecha(doctorId, iso) {
    const fecha = parteFecha(iso);
    const excepciones = excepcionesDeFecha(doctorId, fecha);
    if (!excepciones.length) {
      const dia = diaSemanaDe(fecha);
      return rangosDe(doctorId)
        .filter((r) => Number(r.dia_semana) === dia)
        .map((r) => ({ hora_inicio: r.hora_inicio, hora_fin: r.hora_fin, origen: "SEMANAL" }));
    }
    const habilitados = unirTramos(
      excepciones.filter(esTramoHabilitado).map((e) => ({
        inicio: aMinutos(e.hora_inicio),
        fin: aMinutos(e.hora_fin),
      }))
    );
    const bloqueados = unirTramos(
      excepciones.filter((e) => !esTramoHabilitado(e)).map((e) => ({
        inicio: aMinutos(e.hora_inicio),
        fin: aMinutos(e.hora_fin),
      }))
    );
    return restarTramos(habilitados, bloqueados).map((t) => ({
      hora_inicio: aHora(t.inicio),
      hora_fin: aHora(t.fin),
      origen: "ESPECIFICO",
    }));
  }

  async function cargarDisponibilidad() {
    try {
      const ventana = `?desde=${EXCEPCIONES_DESDE}&hasta=${EXCEPCIONES_HASTA}`;
      const [listas, excepciones] = await Promise.all([
        Promise.all(state.doctores.map((d) => api(`/doctores/${d.id}/disponibilidad`))),
        // Las excepciones son un extra: si el endpoint falla, la agenda semanal
        // igual se dibuja (el calendario simplemente no marca días especiales).
        Promise.all(
          state.doctores.map((d) =>
            api(`/doctores/${d.id}/disponibilidad-especifica${ventana}`).catch(() => null)
          )
        ),
      ]);
      const mapa = {};
      const plana = [];
      state.excepcionesPorDoctor = {};
      excepcionesCargadas = [];
      let conExcepciones = 0;
      state.doctores.forEach(function (d, i) {
        const rangos = listas[i] || [];
        mapa[String(d.id)] = rangos;
        rangos.forEach((r) => plana.push(r));
        if (excepciones[i] === null) return; // endpoint no disponible / caído
        conExcepciones += 1;
        indexarExcepciones(d.id, excepciones[i], null);
      });
      // Si la API de excepciones respondió, toda la ventana inicial queda cacheada
      // y el calendario no la vuelve a pedir al pintar sus meses.
      if (conExcepciones) {
        excepcionesCargadas.push({ desde: EXCEPCIONES_DESDE, hasta: EXCEPCIONES_HASTA });
      }
      state.dispPorDoctor = mapa;
      state.disponibilidad = plana;
      state.dispCargada = true;
      renderDisponibilidad();
      renderExcepciones();
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

  /* ---------------- Excepciones por fecha (tarjeta "Gestionar un día") ------ */

  /* Guarda las excepciones recibidas de la API en el índice doctor → fecha.
     `fechas` indica qué días hay que descartar antes de volver a llenarlos:
     `null` = todo el bloque traído del servidor; una lista = sólo esos días. */
  function indexarExcepciones(doctorId, lista, fechas) {
    const clave = String(doctorId);
    const mapa = state.excepcionesPorDoctor[clave] || (state.excepcionesPorDoctor[clave] = {});
    const objetivos = fechas ? fechas.map(parteFecha) : Object.keys(mapa);
    objetivos.forEach(function (f) { delete mapa[f]; });
    (lista || []).forEach(function (e) {
      const f = parteFecha(e.fecha);
      if (!mapa[f]) mapa[f] = [];
      mapa[f].push(e);
    });
    Object.keys(mapa).forEach(function (f) {
      mapa[f].sort((a, b) => String(a.hora_inicio).localeCompare(String(b.hora_inicio)));
    });
    return mapa;
  }

  /* Fecha que se está editando en la tarjeta "Gestionar un día específico". */
  function fechaExcepciones() {
    const input = document.getElementById("excep-fecha");
    if (input && !input.value) input.value = hoyISO();
    return (input && input.value) || hoyISO();
  }

  /* Chip de un tramo de la fecha: verde si abre horas, rojo si las cierra. */
  function chipExcepcion(e) {
    const abrir = esTramoHabilitado(e);
    return (
      `<span class="excep-chip ${abrir ? "abrir" : "bloquear"}">` +
      `<span class="excep-chip-tipo">${abrir ? "Abre" : "Bloquea"}</span>` +
      `<span>${esc(e.hora_inicio)} &ndash; ${esc(e.hora_fin)}</span>` +
      `<button type="button" class="excep-chip-x" data-borrar-excepcion="${e.id}" ` +
      `data-excepcion-doctor="${e.doctor_id}" data-excepcion-fecha="${esc(parteFecha(e.fecha))}" ` +
      `title="Eliminar este tramo del día" ` +
      `aria-label="Eliminar el tramo de ${esc(e.hora_inicio)} a ${esc(e.hora_fin)}">` +
      `<svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
      `stroke-width="2.6" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>` +
      `</button></span>`
    );
  }

  /* Dibuja la tarjeta del día elegido: estado (personalizado o no), los tramos
     vigentes resultantes y los chips de los tramos cargados. */
  function renderExcepciones() {
    const lista = document.getElementById("excep-lista");
    if (!lista) return;
    const estado = document.getElementById("excep-estado");
    const efectiva = document.getElementById("excep-efectiva");
    const doctor = doctorDisponibilidad();
    const fecha = fechaExcepciones();

    if (!doctor) {
      if (estado) {
        estado.textContent = "Sin odontólogos";
        estado.className = "badge badge-slate";
      }
      if (efectiva) efectiva.textContent = "";
      lista.innerHTML = vacio(
        "Aún no hay odontólogos registrados",
        "Cargá un profesional para personalizar sus días."
      );
      return;
    }

    const excepciones = excepcionesDeFecha(doctor.id, fecha);
    const vigentes = rangosVigentesDeFecha(doctor.id, fecha);
    const personalizado = excepciones.length > 0;

    if (estado) {
      estado.className = `badge ${personalizado ? "badge-amber" : "badge-slate"}`;
      estado.textContent = personalizado
        ? `Día personalizado · ${excepciones.length} tramo${excepciones.length === 1 ? "" : "s"}`
        : "Sin personalizar";
    }
    if (efectiva) {
      const detalle = vigentes.length
        ? `${vigentes.map((r) => `${r.hora_inicio}–${r.hora_fin}`).join(", ")} ` +
          `(${personalizado ? "excepción del día" : "agenda semanal"})`
        : "no se ofrece atención";
      efectiva.textContent = `${fechaLarga(fecha)}: atiende ${detalle}.`;
    }
    lista.innerHTML = personalizado
      ? excepciones.map(chipExcepcion).join("")
      : `<span class="disp-sin-rangos">` +
        `Ese día todavía se rige por la agenda semanal del ` +
        `${nombreDia(diaSemanaDe(fecha)).toLowerCase()}.</span>`;
  }

  /* Trae de la API las excepciones de una fecha puntual. Sin `forzar` se apoya en
     el bloque ya cargado en memoria (el calendario pide muchos días seguidos y no
     tiene sentido golpear la API en cada render). Con `forzar` —lo que ocurre
     siempre después de un alta, un reemplazo o una baja— consulta al servidor y
     reemplaza en el índice los tramos cacheados de ese día: es la única fuente de
     verdad y lo que hace que la lista se actualice sin recargar la página. */
  async function cargarExcepcionesDeFecha(iso, forzar) {
    const doctor = doctorDisponibilidad();
    if (!doctor) return [];
    const fecha = parteFecha(iso);
    if (!forzar) {
      if (fecha >= EXCEPCIONES_DESDE && fecha <= EXCEPCIONES_HASTA) {
        return excepcionesDeFecha(doctor.id, fecha);
      }
      if (rangoCargado(fecha, fecha)) return excepcionesDeFecha(doctor.id, fecha);
    }
    let lista;
    try {
      lista = await api(
        `/doctores/${doctor.id}/disponibilidad-especifica?desde=${fecha}&hasta=${fecha}`
      );
    } catch (err) {
      // Si la petición falla se conserva lo que ya había en memoria: la tarjeta
      // sigue mostrando los tramos y no se borra nada que exista en el servidor.
      toast("error", "No se pudieron leer los tramos del día", err.message);
      return excepcionesDeFecha(doctor.id, fecha);
    }
    indexarExcepciones(doctor.id, lista, [fecha]);
    // La fecha queda recordada (aunque no tenga tramos) para no repetir el
    // pedido si el calendario vuelve a pintarla.
    if (!rangoCargado(fecha, fecha)) excepcionesCargadas.push({ desde: fecha, hasta: fecha });
    return excepcionesDeFecha(doctor.id, fecha);
  }

  /* Pide a la API las excepciones de TODOS los odontólogos para un rango de
     fechas (lo usa el calendario con el mes visible, que puede caer fuera de la
     ventana inicial). Devuelve true si llegaron datos: hay que volver a dibujar. */
  async function cargarExcepcionesDeRango(desde, hasta) {
    const inicio = parteFecha(desde);
    const fin = parteFecha(hasta);
    if (!state.doctores.length || !inicio || !fin || inicio > fin) return false;
    if (rangoCargado(inicio, fin)) return false;
    // El rango se marca ANTES de pedir: si la API falla, un render posterior no
    // vuelve a disparar la misma petición en cadena.
    excepcionesCargadas.push({ desde: inicio, hasta: fin });
    const ventana = `?desde=${inicio}&hasta=${fin}`;
    const listas = await Promise.all(
      state.doctores.map((d) =>
        api(`/doctores/${d.id}/disponibilidad-especifica${ventana}`).catch(() => null)
      )
    );
    const fechas = fechasDelRango(inicio, fin);
    let llegaron = 0;
    state.doctores.forEach(function (d, i) {
      if (listas[i] === null) return; // ese odontólogo se queda con lo cacheado
      llegaron += 1;
      indexarExcepciones(d.id, listas[i], fechas);
    });
    return llegaron > 0;
  }

  /* Después de cada cambio: recarga el día desde el servidor (única fuente de
     verdad), redibuja la tarjeta y avisa al calendario para que rehaga la grilla
     del mes y los bloques del día. La recarga es FORZADA a propósito: la mutación
     que recién ocurrió (alta, reemplazo del día o baja de un tramo) dejó el cache
     desactualizado, y sin esto la lista seguiría mostrando el tramo borrado —o no
     mostraría el nuevo— hasta recargar la página. */
  async function refrescarDiaPersonalizado(fecha) {
    await cargarExcepcionesDeFecha(fecha, true);
    renderExcepciones();
    document.dispatchEvent(new CustomEvent("odonto:disponibilidad"));
  }

  async function agregarExcepcion(ev) {
    ev.preventDefault();
    const doctor = doctorDisponibilidad();
    if (!doctor) return;
    const fecha = fechaExcepciones();
    const tipo = document.getElementById("excep-tipo").value;
    const desde = document.getElementById("excep-desde").value;
    const hasta = document.getElementById("excep-hasta").value;
    if (!desde || !hasta) {
      toast("info", "Faltan horarios", "Indicá la hora de inicio y de fin del tramo.");
      return;
    }
    const btn = ev.target.querySelector("button[type=submit]");
    try {
      bloquear(btn, true);
      await api(`/doctores/${doctor.id}/disponibilidad-especifica`, {
        method: "POST",
        body: JSON.stringify({
          fecha: fecha,
          hora_inicio: desde,
          hora_fin: hasta,
          tipo: tipo,
        }),
      });
      toast(
        "exito",
        tipo === "HABILITADO" ? "Tramo habilitado" : "Tramo bloqueado",
        `${fechaCorta(fecha)} de ${desde} a ${hasta} · ${doctor.nombre}`
      );
      await refrescarDiaPersonalizado(fecha);
    } catch (err) {
      toast("error", "No se pudo agregar el tramo", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* Saca un tramo del índice en memoria (baja optimista): la tarjeta reacciona al
     instante y después el servidor confirma; si dijera que ya no existe, la lista
     queda igual porque el tramo ya se fue de la vista. */
  function quitarExcepcionDelCache(doctorId, fecha, excepcionId) {
    const restantes = excepcionesDeFecha(doctorId, fecha).filter(
      (e) => String(e.id) !== String(excepcionId)
    );
    indexarExcepciones(doctorId, restantes, [parteFecha(fecha)]);
  }

  /* Baja de un tramo desde la cruz del chip: se envía el id REAL que devolvió la
     API (``e.id``, guardado en ``data-borrar-excepcion`` junto con el odontólogo y
     la fecha) y el tramo sale de la lista en el acto, sin recargar la página. El
     botón se deshabilita mientras dura la petición porque un doble clic repetiría
     el DELETE y el segundo respondería 404 (ese tramo ya no existe). Por eso un
     404 acá se informa como aviso —«ya estaba eliminado»— y no como error. */
  async function borrarExcepcion(doctorId, excepcionId, fecha, btn) {
    const idDoctor = Number(doctorId);
    const idExcepcion = Number(excepcionId);
    const dia = parteFecha(fecha);
    if (!idDoctor || !idExcepcion) {
      // Chip sin id (p. ej. HTML viejo servido en el navegador): se relee el día.
      toast("info", "Ese tramo ya no está", "Se actualizó la lista con lo que hay en el servidor.");
      await refrescarDiaPersonalizado(dia);
      return;
    }
    try {
      if (btn) btn.disabled = true;
      quitarExcepcionDelCache(idDoctor, dia, idExcepcion);
      renderExcepciones();
      document.dispatchEvent(new CustomEvent("odonto:disponibilidad"));
      await api(`/doctores/${idDoctor}/disponibilidad-especifica/${idExcepcion}`, {
        method: "DELETE",
      });
      toast("exito", "Tramo eliminado", `${fechaCorta(dia)} · ${nombreDoctor(idDoctor)}`);
    } catch (err) {
      if (err.status === 404) {
        toast(
          "info",
          "El tramo ya estaba eliminado",
          `${fechaCorta(dia)} · ${nombreDoctor(idDoctor)}`
        );
      } else {
        toast("error", "No se pudo eliminar el tramo", err.message);
      }
    } finally {
      if (btn) btn.disabled = false;
    }
    // Pase lo que pase se relee el día del servidor: si la baja falló, el tramo
    // reaparece en la lista en lugar de quedar oculto por el borrado optimista.
    await refrescarDiaPersonalizado(dia);
  }

  /* Reemplazo total del día (PUT). Lo usan «Copiar la agenda semanal»,
     «Bloquear todo el día» y «Volver a la agenda semanal». */
  async function reemplazarExcepciones(fecha, rangos, mensaje) {
    const doctor = doctorDisponibilidad();
    if (!doctor) return;
    try {
      await api(`/doctores/${doctor.id}/disponibilidad-especifica/${fecha}`, {
        method: "PUT",
        body: JSON.stringify({ rangos: rangos }),
      });
      toast("exito", "Día actualizado", `${mensaje} · ${fechaCorta(fecha)} · ${doctor.nombre}`);
      await refrescarDiaPersonalizado(fecha);
    } catch (err) {
      toast("error", "No se pudo guardar el día", err.message);
    }
  }

  /* Copia los rangos de la agenda semanal del día de la semana de esa fecha como
     tramos HABILITADO, para después ajustarlos (agregar o bloquear horas). */
  function copiarAgendaSemanal() {
    const doctor = doctorDisponibilidad();
    if (!doctor) return;
    const fecha = fechaExcepciones();
    const dia = diaSemanaDe(fecha);
    const rangos = rangosDe(doctor.id)
      .filter((r) => Number(r.dia_semana) === dia)
      .sort((a, b) => String(a.hora_inicio).localeCompare(String(b.hora_inicio)))
      .map((r) => ({
        hora_inicio: r.hora_inicio,
        hora_fin: r.hora_fin,
        tipo: "HABILITADO",
      }));
    if (!rangos.length) {
      toast(
        "info",
        `Sin agenda semanal el ${nombreDia(dia).toLowerCase()}`,
        `${doctor.nombre} no tiene rangos cargados ese día de la semana.`
      );
      return;
    }
    reemplazarExcepciones(
      fecha,
      rangos,
      `Agenda semanal copiada (${rangos.length} tramo${rangos.length === 1 ? "" : "s"})`
    );
  }

  /* Cierra el día completo (00:00 a 23:59) sin importar la agenda semanal. */
  function bloquearDiaCompleto() {
    const fecha = fechaExcepciones();
    const ok = window.confirm(
      `¿Bloquear todo el ${fechaLarga(fecha)}? El día queda cerrado de 00:00 a 23:59 ` +
        "y no se ofrecerá ningún horario, aunque la agenda semanal tenga rangos."
    );
    if (!ok) return;
    reemplazarExcepciones(
      fecha,
      [{ hora_inicio: "00:00", hora_fin: "23:59", tipo: "BLOQUEADO" }],
      "Día bloqueado completo"
    );
  }

  /* Borra los tramos de la fecha: el día vuelve a regirse por la semana. */
  function volverAAgendaSemanal() {
    const fecha = fechaExcepciones();
    const ok = window.confirm(
      `¿Borrar los tramos del ${fechaLarga(fecha)}? Ese día vuelve a regirse por la agenda semanal.`
    );
    if (!ok) return;
    reemplazarExcepciones(fecha, [], "Vuelve a la agenda semanal");
  }

  /* «Ver ese día»: completa las excepciones si la fecha está fuera del bloque
     cacheado, redibuja la tarjeta y sincroniza la vista previa de bloques. */
  async function verDiaPersonalizado() {
    const fecha = fechaExcepciones();
    await cargarExcepcionesDeFecha(fecha);
    renderExcepciones();
    const input = document.getElementById("disp-fecha");
    if (input) input.value = fecha;
    await probarBloquesDisponibilidad();
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

  /* ========================================================================= */
  /* Módulo 3 · Facturación y Caja (coseguros, obras sociales y liquidaciones)  */
  /* ========================================================================= */
  /* Flujo del mostrador: al cerrar una visita (desde la tarjeta del turno en la
     agenda o desde la pestaña "Caja del día") se eligen las prestaciones
     realizadas, el sistema muestra el cartel del coseguro y guarda el
     movimiento de caja. Lo que queda a cargo de la obra social se presenta por
     período y se cierra cuando entra el depósito. La producción del mes se
     liquida por profesional según su tipo de pago. */
  const RUTA_CAJA = "/movimientos-caja";
  const RUTA_PRESTACIONES = "/prestaciones";
  const RUTA_ARANCELES = "/aranceles";
  const RUTA_LIS_OS = "/liquidaciones/obras-sociales";
  const RUTA_LIS_PROF = "/liquidaciones/profesionales";

  function rutaPrestacion(id) {
    return id ? `${RUTA_PRESTACIONES}/${id}` : RUTA_PRESTACIONES;
  }
  function rutaArancel(id) {
    return id ? `${RUTA_ARANCELES}/${id}` : RUTA_ARANCELES;
  }

  /* Estados del cobro de una orden presentada a la obra social. */
  const ESTADO_PAGO = {
    PENDIENTE: { texto: "Sin presentar", clase: "badge-amber", icono: ICON.reloj },
    EN_PROCESO_LIQUIDACION: { texto: "Presentada", clase: "badge-brand", icono: ICON.arena },
    COBRADO: { texto: "Cobrada", clase: "badge-green", icono: ICON.check },
    DEBITADO_OS: { texto: "Debitada", clase: "badge-red", icono: ICON.x },
  };
  function badgeEstadoPago(estado) {
    return badge(ESTADO_PAGO[estado] || { texto: estado, clase: "badge-slate", icono: "" });
  }

  /* Cómo se liquida cada modalidad de pago (etiqueta del reporte mensual). */
  const TIPO_PAGO_TEXTO = {
    DUENO: "Dueño",
    COMISION: "Comisión",
    SUELDO: "Sueldo fijo",
  };

  /* --------------------------- Utilidades de la vista -------------------- */
  function valorDe(id) {
    const el = document.getElementById(id);
    return el ? el.value : "";
  }

  /* Texto sin acentos y en minúsculas: comparaciones de búsqueda tolerantes. */
  function normalizarTexto(valor) {
    return String(valor == null ? "" : valor)
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();
  }

  /* Nombre de archivo seguro a partir de un texto (sin acentos ni espacios). */
  function slug(texto) {
    return (
      normalizarTexto(texto)
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "") || "profesional"
    );
  }

  /* Lee un input ``month`` y devuelve ``[anio, mes]`` (por defecto, el actual). */
  function mesDeInput(id) {
    const valor = String(valorDe(id) || "") || mesISO();
    const [anio, mes] = valor.split("-").map(Number);
    return [anio || new Date().getFullYear(), mes || new Date().getMonth() + 1];
  }

  /* Descarga una tabla como CSV (separador ";", el que abre Excel es-AR). */
  function descargarCSV(nombreArchivo, filas) {
    const limpiar = (v) => `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;
    const csv = filas.map((f) => f.map(limpiar).join(";")).join("\r\n");
    const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = nombreArchivo;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  /* Descarga un archivo del servidor (los reportes en PDF). El backend ya manda
     el nombre en ``Content-Disposition``: acá se repite para el navegador. */
  async function descargarArchivo(path, nombreArchivo) {
    const res = await fetch(API + path);
    if (!res.ok) {
      const texto = await res.text();
      let data = null;
      if (texto) { try { data = JSON.parse(texto); } catch (_) { data = texto; } }
      throw new Error(mensajeError(data, res.status));
    }
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = nombreArchivo;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    return nombreArchivo;
  }

  /* Resumen de las prácticas de un movimiento: "1× 01.01 · 2× 02.02". */
  function practicasTexto(movimiento) {
    return (movimiento.prestaciones || [])
      .map((p) => `${p.cantidad}× ${p.codigo_nomenclador}`)
      .join(" · ");
  }

  /* -------------------------------- Pestañas ----------------------------- */
  function cambiarTabFacturacion(tab) {
    if (["caja", "obras", "medicos", "precios", "convenios"].indexOf(tab) === -1) {
      return;
    }
    state.factTab = tab;
    document.querySelectorAll("[data-fact-tab]").forEach((b) =>
      b.classList.toggle("active", b.dataset.factTab === tab)
    );
    document.querySelectorAll("[data-fact-panel]").forEach((p) =>
      p.classList.toggle("hide", p.dataset.factPanel !== tab)
    );
    // Carga perezosa: cada pestaña pide al servidor lo que muestra.
    if (tab === "caja") cargarCajaDelDia();
    if (tab === "obras") {
      cargarSelectoresFacturacion();
      cargarPresentacion();
    }
    if (tab === "medicos") {
      cargarSelectoresFacturacion();
      cargarLiquidacionMedicos();
    }
    if (tab === "precios") cargarPrestaciones();
    if (tab === "convenios") {
      cargarSelectoresFacturacion();
      cargarConvenio();
    }
  }

  /* Rellena los desplegables de la vista con el catálogo ya cargado. */
  function cargarSelectoresFacturacion() {
    const obras = obrasSocialesActivas();
    const opciones =
      `<option value="">Elegí una cobertura…</option>` +
      obras.map((o) => `<option value="${o.id}">${esc(o.nombre)}</option>`).join("");
    if (!state.factArrObraSocial && obras.length) {
      state.factArrObraSocial = String(obras[0].id);
    }
    ["fact-lis-os", "fact-arr-os", "conv-os"].forEach((id) => {
      const sel = document.getElementById(id);
      if (!sel) return;
      sel.innerHTML = opciones;
      sel.value = state.factArrObraSocial;
    });

    const selPrest = document.getElementById("fact-arr-prestacion");
    if (selPrest) {
      const previo = selPrest.value;
      selPrest.innerHTML =
        `<option value="">Elegí una práctica…</option>` +
        state.factPrestaciones
          .filter((p) => p.activa)
          .map(
            (p) =>
              `<option value="${p.id}">${esc(p.codigo_nomenclador)} · ${esc(p.descripcion)}</option>`
          )
          .join("");
      selPrest.value = previo;
    }

    const selDoc = document.getElementById("fact-med-doctor");
    if (selDoc) {
      const previo = selDoc.value || state.factMedDoctor;
      selDoc.innerHTML =
        `<option value="">Todos los odontólogos</option>` +
        state.doctores
          .map(
            (d) =>
              `<option value="${d.id}">${esc(d.nombre)}${d.activo ? "" : " (dado de baja)"}</option>`
          )
          .join("");
      selDoc.value = previo;
    }
  }

  /* -------------------------- Entrada a la vista ------------------------- */
  /* Se llama al elegir "Facturación y Caja" en el menú: prepara los valores por
     defecto (día y mes de hoy) y carga la pestaña activa. */
  async function cargarFacturacion() {
    const fecha = document.getElementById("fact-fecha");
    if (fecha && !fecha.value) fecha.value = state.factFecha || hoyISO();
    const mesLis = document.getElementById("fact-lis-mes");
    if (mesLis && !mesLis.value) mesLis.value = mesISO();
    const mesMed = document.getElementById("fact-med-mes");
    if (mesMed && !mesMed.value) mesMed.value = state.factMedMes || mesISO();

    cargarSelectoresFacturacion();
    try {
      await cargarCajaDelDia();
    } catch (err) {
      toast("error", "No se pudo cargar la caja", err.message);
    }
    cambiarTabFacturacion(state.factTab || "caja");
  }

  /* ------------------------------ Caja del día --------------------------- */
  async function cargarCajaDelDia() {
    const fecha = state.factFecha || hoyISO();
    state.factFecha = fecha;
    const [movimientos, resumen] = await Promise.all([
      api(`${RUTA_CAJA}?desde=${fecha}&hasta=${fecha}&limit=200`),
      api(`${RUTA_CAJA}/resumen?desde=${fecha}&hasta=${fecha}`),
    ]);
    state.factCaja = movimientos || [];
    state.factResumen = resumen;
    renderCaja();
  }

  function renderCaja() {
    const resumen = state.factResumen;
    const set = (id, texto) => {
      const el = document.getElementById(id);
      if (el) el.textContent = texto;
    };
    set("fact-kpi-efectivo", resumen ? fmtMoneda(resumen.monto_efectivo) : "—");
    set("fact-kpi-pendiente", resumen ? fmtMoneda(resumen.monto_pendiente_os) : "—");
    set("fact-kpi-cobrado", resumen ? fmtMoneda(resumen.monto_cobrado_os) : "—");
    set("fact-kpi-cantidad", resumen ? String(resumen.cantidad_movimientos) : "—");
    set("fact-caja-contador", String(state.factCaja.length));

    // Insignia del menú: órdenes todavía sin acreditar (pendientes o presentadas).
    const abiertos = state.factCaja.filter(
      (m) =>
        m.estado_pago === "PENDIENTE" || m.estado_pago === "EN_PROCESO_LIQUIDACION"
    ).length;
    const badgeNav = document.getElementById("nav-fact-pendientes");
    if (badgeNav) {
      badgeNav.textContent = String(abiertos);
      badgeNav.classList.toggle("hide", abiertos === 0);
    }

    const tbody = document.getElementById("fact-lista-caja");
    if (!tbody) return;
    if (!state.factCaja.length) {
      tbody.innerHTML =
        `<tr><td colspan="9">` +
        vacio(
          "Todavía no hay visitas cerradas ese día",
          "Cerrá una visita con el botón «Cobrar» de su tarjeta en la agenda, o elegí otro día."
        ) +
        `</td></tr>`;
      return;
    }

    tbody.innerHTML = state.factCaja
      .map((m) => {
        const abierto =
          m.estado_pago === "PENDIENTE" || m.estado_pago === "EN_PROCESO_LIQUIDACION";
        const acciones = abierto
          ? `<button type="button" class="btn btn-soft btn-sm" data-fact-cobrar="${m.id}" ` +
            `title="Marcar el depósito de la obra social">Cobrar</button>` +
            `<button type="button" class="btn btn-danger-soft btn-sm" data-fact-debitar="${m.id}" ` +
            `title="Registrar un débito de la obra social">Debitar</button>`
          : `<span class="text-[11px] text-slate-400" title="${esc(m.observaciones || "")}">` +
            `${m.estado_pago === "COBRADO" ? "Depositado" : "Sin cobrar"}</span>`;
        return (
          `<tr${m.estado_pago === "COBRADO" ? ' class="fact-tildada"' : ""}>` +
          `<td class="font-semibold text-slate-600">${esc(parteHora(m.turno_fecha_hora_inicio || m.fecha))}</td>` +
          `<td><div class="flex items-center gap-2">${avatar(m.paciente_nombre, m.paciente_id)}` +
          `<div class="min-w-0"><p class="font-semibold text-slate-800 truncate">${esc(m.paciente_nombre || "—")}</p>` +
          `<p class="text-[11px] text-slate-400 font-mono">${esc(m.paciente_dni || "sin DNI")}</p></div></div></td>` +
          `<td>${m.obra_social_nombre ? esc(m.obra_social_nombre) : '<span class="badge badge-slate">Particular</span>'}</td>` +
          `<td class="text-xs">${esc(m.doctor_nombre || "—")}</td>` +
          `<td><ul class="fact-practicas"><li>${esc(practicasTexto(m)) || "—"}</li></ul></td>` +
          `<td class="text-right font-semibold">${fmtMoneda(m.monto_efectivo)}</td>` +
          `<td class="text-right text-slate-500">${fmtMoneda(m.monto_pendiente_os)}</td>` +
          `<td>${badgeEstadoPago(m.estado_pago)}</td>` +
          `<td><div class="flex items-center justify-end gap-2">${acciones}</div></td></tr>`
        );
      })
      .join("");
  }

  /* ---------------------------- Modal de cobro --------------------------- */
  /* Se abre desde el botón "Cobrar" de una tarjeta de la agenda (llega el
     ``turnoId``) o desde el botón general de la vista, que primero ofrece las
     visitas del día que todavía no se cerraron. */
  async function abrirModalCobro(turnoId) {
    const elegir = document.getElementById("fact-cobro-elegir");
    try {
      if (turnoId) {
        elegir.classList.add("hide");
      } else {
        const fecha = state.factFecha || hoyISO();
        const [turnos, movimientos] = await Promise.all([
          api(`/turnos?fecha_desde=${fecha}&fecha_hasta=${fecha}&limit=200`),
          api(`${RUTA_CAJA}?desde=${fecha}&hasta=${fecha}&limit=200`),
        ]);
        const facturados = (movimientos || []).map((m) => String(m.turno_id));
        const pendientes = (turnos || []).filter(
          (t) =>
            t.estado_turno !== "CANCELADO" && facturados.indexOf(String(t.id)) === -1
        );
        if (!pendientes.length) {
          toast(
            "info",
            "No quedan visitas por cobrar",
            `Las visitas del ${fechaCorta(fecha)} ya están cerradas.`
          );
          return;
        }
        const sel = document.getElementById("fact-cobro-turno");
        sel.innerHTML = pendientes
          .map(
            (t) =>
              `<option value="${t.id}">${esc(parteHora(t.fecha_hora_inicio))} · ` +
              `${esc(nombrePaciente(t.paciente_id))} · ${esc(nombreDoctor(t.doctor_id))}</option>`
          )
          .join("");
        elegir.classList.remove("hide");
        turnoId = pendientes[0].id;
      }
      const listo = await cargarTurnoParaCobro(turnoId);
      if (listo) mostrarModal("modal-cobro");
    } catch (err) {
      toast("error", "No se pudo abrir el cobro", err.message);
    }
  }

  /* Trae las prácticas del turno con su arancel resuelto y arma el formulario. */
  async function cargarTurnoParaCobro(turnoId) {
    const datos = await api(`/turnos/${turnoId}/prestaciones-disponibles`);
    if (datos.facturado) {
      const estado = await api(`/turnos/${turnoId}/facturacion`);
      const movimiento = estado.movimiento || {};
      toast(
        "info",
        "Esa visita ya fue facturada",
        `Movimiento #${movimiento.id} · efectivo ${fmtMoneda(movimiento.monto_efectivo)}`
      );
      return false;
    }

    state.cobro = datos;
    state.cobroSeleccion = {};
    state.cobroBusqueda = "";
    const buscar = document.getElementById("fact-cobro-buscar");
    if (buscar) buscar.value = "";
    const set = (id, texto) => {
      const el = document.getElementById(id);
      if (el) el.textContent = texto;
    };
    set("fact-cobro-paciente", datos.paciente_nombre);
    set("fact-cobro-cobertura", datos.obra_social_nombre || "Particular");
    set("fact-cobro-afiliado", datos.numero_afiliado || "—");
    set("fact-cobro-profesional", datos.doctor_nombre);
    set("modal-cobro-titulo", `Cerrar la visita de ${datos.paciente_nombre}`);
    set(
      "modal-cobro-sub",
      `${fechaHora(datos.turno_fecha_hora_inicio)} · turno #${datos.turno_id}`
    );
    const obs = document.getElementById("fact-cobro-obs");
    if (obs) obs.value = "";
    const particular = document.getElementById("fact-cobro-particular");
    if (particular) particular.checked = false;
    ocultarAviso("fact-cobro-error");
    renderCobroPrestaciones();
    return true;
  }

  function cerrarModalCobro() {
    state.cobro = null;
    state.cobroSeleccion = {};
    state.cobroBusqueda = "";
    ocultarModal("modal-cobro");
  }

  function seleccionDe(prestacionId) {
    return state.cobroSeleccion[String(prestacionId)] || 0;
  }

  /* Tildar/destildar una práctica del formulario de cobro (arranca en 1). */
  function alternarSeleccionCobro(prestacionId, tildada) {
    const clave = String(prestacionId);
    if (tildada) {
      state.cobroSeleccion[clave] = state.cobroSeleccion[clave] || 1;
    } else {
      delete state.cobroSeleccion[clave];
    }
    renderCobroPrestaciones();
  }

  /* Cambiar la cantidad sin redibujar la tabla (no se pierde el foco). */
  function fijarCantidadCobro(prestacionId, cantidad) {
    const clave = String(prestacionId);
    if (!Object.prototype.hasOwnProperty.call(state.cobroSeleccion, clave)) return;
    state.cobroSeleccion[clave] = Math.max(1, Math.min(99, Number(cantidad) || 1));
    recalcularCobro();
  }

  /* Prácticas del turno que coinciden con el buscador rápido (código o nombre).
     El filtro es sólo visual: lo ya tildado sigue seleccionado aunque no se vea. */
  function filtrarPrestacionesCobro(lista) {
    const busqueda = normalizarTexto(state.cobroBusqueda).trim();
    if (!busqueda) return lista;
    return lista.filter(
      (p) =>
        normalizarTexto(`${p.codigo_nomenclador} ${p.descripcion}`).indexOf(busqueda) !== -1
    );
  }

  /* Tabla de prácticas del turno: cada fila muestra el arancel de la cobertura,
     se tilda y se le pone cantidad. */
  function renderCobroPrestaciones() {
    const cont = document.getElementById("fact-cobro-lista");
    if (!cont) return;
    const lista = filtrarPrestacionesCobro((state.cobro && state.cobro.prestaciones) || []);
    if (!lista.length) {
      cont.innerHTML =
        `<tr><td colspan="6">` +
        vacio(
          state.cobroBusqueda
            ? `Sin coincidencias para «${state.cobroBusqueda}»`
            : "No hay prestaciones cargadas",
          state.cobroBusqueda
            ? "Probá con el código del nomenclador o con parte del nombre."
            : "Cargá el catálogo en la pestaña «Prestaciones y aranceles»."
        ) +
        `</td></tr>`;
      recalcularCobro();
      return;
    }

    cont.innerHTML = lista
      .map((p) => {
        const cantidad = seleccionDe(p.prestacion_id);
        /* Arancel de la práctica: lo que cubre la cobertura más el coseguro. */
        const arancel =
          Number(p.monto_cubre_os || 0) + Number(p.monto_coseguro_paciente || 0);
        const aviso =
          p.origen === "SIN_ARANCEL"
            ? ` <span class="badge badge-amber">Sin arancel</span>`
            : "";
        return (
          `<tr${cantidad ? ' class="fact-tildada"' : ""}>` +
          `<td><input type="checkbox" class="fact-check" data-cobro-tilde="${p.prestacion_id}" ` +
          `${cantidad ? "checked" : ""} /></td>` +
          `<td><p class="font-semibold text-slate-800">${esc(p.descripcion)}</p>` +
          `<p class="text-[11px] text-slate-400 font-mono">${esc(p.codigo_nomenclador)} · ` +
          `base ${fmtMoneda(p.valor_base)}${aviso}</p></td>` +
          `<td class="text-right">${fmtMoneda(arancel)}</td>` +
          `<td class="text-right text-slate-500">${fmtMoneda(p.monto_cubre_os)}</td>` +
          `<td class="text-right font-semibold">${fmtMoneda(p.monto_coseguro_paciente)}</td>` +
          `<td><input type="number" min="1" max="99" value="${cantidad || 1}" ` +
          `class="field px-2 py-1 text-sm" data-cobro-cantidad="${p.prestacion_id}" ` +
          `${cantidad ? "" : "disabled"} /></td></tr>`
        );
      })
      .join("");
    const contador = document.getElementById("fact-cobro-contador");
    if (contador) contador.textContent = String(lista.length);
    recalcularCobro();
  }

  /* Cartel del mostrador: suma los coseguros tildados y lo que va a la OS.
     El importe definitivo lo confirma el backend al registrar el cobro. */
  function recalcularCobro() {
    const lista = (state.cobro && state.cobro.prestaciones) || [];
    let efectivo = 0;
    let os = 0;
    let cantidad = 0;
    const partes = [];
    lista.forEach((p) => {
      const n = seleccionDe(p.prestacion_id);
      if (!n) return;
      efectivo += Number(p.monto_coseguro_paciente || 0) * n;
      os += Number(p.monto_cubre_os || 0) * n;
      cantidad += n;
      partes.push(`${n}× ${p.codigo_nomenclador}`);
    });

    const cartel = document.getElementById("fact-cobro-cartel");
    if (cartel) cartel.classList.toggle("fact-cartel-vacio", cantidad === 0);
    const set = (id, texto) => {
      const el = document.getElementById(id);
      if (el) el.textContent = texto;
    };
    set("fact-cobro-efectivo", fmtMoneda(efectivo));
    set("fact-cobro-total", fmtMoneda(efectivo + os));
    set("fact-cobro-os", fmtMoneda(os));
    // El saldo de la cobertura queda PENDIENTE en caja hasta la liquidación del
    // período: el cartel lo anticipa antes de cerrar la atención.
    const pendiente = document.getElementById("fact-cobro-pendiente");
    if (pendiente) {
      const cobertura =
        (state.cobro && state.cobro.obra_social_nombre) || "la obra social";
      if (os > 0) {
        pendiente.textContent =
          `Queda PENDIENTE para la liquidación de ${cobertura}: ${fmtMoneda(os)}`;
        pendiente.className = "badge badge-amber";
      } else {
        pendiente.textContent =
          "Visita saldada en el mostrador: sin saldo para la obra social.";
        pendiente.className = "badge badge-green";
      }
    }
    set(
      "fact-cobro-detalle",
      cantidad
        ? `${cantidad} práctica${cantidad === 1 ? "" : "s"}: ${partes.join(" · ")}`
        : "Sin prestaciones seleccionadas."
    );
  }

  /* POST /turnos/{id}/facturar: cierra la visita y registra el ingreso en caja. */
  async function registrarCobro(ev) {
    ev.preventDefault();
    const btn = document.getElementById("modal-cobro-save");
    if (!state.cobro) return;
    const prestaciones = Object.keys(state.cobroSeleccion)
      .filter((id) => seleccionDe(id) > 0)
      .map((id) => ({ prestacion_id: Number(id), cantidad: seleccionDe(id) }));
    if (!prestaciones.length) {
      mostrarAviso("fact-cobro-error", "Marcá al menos una prestación realizada.");
      return;
    }

    const payload = {
      prestaciones,
      observaciones: valorDe("fact-cobro-obs").trim() || null,
      permitir_particular: Boolean(
        document.getElementById("fact-cobro-particular").checked
      ),
    };
    try {
      bloquear(btn, true);
      const movimiento = await api(`/turnos/${state.cobro.turno_id}/facturar`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const cierre =
        `Turno #${state.cobro.turno_id} finalizado · movimiento de caja #${movimiento.id}`;
      cerrarModalCobro();
      toast(
        "exito",
        `Cobrar Coseguro en mostrador: ${fmtMoneda(movimiento.monto_efectivo)}`,
        movimiento.monto_pendiente_os > 0
          ? `${cierre} · se presenta a ` +
            `${movimiento.obra_social_nombre || "la obra social"}: ` +
            `${fmtMoneda(movimiento.monto_pendiente_os)}`
          : `${cierre} · visita saldada en el mostrador (sin saldo para la obra social).`
      );
      await cargarCajaDelDia();
      await cargarTodo();
    } catch (err) {
      mostrarAviso("fact-cobro-error", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* ---------------------- Liquidación de obras sociales ------------------ */
  /* GET /liquidaciones/obras-sociales: el período completo de la cobertura con
     el estado de cada orden (sin presentar, presentada, cobrada o debitada). */
  async function cargarPresentacion() {
    const osId = state.factArrObraSocial;
    if (!osId) {
      state.factPresentacion = null;
      renderPresentacion();
      return;
    }
    const [anio, mes] = mesDeInput("fact-lis-mes");
    try {
      state.factPresentacion = await api(
        `${RUTA_LIS_OS}?obra_social_id=${osId}&anio=${anio}&mes=${mes}`
      );
      state.factTildadas = [];
      const todos = document.getElementById("fact-lis-todos");
      if (todos) todos.checked = false;
      renderPresentacion();
    } catch (err) {
      toast("error", "No se pudo cargar la presentación", err.message);
    }
  }

  function renderPresentacion() {
    const set = (id, texto) => {
      const el = document.getElementById(id);
      if (el) el.textContent = texto;
    };
    const p = state.factPresentacion;
    const tbody = document.getElementById("fact-lista-presentacion");
    if (!p) {
      ["fact-lis-presentado", "fact-lis-cobrado", "fact-lis-debitado", "fact-lis-pendiente"].forEach((id) => set(id, "—"));
      set("fact-lis-contador", "0");
      set("fact-lis-numero", "Elegí la cobertura y el mes: la presentación agrupa las visitas pendientes de ese período.");
      if (tbody) tbody.innerHTML = `<tr><td colspan="9">${vacio("Sin cobertura seleccionada")}</td></tr>`;
      return;
    }

    set("fact-lis-presentado", fmtMoneda(p.total_presentado));
    set("fact-lis-cobrado", fmtMoneda(p.total_cobrado_os));
    set("fact-lis-debitado", fmtMoneda(p.total_debitado_os));
    set("fact-lis-pendiente", fmtMoneda(p.total_pendiente_os));
    set("fact-lis-contador", String(p.cantidad_movimientos));
    const sinPresentar = p.movimientos.filter((m) => m.estado_pago === "PENDIENTE").length;
    set(
      "fact-lis-numero",
      `Presentación ${p.numero_presentacion} · ${p.obra_social_nombre} · ` +
        `${String(p.mes).padStart(2, "0")}/${p.anio} · ` +
        (p.generada ? "emitida" : "sin emitir") +
        (sinPresentar ? ` · ${sinPresentar} orden(es) sin presentar` : "")
    );

    if (!tbody) return;
    if (!p.movimientos.length) {
      tbody.innerHTML = `<tr><td colspan="9">${vacio("No hay visitas de esa cobertura en el período")}</td></tr>`;
      return;
    }

    tbody.innerHTML = p.movimientos
      .map((m) => {
        const abierto =
          m.estado_pago === "PENDIENTE" || m.estado_pago === "EN_PROCESO_LIQUIDACION";
        const tildada = state.factTildadas.indexOf(m.id) !== -1;
        const acciones = abierto
          ? `<button type="button" class="btn btn-soft btn-sm" data-fact-cobrar="${m.id}">Cobrar</button>` +
            `<button type="button" class="btn btn-danger-soft btn-sm" data-fact-debitar="${m.id}">Debitar</button>`
          : `<span class="text-[11px] text-slate-400">${esc(m.observaciones || "Cerrada")}</span>`;
        return (
          `<tr${tildada ? ' class="fact-tildada"' : ""}>` +
          `<td><input type="checkbox" class="fact-check" data-fact-tilde="${m.id}" ${tildada ? "checked" : ""} ${abierto ? "" : "disabled"} /></td>` +
          `<td class="text-xs">${esc(fechaHora(m.turno_fecha_hora_inicio || m.fecha))}</td>` +
          `<td class="font-semibold text-slate-800">${esc(m.paciente_nombre || "—")}</td>` +
          `<td class="text-xs font-mono text-slate-500">${esc(m.paciente_dni || "—")}</td>` +
          `<td><ul class="fact-practicas"><li>${esc(practicasTexto(m)) || "—"}</li></ul></td>` +
          `<td class="text-right font-semibold">${fmtMoneda(m.monto_pendiente_os)}</td>` +
          `<td class="text-right text-slate-500">${fmtMoneda(m.monto_efectivo)}</td>` +
          `<td>${badgeEstadoPago(m.estado_pago)}</td>` +
          `<td><div class="flex items-center justify-end gap-2">${acciones}</div></td></tr>`
        );
      })
      .join("");
  }

  /* POST /liquidaciones/obras-sociales: emite la presentación del período. */
  async function generarPresentacion() {
    const osId = state.factArrObraSocial;
    if (!osId) {
      toast("info", "Elegí una obra social", "La presentación se arma por cobertura.");
      return;
    }
    const [anio, mes] = mesDeInput("fact-lis-mes");
    const p = state.factPresentacion;
    const sinPresentar = p
      ? p.movimientos.filter((m) => m.estado_pago === "PENDIENTE").length
      : 0;
    const ok = window.confirm(
      `¿Generar la presentación de ${String(mes).padStart(2, "0")}/${anio} con ` +
        `${sinPresentar} orden(es) sin presentar? Las órdenes pasan a "en liquidación".`
    );
    if (!ok) return;

    const btn = document.getElementById("fact-lis-generar");
    try {
      bloquear(btn, true);
      const presentacion = await api(RUTA_LIS_OS, {
        method: "POST",
        body: JSON.stringify({ obra_social_id: Number(osId), anio, mes }),
      });
      state.factPresentacion = presentacion;
      state.factTildadas = [];
      renderPresentacion();
      toast(
        "exito",
        `Presentación ${presentacion.numero_presentacion} generada`,
        `${presentacion.cantidad_movimientos} orden(es) · presentado ` +
          `${fmtMoneda(presentacion.total_presentado)}`
      );
    } catch (err) {
      toast("error", "No se pudo generar la presentación", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* Tilde de una orden para cerrarla en lote (cobrada o debitada). */
  function alternarTilde(movimientoId) {
    const id = Number(movimientoId);
    const i = state.factTildadas.indexOf(id);
    if (i === -1) state.factTildadas.push(id);
    else state.factTildadas.splice(i, 1);
    renderPresentacion();
  }

  /* Cierra en lote lo tildado. Cada orden se marca individualmente: los
     endpoints de la API son de a uno (cobrar o debitar con su motivo). */
  async function aplicarTildes(accion) {
    const ids = state.factTildadas.slice();
    if (!ids.length) {
      toast("info", "No hay órdenes tildadas", "Tildá las que entraron en el depósito.");
      return;
    }
    let motivo = "";
    if (accion === "debitar") {
      motivo = window.prompt(
        `Motivo del débito para ${ids.length} orden(es):`,
        "Práctica rechazada por la obra social"
      );
      if (!motivo || motivo.trim().length < 3) return;
    }

    const btn = document.getElementById(
      accion === "cobrar" ? "fact-lis-cobrar-tildadas" : "fact-lis-debitar-tildadas"
    );
    let hechas = 0;
    const errores = [];
    try {
      bloquear(btn, true);
      for (const id of ids) {
        try {
          await api(`${RUTA_CAJA}/${id}/${accion}`, {
            method: "POST",
            body: JSON.stringify(
              accion === "cobrar" ? {} : { observaciones: motivo.trim() }
            ),
          });
          hechas += 1;
        } catch (err) {
          errores.push(`#${id}: ${err.message}`);
        }
      }
      state.factTildadas = [];
      const todos = document.getElementById("fact-lis-todos");
      if (todos) todos.checked = false;
      await cargarPresentacion();
      await cargarCajaDelDia();
      toast(
        errores.length ? "info" : "exito",
        `${hechas} orden(es) marcadas como ${accion === "cobrar" ? "cobradas" : "debitadas"}`,
        errores.length ? errores.slice(0, 3).join(" · ") : "Presentación actualizada."
      );
    } catch (err) {
      toast("error", "No se pudieron cerrar las órdenes", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* Cierre individual: POST /movimientos-caja/{id}/cobrar. */
  async function cobrarMovimiento(movimientoId) {
    try {
      const movimiento = await api(`${RUTA_CAJA}/${movimientoId}/cobrar`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      toast(
        "exito",
        `Orden #${movimiento.id} cobrada`,
        `Depositado ${fmtMoneda(movimiento.monto_cobrado_os || 0)} de ` +
          `${fmtMoneda(movimiento.monto_pendiente_os)} presentados.`
      );
      await Promise.all([cargarCajaDelDia(), cargarPresentacion()]);
    } catch (err) {
      toast("error", "No se pudo marcar el cobro", err.message);
    }
  }

  /* Débito individual: pide el motivo en un modal (queda registrado). */
  function abrirDebito(movimientoId) {
    const id = Number(movimientoId);
    const candidatos = state.factCaja.concat(
      state.factPresentacion ? state.factPresentacion.movimientos : []
    );
    const movimiento = candidatos.find((m) => m.id === id);
    state.debitoMovimientoId = id;
    const detalle = document.getElementById("fact-deb-detalle");
    if (detalle) {
      detalle.textContent = movimiento
        ? `Orden #${movimiento.id} · ${movimiento.paciente_nombre || "—"} · ` +
          `presentado ${fmtMoneda(movimiento.monto_pendiente_os)} (${movimiento.estado_pago})`
        : `Orden #${id}`;
    }
    const motivo = document.getElementById("fact-deb-motivo");
    if (motivo) motivo.value = "";
    const monto = document.getElementById("fact-deb-monto");
    if (monto) monto.value = "";
    ocultarAviso("fact-deb-error");
    mostrarModal("modal-debito");
  }

  function cerrarDebito() {
    state.debitoMovimientoId = null;
    ocultarModal("modal-debito");
  }

  /* POST /movimientos-caja/{id}/debitar con el motivo obligatorio. */
  async function confirmarDebito(ev) {
    ev.preventDefault();
    if (!state.debitoMovimientoId) return;
    const btn = document.getElementById("modal-deb-save");
    const monto = valorDe("fact-deb-monto");
    const payload = { observaciones: valorDe("fact-deb-motivo").trim() };
    if (monto !== "") payload.monto_cobrado_os = Number(monto);
    try {
      bloquear(btn, true);
      const movimiento = await api(
        `${RUTA_CAJA}/${state.debitoMovimientoId}/debitar`,
        { method: "POST", body: JSON.stringify(payload) }
      );
      cerrarDebito();
      toast(
        "exito",
        `Orden #${movimiento.id} debitada`,
        `Sin cobrar ${fmtMoneda(
          movimiento.monto_pendiente_os - (movimiento.monto_cobrado_os || 0)
        )}`
      );
      await Promise.all([cargarCajaDelDia(), cargarPresentacion()]);
    } catch (err) {
      mostrarAviso("fact-deb-error", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* Exporta la presentación del período para presentarla ante la cobertura. */
  function exportarPresentacion() {
    const p = state.factPresentacion;
    if (!p || !p.movimientos.length) {
      toast("info", "No hay nada para exportar", "Consultá primero el período de la cobertura.");
      return;
    }
    const filas = [
      [`Presentación ${p.numero_presentacion}`],
      ["Cobertura", p.obra_social_nombre, "Código", p.obra_social_codigo || "-"],
      ["Período", `${String(p.mes).padStart(2, "0")}/${p.anio}`],
      ["Emitida", p.generada ? "Sí" : "No (borrador)"],
      [],
      ["Fecha", "Paciente", "DNI", "Prácticas", "Presentado", "Efectivo", "Estado", "Observaciones"],
    ];
    p.movimientos.forEach((m) => {
      filas.push([
        parteFecha(m.turno_fecha_hora_inicio || m.fecha),
        m.paciente_nombre || "",
        m.paciente_dni || "",
        practicasTexto(m),
        fmtMoneda(m.monto_pendiente_os),
        fmtMoneda(m.monto_efectivo),
        m.estado_pago,
        m.observaciones || "",
      ]);
    });
    filas.push([]);
    filas.push(["Totales", "", "", "", fmtMoneda(p.total_presentado), fmtMoneda(p.total_efectivo)]);
    descargarCSV(`presentacion-${p.numero_presentacion}.csv`, filas);
    toast("exito", "Resumen exportado", `presentacion-${p.numero_presentacion}.csv`);
  }

  /* El mismo resumen, pero en PDF: es el que se le entrega a la cobertura. */
  async function exportarPresentacionPDF() {
    const p = state.factPresentacion;
    if (!p || !p.movimientos.length) {
      toast("info", "No hay nada para exportar", "Consultá primero el período de la cobertura.");
      return;
    }
    const nombre = `presentacion-${p.numero_presentacion}.pdf`;
    try {
      await descargarArchivo(
        `${RUTA_LIS_OS}/pdf?obra_social_id=${p.obra_social_id}&anio=${p.anio}&mes=${p.mes}`,
        nombre
      );
      toast("exito", "PDF generado", nombre);
    } catch (err) {
      toast("error", "No se pudo generar el PDF", err.message);
    }
  }

  /* --------------------- Liquidación de profesionales -------------------- */
  /* GET /liquidaciones/profesionales: producción y honorarios del mes. */
  async function cargarLiquidacionMedicos() {
    const [anio, mes] = mesDeInput("fact-med-mes");
    state.factMedDoctor = valorDe("fact-med-doctor");
    const consulta =
      `anio=${anio}&mes=${mes}` +
      (state.factMedDoctor ? `&doctor_id=${state.factMedDoctor}` : "");
    try {
      state.factMedicos = await api(`${RUTA_LIS_PROF}?${consulta}`);
      renderLiquidacionMedicos();
    } catch (err) {
      toast("error", "No se pudo cargar la liquidación", err.message);
    }
  }

  function renderLiquidacionMedicos() {
    const set = (id, texto) => {
      const el = document.getElementById(id);
      if (el) el.textContent = texto;
    };
    const r = state.factMedicos;
    const cont = document.getElementById("fact-lista-medicos");
    if (!r) {
      ["fact-med-produccion", "fact-med-honorarios", "fact-med-efectivo", "fact-med-os"].forEach((id) => set(id, "—"));
      if (cont) cont.innerHTML = vacio("Sin reporte cargado", "Elegí el período y tocá «Ver reporte».");
      return;
    }

    set("fact-med-produccion", fmtMoneda(r.total_produccion));
    set("fact-med-honorarios", fmtMoneda(r.total_honorarios));
    set("fact-med-efectivo", fmtMoneda(r.total_efectivo));
    set("fact-med-os", fmtMoneda(r.total_os));

    if (!cont) return;
    if (!r.doctores.length) {
      cont.innerHTML = vacio("No hay profesionales activos", "Cargalos en la pestaña «Doctores».");
      return;
    }

    const filas = r.doctores
      .map((d) => {
        const datos =
          `${esc(d.doctor_especialidad)} · ${d.cantidad_turnos} atención(es)` +
          (d.activo ? "" : " · dado de baja");
        return (
          `<tr>` +
          `<td><div class="flex items-center gap-3">${avatar(d.doctor_nombre, d.doctor_id)}` +
          `<div><p class="fact-med-nombre">${esc(d.doctor_nombre)}</p>` +
          `<p class="fact-med-dato">${datos}</p></div></div></td>` +
          `<td><span class="fact-med-tipo">${esc(textoPagoDoctor(d))}</span></td>` +
          `<td class="text-right">${fmtMoneda(d.produccion_efectivo)}</td>` +
          `<td class="text-right">${fmtMoneda(d.produccion_os)}</td>` +
          `<td class="text-right font-semibold">${fmtMoneda(d.produccion_total)}</td>` +
          `<td class="text-right font-bold text-brand-700">${fmtMoneda(d.honorarios)}</td>` +
          `<td class="text-xs text-slate-500">${esc(d.detalle_honorarios)}</td>` +
          `</tr>`
        );
      })
      .join("");

    /* Tabla de subtotales: una fila por profesional y el total del consultorio
       al pie (la producción del mes y lo que se le liquida a cada uno). */
    const atenciones = r.doctores.reduce((suma, d) => suma + d.cantidad_turnos, 0);
    cont.innerHTML =
      `<div class="card overflow-hidden">` +
      `<div class="overflow-x-auto"><table class="data-table">` +
      `<thead><tr>` +
      `<th>Profesional</th><th>Forma de pago</th>` +
      `<th class="text-right">Efectivo</th>` +
      `<th class="text-right">Obras sociales</th>` +
      `<th class="text-right">Producción</th>` +
      `<th class="text-right">Honorarios</th>` +
      `<th>Cálculo del mes</th></tr></thead>` +
      `<tbody>${filas}</tbody>` +
      `<tfoot><tr class="bg-slate-50">` +
      `<td colspan="2" class="font-bold text-slate-700">Totales del período</td>` +
      `<td class="text-right font-bold">${fmtMoneda(r.total_efectivo)}</td>` +
      `<td class="text-right font-bold">${fmtMoneda(r.total_os)}</td>` +
      `<td class="text-right font-bold">${fmtMoneda(r.total_produccion)}</td>` +
      `<td class="text-right font-bold text-brand-700">${fmtMoneda(r.total_honorarios)}</td>` +
      `<td class="text-xs text-slate-500">${atenciones} atención(es) facturada(s)</td>` +
      `</tr></tfoot>` +
      `</table></div></div>`;
  }

  /* Exporta el reporte del mes (lo que se le liquida a cada profesional). */
  function exportarLiquidacionMedicos() {
    const r = state.factMedicos;
    if (!r || !r.doctores.length) {
      toast("info", "No hay reporte para exportar", "Consultá primero el período.");
      return;
    }
    const filas = [
      ["Liquidación de profesionales", `${String(r.mes).padStart(2, "0")}/${r.anio}`],
      [],
      [
        "Profesional", "Especialidad", "Tipo de pago", "Valor", "Atenciones",
        "Producción efectivo", "Presentado a la OS", "Producción total",
        "Honorarios", "Cálculo",
      ],
    ];
    r.doctores.forEach((d) => {
      filas.push([
        d.doctor_nombre,
        d.doctor_especialidad,
        d.tipo_pago,
        d.valor_pago == null ? "" : d.valor_pago,
        d.cantidad_turnos,
        fmtMoneda(d.produccion_efectivo),
        fmtMoneda(d.produccion_os),
        fmtMoneda(d.produccion_total),
        fmtMoneda(d.honorarios),
        d.detalle_honorarios,
      ]);
    });
    filas.push([]);
    filas.push([
      "Totales", "", "", "", "", fmtMoneda(r.total_efectivo), fmtMoneda(r.total_os),
      fmtMoneda(r.total_produccion), fmtMoneda(r.total_honorarios),
    ]);
    const nombre = `liquidacion-profesionales-${r.anio}-${String(r.mes).padStart(2, "0")}.csv`;
    descargarCSV(nombre, filas);
    toast("exito", "Reporte exportado", nombre);
  }

  /* El mismo reporte, pero en PDF: es el que se le entrega al profesional. */
  async function exportarLiquidacionMedicosPDF() {
    const r = state.factMedicos;
    if (!r || !r.doctores.length) {
      toast("info", "No hay reporte para exportar", "Consultá primero el período.");
      return;
    }
    /* El PDF sigue el filtro del reporte: si se liquidó a un solo profesional,
       se pide (y se nombra) sólo el suyo. */
    const profesional = porId(state.doctores, Number(state.factMedDoctor));
    const nombre = profesional
      ? `liquidacion-${slug(profesional.nombre)}-${r.anio}-${String(r.mes).padStart(2, "0")}.pdf`
      : `liquidacion-profesionales-${r.anio}-${String(r.mes).padStart(2, "0")}.pdf`;
    try {
      const consulta =
        `anio=${r.anio}&mes=${r.mes}` +
        (state.factMedDoctor ? `&doctor_id=${state.factMedDoctor}` : "");
      await descargarArchivo(`${RUTA_LIS_PROF}/pdf?${consulta}`, nombre);
      toast("exito", "PDF generado", nombre);
    } catch (err) {
      toast("error", "No se pudo generar el PDF", err.message);
    }
  }

  /* ------------------- Catálogo de prestaciones y aranceles -------------- */
  /* GET /prestaciones: el catálogo completo (la vista filtra las dadas de baja
     con "Mostrar las dadas de baja") y, después, los aranceles de la cobertura
     elegida. */
  async function cargarPrestaciones() {
    try {
      state.factPrestaciones = (await api(RUTA_PRESTACIONES)) || [];
      cargarSelectoresFacturacion();
      renderPrestaciones();
      await cargarAranceles();
    } catch (err) {
      toast("error", "No se pudo cargar el catálogo de prestaciones", err.message);
    }
  }

  function renderPrestaciones() {
    const tbody = document.getElementById("fact-lista-prestaciones");
    const contador = document.getElementById("fact-prest-contador");
    const activas = state.factPrestaciones.filter((p) => p.activa);
    if (contador) {
      contador.textContent = `${activas.length} activa${activas.length === 1 ? "" : "s"}`;
    }
    if (!tbody) return;

    const visibles = state.factMostrarInactivas
      ? state.factPrestaciones
      : activas;
    if (!visibles.length) {
      tbody.innerHTML =
        `<tr><td colspan="6">` +
        vacio(
          "Aún no hay prestaciones cargadas",
          "Usá «Nueva prestación» para cargar la primera práctica facturable."
        ) +
        `</td></tr>`;
      return;
    }

    const conArancel = {};
    state.factAranceles.forEach((a) => {
      conArancel[String(a.prestacion_id)] = a;
    });

    tbody.innerHTML = visibles
      .map((p) => {
        const arancel = conArancel[String(p.id)];
        const estado = p.activa
          ? `<span class="badge badge-green">${ICON.check}Activa</span>`
          : `<span class="badge badge-slate">Dada de baja</span>`;
        const alterno = p.activa
          ? `<button type="button" class="btn btn-danger-soft btn-sm" data-fact-baja-prest="${p.id}" ` +
            `title="Dejar de ofrecer ${esc(p.descripcion)} sin borrarla">Dar de baja</button>`
          : `<button type="button" class="btn btn-soft btn-sm" data-fact-reactivar-prest="${p.id}" ` +
            `title="Volver a ofrecer ${esc(p.descripcion)} al facturar">Reactivar</button>`;
        return (
          `<tr${p.activa ? "" : ' class="opacity-70"'}>` +
          `<td class="font-mono text-xs">${esc(p.codigo_nomenclador)}</td>` +
          `<td class="font-semibold text-slate-800">${esc(p.descripcion)}</td>` +
          `<td class="text-right">${fmtMoneda(p.valor_base)}</td>` +
          `<td>${estado}</td>` +
          `<td>${arancel
            ? `<span class="badge badge-brand">${fmtMoneda(arancel.monto_total)}</span>`
            : `<span class="text-[11px] text-slate-400">sin cargar</span>`}</td>` +
          `<td><div class="flex items-center justify-end gap-2">` +
          `<button type="button" class="btn btn-ghost btn-sm" data-fact-editar-prest="${p.id}">Editar</button>` +
          `${alterno}</div></td></tr>`
        );
      })
      .join("");
  }

  /* ------------------ Importador del PDF de aranceles ------------------- */
  /* El botón abre el selector de archivos y, al elegirlo, se manda una
     **vista previa** (``simular``): el panel muestra cuántas prácticas se crean,
     cuántas cambian y qué filas fallaron. Recién cuando la secretaría confirma
     se escribe en el catálogo. */
  const MAX_BYTES_PDF_PRESTACIONES = 10 * 1024 * 1024;

  function abrirImportadorPrestacionesPdf() {
    const input = document.getElementById("fact-prest-pdf");
    if (!input) return;
    input.value = "";
    input.click();
  }

  function cerrarImportacionPrestacionesPdf() {
    state.factPrestPdfArchivo = null;
    state.factPrestPdfReporte = null;
    const input = document.getElementById("fact-prest-pdf");
    if (input) input.value = "";
    const panel = document.getElementById("fact-prest-pdf-panel");
    if (panel) panel.classList.add("hide");
    const reporte = document.getElementById("fact-prest-pdf-reporte");
    if (reporte) reporte.innerHTML = "";
    const confirmar = document.getElementById("fact-prest-pdf-confirmar");
    if (confirmar) confirmar.classList.add("hide");
  }

  async function elegirPdfPrestaciones(archivo) {
    if (!archivo) return;
    const esPdf =
      archivo.type === "application/pdf" || /\.pdf$/i.test(archivo.name || "");
    if (!esPdf) {
      toast("info", "El archivo no es un PDF", "Elegí el listado de aranceles en formato .pdf.");
      return;
    }
    if (archivo.size > MAX_BYTES_PDF_PRESTACIONES) {
      toast(
        "info",
        "El PDF es demasiado grande",
        "El límite es de 10 MB: subí sólo el listado de aranceles."
      );
      return;
    }

    /* Al elegir otro archivo se descarta la vista previa anterior: el botón de
       confirmar no debe quedar visible mientras se lee el PDF nuevo. */
    state.factPrestPdfArchivo = null;
    state.factPrestPdfReporte = null;
    const confirmar = document.getElementById("fact-prest-pdf-confirmar");
    if (confirmar) confirmar.classList.add("hide");
    const panel = document.getElementById("fact-prest-pdf-panel");
    if (panel) panel.classList.remove("hide");
    const nombre = document.getElementById("fact-prest-pdf-nombre");
    if (nombre) nombre.textContent = `Leyendo ${archivo.name}…`;
    const reporte = document.getElementById("fact-prest-pdf-reporte");
    if (reporte) reporte.innerHTML = "";

    try {
      const contenido = await base64DeArchivo(archivo);
      state.factPrestPdfArchivo = { nombre: archivo.name, contenido: contenido };
      state.factPrestPdfReporte = null;
      if (nombre) {
        const kb = Math.max(1, Math.round(archivo.size / 1024));
        nombre.textContent = `PDF elegido: ${archivo.name} (${kb} KB)`;
      }
      await importarPrestacionesPdf(true);
    } catch (err) {
      state.factPrestPdfArchivo = null;
      if (nombre) nombre.textContent = "";
      toast("error", "No se pudo leer el PDF", err.message);
      cerrarImportacionPrestacionesPdf();
    }
  }

  async function importarPrestacionesPdf(simular) {
    const archivo = state.factPrestPdfArchivo;
    if (!archivo) {
      toast("info", "No hay un PDF elegido", "Usá «Importar aranceles (PDF)» para elegir el archivo.");
      return;
    }
    const btn = document.getElementById(
      simular ? "fact-importar-prestaciones-pdf" : "fact-prest-pdf-confirmar"
    );
    try {
      if (!simular) bloquear(btn, true);
      const reporte = await api(`${RUTA_PRESTACIONES}/importar-pdf`, {
        method: "POST",
        body: JSON.stringify({
          nombre_archivo: archivo.nombre,
          contenido_base64: archivo.contenido,
          simular: simular,
        }),
      });
      state.factPrestPdfReporte = reporte;
      renderReportePdfPrestaciones(reporte);
      if (!simular) {
        toast(
          "exito",
          "Aranceles importados",
          `${reporte.creados} nueva${reporte.creados === 1 ? "" : "s"} y ` +
            `${reporte.actualizados} actualizada${reporte.actualizados === 1 ? "" : "s"}` +
            (reporte.errores ? ` · ${reporte.errores} con error.` : ".")
        );
        state.factPrestPdfArchivo = null;
        const input = document.getElementById("fact-prest-pdf");
        if (input) input.value = "";
        await cargarPrestaciones();
      }
    } catch (err) {
      toast(
        "error",
        simular ? "No se pudo leer el PDF" : "No se pudieron importar los aranceles",
        err.message
      );
      const reporte = document.getElementById("fact-prest-pdf-reporte");
      if (reporte) {
        reporte.innerHTML =
          `<p class="text-[12px] text-rose-600">${esc(err.message)}</p>`;
      }
    } finally {
      if (!simular) bloquear(btn, false);
    }
  }

  /* Reporte del importador: mismos chips que la planilla de convenios, con el
     detalle de las filas que tienen error y de los primeros cambios. */
  function renderReportePdfPrestaciones(reporte) {
    const panel = document.getElementById("fact-prest-pdf-panel");
    const cont = document.getElementById("fact-prest-pdf-reporte");
    if (!cont) return;
    if (panel) panel.classList.remove("hide");

    const chip = (texto, clase) => `<span class="badge ${clase}">${esc(texto)}</span>`;
    const aImportar = reporte.creados + reporte.actualizados;
    const errores = (reporte.detalle || []).filter((f) => f.estado === "ERROR");
    const cambios = (reporte.detalle || [])
      .filter((f) => f.estado === "NUEVO" || f.estado === "ACTUALIZADO")
      .slice(0, 20);

    cont.innerHTML =
      `<div class="conv-reporte-totales">` +
      chip(`${reporte.filas_leidas} práctica(s) leídas`, "badge-slate") +
      chip(`${reporte.creados} nueva(s)`, "badge-brand") +
      chip(`${reporte.actualizados} actualizada(s)`, "badge-green") +
      chip(`${reporte.sin_cambios} sin cambios`, "badge-slate") +
      chip(
        `${reporte.sin_precio} sin precio`,
        reporte.sin_precio ? "badge-amber" : "badge-slate"
      ) +
      chip(
        `${reporte.errores} con error`,
        reporte.errores ? "badge-red" : "badge-slate"
      ) +
      `</div>` +
      `<p class="text-[11px] text-slate-500 mt-2">` +
      (reporte.simulado
        ? "Vista previa: todavía no se guardó nada. Revisá el resumen y confirmá la importación."
        : "Importación aplicada: el catálogo ya tiene estos códigos y honorarios.") +
      (reporte.sin_precio
        ? " Las prácticas sin honorario se crean con valor 0 (o conservan el que tenían)."
        : "") +
      `</p>` +
      (errores.length
        ? `<ul class="conv-reporte-lista">` +
          errores
            .slice(0, 20)
            .map(
              (fila) =>
                `<li><span class="conv-linea">pág. ${fila.pagina}</span> · ` +
                `<span class="font-mono">${esc(fila.codigo || "—")}</span> · ` +
                `<span class="text-rose-600">${esc(fila.mensaje || "error")}</span></li>`
            )
            .join("") +
          (errores.length > 20
            ? `<li class="text-slate-400">… y ${errores.length - 20} fila(s) más con error.</li>`
            : "") +
          `</ul>`
        : "") +
      (cambios.length
        ? `<ul class="conv-reporte-lista">` +
          cambios
            .map(
              (fila) =>
                `<li><span class="font-mono">${esc(fila.codigo)}</span> · ` +
                `${esc(fila.descripcion)} · ` +
                `<span class="text-slate-500">${fmtMoneda(fila.valor_base || 0)}</span> · ` +
                `<span class="${fila.estado === "NUEVO" ? "text-brand-700" : "text-emerald-700"}">` +
                `${fila.estado === "NUEVO" ? "nueva" : "actualizada"}</span></li>`
            )
            .join("") +
          (aImportar > cambios.length
            ? `<li class="text-slate-400">… y ${aImportar - cambios.length} práctica(s) más.</li>`
            : "") +
          `</ul>`
        : "");

    const confirmar = document.getElementById("fact-prest-pdf-confirmar");
    if (confirmar) {
      confirmar.classList.toggle("hide", !(reporte.simulado && aImportar > 0));
      confirmar.textContent = `Confirmar importación (${aImportar})`;
    }
  }

  /* Modal de prestación: alta (``id`` vacío) o edición del catálogo. */
  function abrirModalPrestacion(id) {
    const prestacion = id
      ? state.factPrestaciones.find((p) => p.id === Number(id))
      : null;
    state.factPrestacionEditId = prestacion ? prestacion.id : null;
    const set = (campo, valor) => {
      const el = document.getElementById(campo);
      if (el) el.value = valor;
    };
    const setTexto = (campo, valor) => {
      const el = document.getElementById(campo);
      if (el) el.textContent = valor;
    };
    setTexto(
      "modal-prest-titulo",
      prestacion ? `Editar ${prestacion.codigo_nomenclador}` : "Nueva prestación"
    );
    set("fact-prest-codigo", prestacion ? prestacion.codigo_nomenclador : "");
    set("fact-prest-descripcion", prestacion ? prestacion.descripcion : "");
    set("fact-prest-valor", prestacion ? prestacion.valor_base : 0);
    const activa = document.getElementById("fact-prest-activa");
    if (activa) activa.checked = prestacion ? Boolean(prestacion.activa) : true;
    ocultarAviso("fact-prest-error");
    mostrarModal("modal-prestacion");
  }

  function cerrarModalPrestacion() {
    state.factPrestacionEditId = null;
    ocultarModal("modal-prestacion");
  }

  /* POST /prestaciones (alta) o PUT /prestaciones/{id} (edición completa). */
  async function guardarPrestacion(ev) {
    ev.preventDefault();
    const btn = document.getElementById("modal-prest-save");
    const editando = Boolean(state.factPrestacionEditId);
    const payload = {
      codigo_nomenclador: valorDe("fact-prest-codigo").trim(),
      descripcion: valorDe("fact-prest-descripcion").trim(),
      valor_base: Number(valorDe("fact-prest-valor")) || 0,
      activa: Boolean(document.getElementById("fact-prest-activa").checked),
    };
    try {
      bloquear(btn, true);
      await api(rutaPrestacion(state.factPrestacionEditId), {
        method: editando ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      cerrarModalPrestacion();
      toast(
        "exito",
        editando ? "Prestación actualizada" : "Prestación creada",
        `${payload.codigo_nomenclador} · ${payload.descripcion}`
      );
      await cargarPrestaciones();
    } catch (err) {
      mostrarAviso("fact-prest-error", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* Baja lógica (DELETE) y reactivación (PATCH): la práctica nunca se borra. */
  async function cambiarEstadoPrestacion(id, activa) {
    const prestacion = state.factPrestaciones.find((p) => p.id === Number(id));
    if (!prestacion) return;
    try {
      if (activa) {
        await api(rutaPrestacion(prestacion.id), {
          method: "PATCH",
          body: JSON.stringify({ activa: true }),
        });
      } else {
        await api(rutaPrestacion(prestacion.id), { method: "DELETE" });
      }
      toast(
        "exito",
        `Prestación ${activa ? "reactivada" : "dada de baja"}`,
        `${prestacion.codigo_nomenclador} · ${prestacion.descripcion}`
      );
      await cargarPrestaciones();
    } catch (err) {
      toast("error", "No se pudo cambiar el estado de la prestación", err.message);
    }
  }

  /* ---------------------- Aranceles de la cobertura ---------------------- */
  async function cargarAranceles() {
    const osId = state.factArrObraSocial;
    if (!osId) {
      state.factAranceles = [];
      renderAranceles();
      renderPrestaciones();
      return;
    }
    try {
      state.factAranceles = (await api(`${OBRAS_SOCIALES}/${osId}/aranceles`)) || [];
      renderAranceles();
      renderPrestaciones();
    } catch (err) {
      toast("error", "No se pudieron cargar los aranceles", err.message);
    }
  }

  function renderAranceles() {
    const tbody = document.getElementById("fact-lista-aranceles");
    if (!tbody) return;
    const obra = obraSocialPorId(state.factArrObraSocial);
    if (!obra) {
      tbody.innerHTML =
        `<tr><td colspan="6">` +
        vacio("Elegí una cobertura", "Los aranceles se cargan por obra social.") +
        `</td></tr>`;
      return;
    }
    if (!state.factAranceles.length) {
      tbody.innerHTML =
        `<tr><td colspan="6">` +
        vacio(
          `${obra.nombre} no tiene aranceles cargados`,
          "Mientras falten, esas prácticas se cobran a valor de referencia al facturar."
        ) +
        `</td></tr>`;
      return;
    }

    tbody.innerHTML = state.factAranceles
      .map(
        (a) =>
          `<tr>` +
          `<td class="font-mono text-xs">${esc(a.prestacion_codigo || "—")}</td>` +
          `<td class="font-semibold text-slate-800">${esc(a.prestacion_descripcion || "—")}</td>` +
          `<td class="text-right text-slate-500">${fmtMoneda(a.monto_cubre_os)}</td>` +
          `<td class="text-right font-semibold">${fmtMoneda(a.monto_coseguro_paciente)}</td>` +
          `<td class="text-right">${fmtMoneda(a.monto_total)}</td>` +
          `<td><div class="flex items-center justify-end gap-2">` +
          `<button type="button" class="btn btn-ghost btn-sm" data-fact-editar-arancel="${a.id}">Editar</button>` +
          `<button type="button" class="btn btn-danger-soft btn-sm" data-fact-eliminar-arancel="${a.id}" ` +
          `title="Quitar el arancel de ${esc(obra.nombre)} para esa práctica">Quitar</button>` +
          `</div></td></tr>`
      )
      .join("");
  }

  /* Modal de arancel: alta (``id`` vacío) o edición de los montos. */
  function abrirModalArancel(id) {
    const arancel = id
      ? state.factAranceles.find((a) => a.id === Number(id))
      : null;
    const obra = obraSocialPorId(state.factArrObraSocial);
    if (!obra) {
      toast("info", "Elegí una obra social", "Los aranceles se cargan por cobertura.");
      return;
    }
    if (!state.factPrestaciones.length) {
      toast("info", "Cargá primero el catálogo", "Sin prestaciones no hay arancel que cargar.");
      return;
    }

    state.factArancelEditId = arancel ? arancel.id : null;
    const set = (campo, valor) => {
      const el = document.getElementById(campo);
      if (el) el.value = valor;
    };
    const setTexto = (campo, valor) => {
      const el = document.getElementById(campo);
      if (el) el.textContent = valor;
    };
    setTexto(
      "modal-arr-titulo",
      arancel ? `Editar arancel de ${obra.nombre}` : `Cargar arancel de ${obra.nombre}`
    );
    setTexto(
      "modal-arr-sub",
      arancel
        ? "Se actualizan los montos de esa práctica (el par cobertura/práctica no cambia)."
        : "Cuánto cubre la cobertura y cuánto pone el paciente."
    );
    cargarSelectoresFacturacion();
    const sel = document.getElementById("fact-arr-prestacion");
    if (sel) {
      sel.value = arancel ? String(arancel.prestacion_id) : "";
      // En edición la práctica no se puede cambiar: identifica el arancel.
      sel.disabled = Boolean(arancel);
    }
    set("fact-arr-cubre", arancel ? arancel.monto_cubre_os : 0);
    set("fact-arr-coseguro", arancel ? arancel.monto_coseguro_paciente : 0);
    renderTotalArancel();
    ocultarAviso("fact-arr-error");
    mostrarModal("modal-arancel");
  }

  function cerrarModalArancel() {
    state.factArancelEditId = null;
    const sel = document.getElementById("fact-arr-prestacion");
    if (sel) sel.disabled = false;
    ocultarAviso("fact-arr-error");
    ocultarModal("modal-arancel");
  }

  /* Total en vivo del arancel: cobertura + coseguro. */
  function renderTotalArancel() {
    const total =
      (Number(valorDe("fact-arr-cubre")) || 0) +
      (Number(valorDe("fact-arr-coseguro")) || 0);
    const el = document.getElementById("fact-arr-total");
    if (el) el.textContent = fmtMoneda(total);
  }

  /* POST /obras-sociales/{id}/aranceles (alta) o PUT /aranceles/{id} (edición). */
  async function guardarArancel(ev) {
    ev.preventDefault();
    const btn = document.getElementById("modal-arr-save");
    const obraId = state.factArrObraSocial;
    const prestacionId = Number(valorDe("fact-arr-prestacion"));
    if (!obraId || !prestacionId) {
      mostrarAviso("fact-arr-error", "Elegí la práctica que se va a arancelar.");
      return;
    }
    const payload = {
      prestacion_id: prestacionId,
      monto_cubre_os: Number(valorDe("fact-arr-cubre")) || 0,
      monto_coseguro_paciente: Number(valorDe("fact-arr-coseguro")) || 0,
    };
    const editando = Boolean(state.factArancelEditId);
    try {
      bloquear(btn, true);
      if (editando) {
        await api(rutaArancel(state.factArancelEditId), {
          method: "PUT",
          body: JSON.stringify(payload),
        });
      } else {
        await api(`${OBRAS_SOCIALES}/${obraId}/aranceles`, {
          method: "POST",
          body: JSON.stringify(payload),
        });
      }
      const obra = obraSocialPorId(obraId);
      cerrarModalArancel();
      toast(
        "exito",
        editando ? "Arancel actualizado" : "Arancel cargado",
        `${obra ? obra.nombre : "Cobertura"} · cubre ${fmtMoneda(payload.monto_cubre_os)} ` +
          `· coseguro ${fmtMoneda(payload.monto_coseguro_paciente)}`
      );
      await cargarAranceles();
    } catch (err) {
      mostrarAviso("fact-arr-error", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* DELETE /aranceles/{id}: es configuración, se puede quitar y volver a cargar
     (las facturas emitidas conservan sus importes congelados). */
  async function eliminarArancel(id) {
    const arancel = state.factAranceles.find((a) => a.id === Number(id));
    if (!arancel) return;
    const ok = window.confirm(
      `¿Quitar el arancel de ${arancel.prestacion_codigo} ` +
        `(${arancel.prestacion_descripcion})? Las facturas ya emitidas no cambian.`
    );
    if (!ok) return;
    try {
      await api(rutaArancel(arancel.id), { method: "DELETE" });
      toast("exito", "Arancel eliminado", `${arancel.prestacion_codigo}`);
      await cargarAranceles();
    } catch (err) {
      toast("error", "No se pudo eliminar el arancel", err.message);
    }
  }

  /* ------------------- Convenios por obra social (grilla) ---------------- */
  /* La grilla de convenios es la tabla de precios acordada con una cobertura:
     cada práctica del nomenclador base de Córdoba con el monto que cubre la OS y
     el **coseguro sugerido** (arancel base - monto). Se guarda en lote y también
     se puede completar desde la planilla estándar de Excel (importador masivo). */
  function rutaConvenio(obraSocialId, sufijo) {
    const base = `${OBRAS_SOCIALES}/${obraSocialId}/convenio`;
    return sufijo ? `${base}/${sufijo}` : base;
  }

  /* Importador masivo: el endpoint cuelga de la obra social, no de /convenio. */
  function rutaImportarAranceles(obraSocialId) {
    return `${OBRAS_SOCIALES}/${obraSocialId}/importar-aranceles`;
  }

  /* Coseguro sugerido: el arancel base menos lo que cubre la OS. Nunca negativo
     (si la cobertura paga de más, el mostrador no cobra nada). Es la misma regla
     que aplica el servidor al guardar y al importar la planilla. */
  function coseguroDeConvenio(valorBase, montoOs) {
    const diferencia = (Number(valorBase) || 0) - (Number(montoOs) || 0);
    return diferencia > 0 ? Math.round(diferencia * 100) / 100 : 0;
  }

  /* Lo que se está mostrando en una fila: si la secretaría escribió un monto
     nuevo, esa edición manda sobre el valor que trajo el servidor. */
  function valoresConvenio(fila) {
    const clave = String(fila.prestacion_id);
    const tocado = Object.prototype.hasOwnProperty.call(state.convEdiciones, clave);
    return {
      cubre: tocado ? state.convEdiciones[clave] : Number(fila.monto_cubre_os) || 0,
      tocado: tocado,
    };
  }

  function actualizarPendientesConvenio() {
    const pendientes = Object.keys(state.convEdiciones).length;
    const badge = document.getElementById("conv-pendientes");
    if (badge) {
      badge.textContent = `${pendientes} sin guardar`;
      badge.classList.toggle("hide", pendientes === 0);
    }
    const btn = document.getElementById("conv-guardar");
    if (btn) btn.disabled = pendientes === 0;
  }

  /* Totales de la grilla: arancel base, lo que cubre la OS y el coseguro
     sugerido, con los montos que se están viendo en pantalla (ediciones incluidas). */
  function renderTotalesConvenio() {
    const set = (id, texto) => {
      const el = document.getElementById(id);
      if (el) el.textContent = texto;
    };
    const convenio = state.factConvenio;
    if (!convenio) {
      ["conv-total-base", "conv-total-os", "conv-total-coseguro"].forEach((id) =>
        set(id, "—")
      );
      ["conv-pie-base", "conv-pie-os", "conv-pie-coseguro"].forEach((id) =>
        set(id, fmtMoneda(0))
      );
      actualizarPendientesConvenio();
      return;
    }

    let base = 0;
    let cubre = 0;
    let sugerido = 0;
    convenio.filas.forEach((fila) => {
      const valores = valoresConvenio(fila);
      base += Number(fila.valor_base) || 0;
      cubre += valores.cubre;
      sugerido += coseguroDeConvenio(fila.valor_base, valores.cubre);
    });
    set("conv-total-base", fmtMoneda(base));
    set("conv-total-os", fmtMoneda(cubre));
    set("conv-total-coseguro", fmtMoneda(sugerido));
    set("conv-pie-base", fmtMoneda(base));
    set("conv-pie-os", fmtMoneda(cubre));
    set("conv-pie-coseguro", fmtMoneda(sugerido));
    actualizarPendientesConvenio();
  }

  /* GET /obras-sociales/{id}/convenio: la grilla completa de la cobertura. */
  async function cargarConvenio() {
    const osId = state.factArrObraSocial;
    state.convEdiciones = {};
    if (!osId) {
      state.factConvenio = null;
      renderConvenio();
      return;
    }
    try {
      state.factConvenio = await api(rutaConvenio(osId));
      ocultarAviso("conv-error");
      renderConvenio();
    } catch (err) {
      state.factConvenio = null;
      renderConvenio();
      toast("error", "No se pudo cargar el convenio", err.message);
    }
  }

  /* Una fila de la grilla: el monto que cubre la OS es editable y el coseguro
     sugerido se recalcula a cada tecla (sin redibujar, para no perder el foco). */
  function filaConvenioHtml(fila) {
    const convenio = state.factConvenio || {};
    const valores = valoresConvenio(fila);
    const sugerido = coseguroDeConvenio(fila.valor_base, valores.cubre);
    const estado = fila.cargado
      ? `<span class="badge badge-green">${ICON.check}Cargado</span>`
      : `<span class="badge badge-slate">Sin cargar</span>`;
    return (
      `<tr data-conv-fila="${fila.prestacion_id}"` +
      `${valores.tocado ? ' class="conv-cambiado"' : ""}>` +
      `<td class="font-mono text-xs">${esc(fila.codigo_nomenclador)}</td>` +
      `<td class="font-semibold text-slate-800">${esc(fila.descripcion)}</td>` +
      `<td class="text-right text-slate-500">${fmtMoneda(fila.valor_base)}</td>` +
      `<td class="text-right"><input type="number" min="0" step="0.01" ` +
      `class="field conv-monto" data-conv-monto="${fila.prestacion_id}" ` +
      `value="${valores.cubre}" title="Monto que cubre ` +
      `${esc(convenio.obra_social_nombre || "la obra social")} por esta práctica" /></td>` +
      `<td class="text-right"><span class="conv-coseguro" ` +
      `data-conv-coseguro="${fila.prestacion_id}">${fmtMoneda(sugerido)}</span></td>` +
      `<td>${estado}</td></tr>`
    );
  }

  function renderConvenio() {
    const tbody = document.getElementById("conv-lista");
    const contador = document.getElementById("conv-contador");
    if (!tbody) return;
    const convenio = state.factConvenio;
    if (!convenio) {
      if (contador) contador.textContent = "—";
      tbody.innerHTML =
        `<tr><td colspan="6">` +
        vacio(
          "Elegí una obra social",
          "El convenio (los precios acordados) se carga por cobertura."
        ) +
        `</td></tr>`;
      renderTotalesConvenio();
      return;
    }

    const busqueda = normalizarTexto(state.convBusqueda).trim();
    const visibles = busqueda
      ? convenio.filas.filter(
          (fila) =>
            normalizarTexto(
              `${fila.codigo_nomenclador} ${fila.descripcion}`
            ).indexOf(busqueda) !== -1
        )
      : convenio.filas;
    if (contador) {
      contador.textContent = busqueda
        ? `${visibles.length} de ${convenio.filas.length}`
        : String(convenio.filas.length);
    }
    tbody.innerHTML = visibles.length
      ? visibles.map(filaConvenioHtml).join("")
      : `<tr><td colspan="6">` +
        vacio(
          `Sin coincidencias para «${state.convBusqueda}»`,
          "Probá con el código del nomenclador o con parte del nombre."
        ) +
        `</td></tr>`;
    renderTotalesConvenio();
  }

  /* Edición en vivo del monto que cubre la OS: actualiza la edición pendiente, el
     coseguro sugerido de esa fila y los totales de la grilla. */
  function fijarMontoConvenio(prestacionId, valorTexto) {
    const filas = (state.factConvenio && state.factConvenio.filas) || [];
    const fila = filas.find((f) => String(f.prestacion_id) === String(prestacionId));
    if (!fila) return;
    const clave = String(prestacionId);
    const numero = valorTexto === "" ? 0 : Number(valorTexto);
    if (!isFinite(numero) || numero < 0) {
      toast(
        "info",
        "Monto inválido",
        "El monto que cubre la obra social no puede ser negativo."
      );
      return;
    }

    if (numero === (Number(fila.monto_cubre_os) || 0)) {
      delete state.convEdiciones[clave];
    } else {
      state.convEdiciones[clave] = numero;
    }

    const celda = document.querySelector(`[data-conv-coseguro="${prestacionId}"]`);
    if (celda) {
      celda.textContent = fmtMoneda(coseguroDeConvenio(fila.valor_base, numero));
    }
    const renglon = document.querySelector(`[data-conv-fila="${prestacionId}"]`);
    if (renglon) {
      renglon.classList.toggle(
        "conv-cambiado",
        Object.prototype.hasOwnProperty.call(state.convEdiciones, clave)
      );
    }
    renderTotalesConvenio();
  }

  /* PUT /obras-sociales/{id}/convenio: guarda sólo las filas modificadas. */
  async function guardarConvenio() {
    const osId = state.factArrObraSocial;
    const btn = document.getElementById("conv-guardar");
    const ids = Object.keys(state.convEdiciones);
    if (!osId) {
      toast("info", "Elegí una obra social", "El convenio se guarda por cobertura.");
      return;
    }
    if (!ids.length) {
      toast(
        "info",
        "No hay cambios para guardar",
        "Modificá el monto que cubre la OS en alguna práctica."
      );
      return;
    }

    const filas = ids.map((id) => ({
      prestacion_id: Number(id),
      monto_cubre_os: state.convEdiciones[id],
    }));
    try {
      bloquear(btn, true);
      const convenio = await api(rutaConvenio(osId), {
        method: "PUT",
        body: JSON.stringify({ filas: filas }),
      });
      state.factConvenio = convenio;
      state.convEdiciones = {};
      ocultarAviso("conv-error");
      renderConvenio();
      toast(
        "exito",
        "Convenio guardado",
        `${convenio.actualizados} actualizada${convenio.actualizados === 1 ? "" : "s"} y ` +
          `${convenio.creados} nueva${convenio.creados === 1 ? "" : "s"} para ` +
          `${convenio.obra_social_nombre}.`
      );
      // La pestaña "Prestaciones y aranceles" muestra la misma tabla de precios.
      await cargarAranceles();
    } catch (err) {
      mostrarAviso("conv-error", err.message);
    } finally {
      bloquear(btn, false);
      // ``bloquear`` vuelve a habilitar el botón: con la grilla ya guardada no
      // quedan cambios pendientes, así que "Guardar cambios" queda inactivo.
      actualizarPendientesConvenio();
    }
  }

  /* ----------------- Importador masivo (plantilla estándar) -------------- */
  /* El circuito es: descargar la plantilla de ejemplo (dos columnas: ``codigo`` y
     ``precio_cobertura``), completarla en Excel, arrastrarla al recuadro y
     confirmar. Primero se pide una **vista previa** (``simular``) para que la
     secretaría vea cuántos códigos entran y cuáles tienen error. */
  async function descargarPlantillaConvenio() {
    const osId = state.factArrObraSocial;
    const btn = document.getElementById("conv-plantilla");
    if (!osId) {
      toast("info", "Elegí una obra social", "La plantilla se arma por cobertura.");
      return;
    }
    const formato = valorDe("conv-formato") === "xlsx" ? "xlsx" : "csv";
    const obra = obraSocialPorId(osId);
    try {
      bloquear(btn, true);
      await descargarArchivo(
        rutaConvenio(osId, `plantilla?formato=${formato}`),
        `plantilla-aranceles-${slug(obra ? obra.nombre : "cobertura")}.${formato}`
      );
      toast(
        "info",
        "Plantilla descargada",
        "Completá la columna «precio_cobertura» y arrastrá el archivo acá."
      );
    } catch (err) {
      toast("error", "No se pudo descargar la plantilla", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* Lee el archivo elegido y lo pasa a base64 (sirve igual para CSV y para XLSX). */
  function base64DeArchivo(archivo) {
    return new Promise(function (resolve, reject) {
      const lector = new FileReader();
      lector.onload = function () {
        const resultado = String(lector.result || "");
        const coma = resultado.indexOf(",");
        resolve(coma === -1 ? "" : resultado.slice(coma + 1));
      };
      lector.onerror = function () {
        reject(new Error("No se pudo leer el archivo elegido."));
      };
      lector.readAsDataURL(archivo);
    });
  }

  async function elegirArchivoConvenio(archivo) {
    if (!archivo) return;
    try {
      const contenido = await base64DeArchivo(archivo);
      state.convArchivo = { nombre: archivo.name, contenido: contenido };
      const etiqueta = document.getElementById("conv-archivo-nombre");
      if (etiqueta) {
        const kb = Math.max(1, Math.round(archivo.size / 1024));
        etiqueta.textContent = `Archivo elegido: ${archivo.name} (${kb} KB)`;
        etiqueta.classList.remove("hide");
      }
      await previsualizarImportacion();
    } catch (err) {
      toast("error", "No se pudo leer el archivo", err.message);
    }
  }

  /* POST /obras-sociales/{id}/importar-aranceles con ``simular`` (vista previa) o
     sin él (importación real). */
  async function importarPlanillaConvenio(simular) {
    const osId = state.factArrObraSocial;
    const btn = document.getElementById(simular ? "conv-plantilla" : "conv-confirmar");
    if (!osId || !state.convArchivo) return;
    try {
      bloquear(btn, true);
      const reporte = await api(rutaImportarAranceles(osId), {
        method: "POST",
        body: JSON.stringify({
          nombre_archivo: state.convArchivo.nombre,
          contenido_base64: state.convArchivo.contenido,
          simular: simular,
        }),
      });
      state.convReporte = reporte;
      ocultarAviso("conv-error");
      renderReporteImportacion(reporte);
      if (!simular) {
        toast(
          "exito",
          "Aranceles importados",
          `${reporte.creados} nueva${reporte.creados === 1 ? "" : "s"} y ` +
            `${reporte.actualizados} actualizada${reporte.actualizados === 1 ? "" : "s"} ` +
            `en ${reporte.obra_social_nombre}` +
            (reporte.errores ? ` · ${reporte.errores} fila(s) con error.` : ".")
        );
        limpiarImportacionConvenio(true);
        await cargarConvenio();
      }
    } catch (err) {
      mostrarAviso("conv-error", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  function previsualizarImportacion() {
    return importarPlanillaConvenio(true);
  }

  function confirmarImportacion() {
    return importarPlanillaConvenio(false);
  }

  /* Reporte de la planilla: cuántas filas entran, cuántas fallan y por qué. */
  function renderReporteImportacion(reporte) {
    const cont = document.getElementById("conv-reporte");
    if (!cont) return;
    const confirmar = document.getElementById("conv-confirmar");
    const limpiar = document.getElementById("conv-limpiar");
    const errores = (reporte.detalle || []).filter((f) => f.estado === "ERROR");
    const aImportar = reporte.creados + reporte.actualizados;
    const chip = (texto, clase) => `<span class="badge ${clase}">${esc(texto)}</span>`;

    cont.innerHTML =
      `<div class="conv-reporte-totales">` +
      chip(`${reporte.filas_leidas} fila(s) leídas`, "badge-slate") +
      chip(`${reporte.creados} nueva(s)`, "badge-brand") +
      chip(`${reporte.actualizados} actualizada(s)`, "badge-green") +
      chip(`${reporte.sin_cambios} sin cambios`, "badge-slate") +
      chip(`${reporte.ignoradas} sin precio`, "badge-amber") +
      chip(
        `${reporte.errores} con error`,
        reporte.errores ? "badge-red" : "badge-slate"
      ) +
      `</div>` +
      `<p class="text-[11px] text-slate-400 mt-2">` +
      (reporte.simulado
        ? "Vista previa: todavía no se guardó nada. Revisá los errores y confirmá la importación."
        : "Importación aplicada: el convenio ya tiene estos precios.") +
      `</p>` +
      (errores.length
        ? `<ul class="conv-reporte-lista">` +
          errores
            .slice(0, 40)
            .map(
              (fila) =>
                `<li><span class="conv-linea">línea ${fila.linea}</span> · ` +
                `<span class="font-mono">${esc(fila.codigo || "—")}</span> · ` +
                `<span class="text-rose-600">${esc(fila.mensaje || "error")}</span></li>`
            )
            .join("") +
          (errores.length > 40
            ? `<li class="text-slate-400">… y ${errores.length - 40} fila(s) más con error.</li>`
            : "") +
          `</ul>`
        : "");
    cont.classList.remove("hide");
    if (limpiar) limpiar.classList.remove("hide");
    if (confirmar) {
      const mostrar = reporte.simulado && aImportar > 0;
      confirmar.classList.toggle("hide", !mostrar);
      confirmar.textContent = `Confirmar importación (${aImportar})`;
    }
  }

  /* Vuelve el importador a cero. Con ``conservarReporte`` deja a la vista el
     resultado de la importación que se acaba de aplicar. */
  function limpiarImportacionConvenio(conservarReporte) {
    state.convArchivo = null;
    const input = document.getElementById("conv-archivo");
    if (input) input.value = "";
    const etiqueta = document.getElementById("conv-archivo-nombre");
    if (etiqueta) {
      etiqueta.textContent = "";
      etiqueta.classList.add("hide");
    }
    const confirmar = document.getElementById("conv-confirmar");
    if (confirmar) confirmar.classList.add("hide");
    const limpiar = document.getElementById("conv-limpiar");
    if (limpiar) limpiar.classList.add("hide");
    if (!conservarReporte) {
      state.convReporte = null;
      const cont = document.getElementById("conv-reporte");
      if (cont) {
        cont.innerHTML = "";
        cont.classList.add("hide");
      }
    }
  }

  /* ------------------------------ Navegación ----------------------------- */
  const VISTAS = {
    dashboard: ["Panel Principal", "Resumen del consultorio y estado de los recordatorios"],
    calendario: ["Calendario", "Calendario mensual de turnos y disponibilidad por día"],
    disponibilidad: [
      "Disponibilidad médica",
      "Agenda semanal por odontólogo: rangos de mañana y tarde para cada día",
    ],
    recordatorios: ["Recordatorios pendientes", "Turnos próximos que todavía no recibieron el aviso de WhatsApp"],
    "fichas-salud": [
      "Fichas de salud",
      "Declaración de salud de los próximos turnos, con enlace público y alerta médica",
    ],
    doctores: ["Doctores", "Registro de profesionales del consultorio"],
    pacientes: ["Pacientes", "Registro de pacientes y sus teléfonos de WhatsApp"],
    "obras-sociales": [
      "Obras Sociales",
      "Catálogo de coberturas que se ofrecen al registrar un paciente",
    ],
    "bot-whatsapp": [
      "Bot WhatsApp",
      "Menú de opciones que el bot le ofrece a los pacientes por WhatsApp",
    ],
    facturacion: [
      "Facturación y Caja",
      "Cierre de visitas, coseguros del mostrador y liquidación de obras sociales y profesionales",
    ],
  };

  /* Subtítulo del encabezado superior. El <h1> muestra la operación activa (la
     pestaña «dashboard» se llama «Panel Principal») y el subtítulo explica para
     qué sirve. */
  function subtituloVista(v) {
    const [, desc] = VISTAS[v] || ["", ""];
    return desc;
  }

  function cambiarVista(v) {
    if (!VISTAS[v]) return;
    state.vista = v;
    document.querySelectorAll("main > section").forEach((s) => s.classList.add("hide"));
    const sec = document.getElementById("view-" + v);
    if (sec) sec.classList.remove("hide");
    document.querySelectorAll(".nav-link[data-view]").forEach((a) =>
      a.classList.toggle("active", a.dataset.view === v));
    // El encabezado de arriba de todo sigue a la operación elegida en el menú:
    // el <h1> lleva su nombre y el subtítulo, para qué sirve.
    document.getElementById("page-title").textContent = VISTAS[v][0];
    document.getElementById("page-subtitle").textContent = subtituloVista(v);
    document.getElementById("sidebar").classList.remove("open");
    if (v !== "dashboard") ocultarAlertaPanel();
    if (v === "recordatorios") {
      // Al entrar a la pestaña se refresca la lista y el subtítulo con la
      // ventana de días vigente.
      actualizarSubtituloRecordatorios();
      cargarRecordatorios();
    }
    if (v === "fichas-salud") {
      // El panel de anamnesis se refresca al entrar para ver los cambios que
      // hizo el paciente desde el enlace público.
      actualizarSubtituloFichas();
      renderFichasSalud();
      cargarFichasSalud();
    }
    if (v === "disponibilidad") {
      // La grilla semanal se redibuja con lo que ya haya en memoria; si todavía
      // no llegó la disposición del servidor, muestra el estado de carga.
      renderDisponibilidad();
      // La tarjeta de días personalizados («Gestionar un día específico») usa el
      // mismo odontólogo seleccionado y los tramos ya cacheados.
      renderExcepciones();
    }
    if (v === "facturacion") {
      // Al entrar se refresca la caja del día y la pestaña que estaba activa
      // (cada una pide al servidor lo que muestra).
      cargarFacturacion();
    }
    if (v === "bot-whatsapp") {
      // La tabla se dibuja con lo que ya está en memoria y la vista previa se
      // pide al backend: es el texto exacto que recibe el paciente.
      renderMenuWhatsApp();
      cargarVistaPreviaMenu();
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
  /* ------------- Liquidación del profesional: forma de pago (Módulo 3) ----- */
  /* Cómo se le paga a cada odontólogo. El selector de la vista "Doctores"
     replica el enum ``TipoPagoDoctor`` del backend y define los textos del campo
     numérico que se habilita al elegir comisión o sueldo:

     * ``DUENO``    → se queda con el 100 % de su producción (porcentaje opcional);
     * ``COMISION`` → un porcentaje (0-100) de la producción del mes;
     * ``SUELDO``   → un importe fijo por mes, sin importar la producción. */
  /* Las etiquetas visibles ("Dueño", "Comisión", "Sueldo fijo") salen de
     TIPO_PAGO_TEXTO: es el mismo texto que usan el reporte, el CSV y el PDF. */
  const PAGO_MODALIDAD = {
    DUENO: {
      campo: "Porcentaje de la producción (%)",
      ayuda:
        "Dueño del consultorio: se lleva el 100% de las prestaciones que factura. " +
        "Cargá un porcentaje sólo si retiene una parte (ej.: 60).",
      ejemplo: "100 (por defecto)",
      requerido: false,
    },
    COMISION: {
      campo: "Porcentaje de la comisión (%)",
      ayuda: "Porcentaje sobre la producción del mes, entre 0 y 100. Ej.: 40 → se lleva el 40%.",
      ejemplo: "Ej.: 40",
      requerido: true,
    },
    SUELDO: {
      campo: "Sueldo mensual ($)",
      ayuda: "Importe fijo por mes, sin importar cuánto produjo en el período.",
      ejemplo: "Ej.: 900000",
      requerido: true,
    },
  };

  /* Ids de los campos de pago en el formulario de alta y en el modal de edición. */
  const IDS_PAGO_ALTA = {
    select: "doc-tipo-pago",
    grupo: "doc-valor-grupo",
    label: "doc-valor-label",
    input: "doc-valor-pago",
    ayuda: "doc-pago-ayuda",
  };
  const IDS_PAGO_EDIT = {
    select: "doc-edit-tipo-pago",
    grupo: "doc-edit-valor-grupo",
    label: "doc-edit-valor-label",
    input: "doc-edit-valor-pago",
    ayuda: "doc-edit-pago-ayuda",
  };

  function datosPago(tipoPago) {
    return PAGO_MODALIDAD[tipoPago] || PAGO_MODALIDAD.DUENO;
  }

  /* Texto corto de la forma de pago: "Comisión · 40%", "Sueldo fijo · $ 900.000,00". */
  function textoPagoDoctor(doctor) {
    const etiqueta = TIPO_PAGO_TEXTO[doctor.tipo_pago] || doctor.tipo_pago;
    if (doctor.valor_pago == null) return etiqueta;
    const valor =
      doctor.tipo_pago === "SUELDO"
        ? fmtMoneda(doctor.valor_pago)
        : `${doctor.valor_pago}%`;
    return `${etiqueta} · ${valor}`;
  }

  /* Aplica el tipo elegido al formulario: habilita u oculta el campo numérico y
     ajusta su etiqueta, la ayuda y el ``required``. ``valorInicial`` precarga el
     importe guardado (así un dueño con porcentaje no pierde el dato al editar);
     al cambiar el select se pasa ``null`` y el campo arranca vacío. */
  function configurarCamposPago(tipoPago, ids, valorInicial) {
    const datos = datosPago(tipoPago);
    const grupo = document.getElementById(ids.grupo);
    const label = document.getElementById(ids.label);
    const entrada = document.getElementById(ids.input);
    const ayuda = document.getElementById(ids.ayuda);
    if (ayuda) ayuda.textContent = datos.ayuda;
    if (label) label.textContent = datos.campo;
    if (grupo) {
      const mostrar =
        datos.requerido || (valorInicial != null && valorInicial !== "");
      grupo.classList.toggle("hide", !mostrar);
    }
    if (entrada) {
      entrada.required = datos.requerido;
      entrada.placeholder = datos.ejemplo;
      entrada.value = valorInicial == null ? "" : valorInicial;
      if (tipoPago === "SUELDO") entrada.removeAttribute("max");
      else entrada.max = "100";
    }
  }

  /* Lee el tipo de pago y el importe del formulario (``null`` si no aplica). */
  function leerPagoFormulario(ids) {
    const select = document.getElementById(ids.select);
    const tipoPago = (select && select.value) || "DUENO";
    const grupo = document.getElementById(ids.grupo);
    const oculto = !grupo || grupo.classList.contains("hide");
    const entrada = document.getElementById(ids.input);
    const texto = entrada ? String(entrada.value).trim() : "";
    const numero = Number(texto);
    const valor =
      oculto || texto === "" || Number.isNaN(numero) ? null : numero;
    return { tipo_pago: tipoPago, valor_pago: valor };
  }

  /* Validación previa al envío: evita el 422 del backend con un mensaje claro. */
  function errorDePago(tipoPago, valorPago) {
    if (tipoPago === "COMISION") {
      if (valorPago == null) return "Ingresá el porcentaje de la comisión (0 a 100).";
      if (valorPago < 0 || valorPago > 100)
        return "El porcentaje de la comisión debe estar entre 0 y 100.";
    }
    if (tipoPago === "SUELDO" && (valorPago == null || valorPago <= 0)) {
      return "Ingresá el sueldo mensual del profesional (mayor que 0).";
    }
    if (tipoPago === "DUENO" && valorPago != null && (valorPago < 0 || valorPago > 100)) {
      return "El porcentaje que retiene el dueño debe estar entre 0 y 100.";
    }
    return "";
  }

  async function crearDoctor(ev) {
    ev.preventDefault();
    const form = ev.target;
    const btn = form.querySelector("button[type=submit]");
    const pago = leerPagoFormulario(IDS_PAGO_ALTA);
    const errorPago = errorDePago(pago.tipo_pago, pago.valor_pago);
    if (errorPago) {
      mostrarAviso("doc-error", errorPago);
      return;
    }
    const payload = {
      nombre: document.getElementById("doc-nombre").value.trim(),
      especialidad: document.getElementById("doc-especialidad").value.trim(),
      activo: document.getElementById("doc-activo").checked,
      ...pago,
    };
    try {
      bloquear(btn, true);
      const nuevo = await api("/doctores", { method: "POST", body: JSON.stringify(payload) });
      form.reset();
      document.getElementById("doc-activo").checked = true;
      configurarCamposPago("DUENO", IDS_PAGO_ALTA, null);
      ocultarAviso("doc-error");
      toast("exito", "Doctor guardado", `${nuevo.nombre} · ${nuevo.especialidad} · ${textoPagoDoctor(nuevo)}`);
      await cargarTodo();
    } catch (err) {
      toast("error", "No se pudo guardar el doctor", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* ------------------- Modales de gestión (edición y baja) --------------- */
  /* Los modales de doctor y de obra social comparten el mismo patrón: abrir,
     cerrar (botón, fondo y tecla Esc) y mostrar el error que devuelve la API
     dentro del propio formulario, sin perder lo ya cargado. */
  function mostrarModal(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove("hide");
    const foco = el.querySelector("input:not([type=checkbox]), select, textarea");
    if (foco && foco.focus) foco.focus();
  }

  function ocultarModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add("hide");
  }

  function mostrarAviso(id, msg) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = msg;
    el.classList.remove("hide");
  }

  function ocultarAviso(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add("hide");
  }

  /* ------------------------- Edición de doctores ------------------------- */
  /* El toggle "Activo" del modal permite reactivar a un profesional dado de baja
     sin salir de la vista "Doctores". */
  function abrirModalDoctor(id) {
    const doctor = porId(state.doctores, Number(id));
    if (!doctor) return;
    state.doctorEditId = doctor.id;
    const titulo = document.getElementById("modal-doctor-titulo");
    if (titulo) titulo.textContent = `Editar ${doctor.nombre}`;
    document.getElementById("doc-edit-nombre").value = doctor.nombre || "";
    document.getElementById("doc-edit-especialidad").value = doctor.especialidad || "";
    document.getElementById("doc-edit-activo").checked = Boolean(doctor.activo);
    /* Forma de pago: el select manda y el campo numérico se precarga con el valor
       guardado (un dueño con porcentaje no pierde el dato al reeditar). */
    document.getElementById("doc-edit-tipo-pago").value = doctor.tipo_pago || "DUENO";
    configurarCamposPago(doctor.tipo_pago, IDS_PAGO_EDIT, doctor.valor_pago);
    ocultarAviso("doctor-error");
    mostrarModal("modal-doctor");
  }

  function cerrarModalDoctor() {
    state.doctorEditId = null;
    ocultarModal("modal-doctor");
  }

  /* PUT /doctores/{id}: reemplaza nombre, especialidad y estado del profesional. */
  async function guardarEdicionDoctor(ev) {
    ev.preventDefault();
    if (!state.doctorEditId) return;
    const btn = document.getElementById("modal-doctor-save");
    const pago = leerPagoFormulario(IDS_PAGO_EDIT);
    const errorPago = errorDePago(pago.tipo_pago, pago.valor_pago);
    if (errorPago) {
      mostrarAviso("doctor-error", errorPago);
      return;
    }
    const payload = {
      nombre: document.getElementById("doc-edit-nombre").value.trim(),
      especialidad: document.getElementById("doc-edit-especialidad").value.trim(),
      activo: document.getElementById("doc-edit-activo").checked,
      ...pago,
    };
    try {
      bloquear(btn, true);
      const doctor = await api(`/doctores/${state.doctorEditId}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      cerrarModalDoctor();
      toast("exito", "Doctor actualizado", `${doctor.nombre} · ${doctor.especialidad} · ${textoPagoDoctor(doctor)}`);
      await cargarTodo();
    } catch (err) {
      mostrarAviso("doctor-error", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* DELETE /doctores/{id} es una baja lógica (``activo = false``) y PATCH la
     revierte. Los turnos ya agendados se conservan: el profesional deja de
     ofrecerse en las agendas pero su historial queda intacto. */
  async function cambiarEstadoDoctor(id, activo) {
    const doctor = porId(state.doctores, Number(id));
    if (!doctor) return;
    try {
      if (activo) {
        await api(`/doctores/${doctor.id}`, {
          method: "PATCH",
          body: JSON.stringify({ activo: true }),
        });
      } else {
        await api(`/doctores/${doctor.id}`, { method: "DELETE" });
      }
      toast(
        "exito",
        `Doctor ${activo ? "reactivado" : "dado de baja"}`,
        `${doctor.nombre} · ${doctor.especialidad}`
      );
      await cargarTodo();
    } catch (err) {
      toast("error", `No se pudo ${activo ? "reactivar" : "dar de baja"} al doctor`, err.message);
    }
  }

  /* --------------------- Obras sociales (alta y edición) ----------------- */
  /* El modal sirve para las dos operaciones: ``state.obraSocialEditId`` guarda el
     id en edición (``null`` = cobertura nueva). */
  function abrirModalObraSocial(id) {
    const obra = id ? obraSocialPorId(id) : null;
    state.obraSocialEditId = obra ? obra.id : null;
    const titulo = document.getElementById("modal-os-titulo");
    if (titulo) titulo.textContent = obra ? `Editar ${obra.nombre}` : "Nueva obra social";
    document.getElementById("os-nombre").value = obra ? obra.nombre : "";
    document.getElementById("os-codigo").value = obra && obra.codigo ? obra.codigo : "";
    document.getElementById("os-activa").checked = obra ? Boolean(obra.activa) : true;
    ocultarAviso("os-error");
    mostrarModal("modal-obra-social");
  }

  function cerrarModalObraSocial() {
    state.obraSocialEditId = null;
    const form = document.getElementById("form-obra-social");
    if (form) form.reset();
    ocultarModal("modal-obra-social");
  }

  /* POST /obras-sociales (alta) o PUT /{id} (reemplazo). Si el nombre ya existe
     el backend responde 409 y el mensaje se muestra dentro del modal. */
  async function guardarObraSocial(ev) {
    ev.preventDefault();
    const btn = document.getElementById("modal-os-save");
    const editando = Boolean(state.obraSocialEditId);
    const payload = {
      nombre: document.getElementById("os-nombre").value.trim(),
      codigo: document.getElementById("os-codigo").value.trim() || null,
      activa: document.getElementById("os-activa").checked,
    };
    try {
      bloquear(btn, true);
      const obra = await api(rutaObraSocial(state.obraSocialEditId), {
        method: editando ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      cerrarModalObraSocial();
      toast("exito", editando ? "Obra social actualizada" : "Obra social creada", obra.nombre);
      // Recarga el panel completo: cargarTodo → cargarSelectores rearma el
      // desplegable de coberturas del formulario de pacientes.
      await cargarTodo();
    } catch (err) {
      mostrarAviso("os-error", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* DELETE /obras-sociales/{id} da de baja la cobertura y PATCH la reactiva. Los
     pacientes que la tenían conservan el vínculo: la fila nunca se borra. */
  async function cambiarEstadoObraSocial(id, activa) {
    const obra = obraSocialPorId(id);
    if (!obra) return;
    try {
      if (activa) {
        await api(rutaObraSocial(obra.id), {
          method: "PATCH",
          body: JSON.stringify({ activa: true }),
        });
      } else {
        await api(rutaObraSocial(obra.id), { method: "DELETE" });
      }
      toast("exito", `Obra social ${activa ? "reactivada" : "desactivada"}`, obra.nombre);
      await cargarTodo();
    } catch (err) {
      toast(
        "error",
        `No se pudo ${activa ? "reactivar" : "desactivar"} la obra social`,
        err.message
      );
    }
  }

  /* ------------------ Menú del Bot WhatsApp (alta y edición) --------------- */
  /* El modal sirve para las dos operaciones: ``state.opcionMenuEditId`` guarda
     el id en edición (``null`` = opción nueva). Las teclas ya usadas por otra
     opción se deshabilitan para evitar el 409 del backend. */
  function tipoMenuEsTexto() {
    const sel = document.getElementById("menu-tipo");
    return sel ? sel.value === "TEXTO" : true;
  }

  /* El texto libre es el único tipo que necesita la respuesta escrita a mano;
     los demás la arman con datos del sistema, así que se oculta el campo. */
  function sincronizarTipoMenu() {
    const esTexto = tipoMenuEsTexto();
    const wrap = document.getElementById("menu-respuesta-wrap");
    const campo = document.getElementById("menu-respuesta");
    const hint = document.getElementById("menu-respuesta-hint");
    if (wrap) wrap.classList.toggle("hide", !esTexto);
    if (campo) campo.required = esTexto;
    if (hint) {
      hint.textContent = esTexto
        ? "Obligatoria: es el mensaje que recibe el paciente."
        : "Este tipo no usa texto libre: el bot arma la respuesta con los datos del sistema.";
    }
  }

  function actualizarNumerosDisponiblesMenu() {
    const sel = document.getElementById("menu-numero");
    if (!sel) return;
    const usados = new Set(
      state.menuWhatsApp
        .filter((o) => String(o.id) !== String(state.opcionMenuEditId))
        .map((o) => String(o.numero))
    );
    Array.prototype.forEach.call(sel.options, (op) => {
      op.disabled = usados.has(op.value);
    });
  }

  function primerNumeroLibreMenu() {
    const usados = new Set(state.menuWhatsApp.map((o) => String(o.numero)));
    const orden = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"];
    return orden.find((n) => !usados.has(n)) || orden[0];
  }

  function abrirModalOpcionMenu(id) {
    const opcion = id ? opcionMenuPorId(id) : null;
    state.opcionMenuEditId = opcion ? opcion.id : null;

    const titulo = document.getElementById("modal-menu-titulo");
    if (titulo) titulo.textContent = opcion ? `Editar «${opcion.titulo}»` : "Nueva opción del menú";
    document.getElementById("menu-numero").value = opcion
      ? String(opcion.numero)
      : primerNumeroLibreMenu();
    document.getElementById("menu-titulo").value = opcion ? opcion.titulo : "";
    document.getElementById("menu-tipo").value = opcion ? opcion.tipo : "TEXTO";
    document.getElementById("menu-respuesta").value =
      opcion && opcion.respuesta ? opcion.respuesta : "";
    document.getElementById("menu-orden").value = opcion ? opcion.orden : state.menuWhatsApp.length;
    document.getElementById("menu-activa").checked = opcion ? Boolean(opcion.activa) : true;

    actualizarNumerosDisponiblesMenu();
    sincronizarTipoMenu();
    ocultarAviso("menu-error");
    mostrarModal("modal-opcion-menu");
  }

  function cerrarModalOpcionMenu() {
    state.opcionMenuEditId = null;
    const form = document.getElementById("form-opcion-menu");
    if (form) form.reset();
    ocultarModal("modal-opcion-menu");
  }


  /* POST /whatsapp/menu (alta) o PUT /{id} (reemplazo). El backend valida la
     tecla única (409) y que TEXTO tenga respuesta (422); el mensaje se muestra
     dentro del modal sin perder lo cargado. */
  async function guardarOpcionMenu(ev) {
    ev.preventDefault();
    const btn = document.getElementById("modal-menu-save");
    const editando = Boolean(state.opcionMenuEditId);
    const tipo = document.getElementById("menu-tipo").value;
    const payload = {
      numero: document.getElementById("menu-numero").value,
      titulo: document.getElementById("menu-titulo").value.trim(),
      tipo,
      respuesta: tipo === "TEXTO"
        ? document.getElementById("menu-respuesta").value.trim()
        : null,
      activa: document.getElementById("menu-activa").checked,
      orden: Number(document.getElementById("menu-orden").value) || 0,
    };
    try {
      bloquear(btn, true);
      const opcion = await api(rutaOpcionMenu(state.opcionMenuEditId), {
        method: editando ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      cerrarModalOpcionMenu();
      toast(
        "exito",
        editando ? "Opción actualizada" : "Opción agregada al menú",
        `${opcion.numero} · ${opcion.titulo}`
      );
      await cargarMenuWhatsApp();
      await cargarVistaPreviaMenu();
    } catch (err) {
      mostrarAviso("menu-error", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* DELETE /whatsapp/menu/{id} da de baja la opción y PATCH {activa:true} la
     reactiva: la fila nunca se borra. */
  async function cambiarEstadoOpcionMenu(id, activa) {
    const opcion = opcionMenuPorId(id);
    if (!opcion) return;
    try {
      if (activa) {
        await api(rutaOpcionMenu(opcion.id), {
          method: "PATCH",
          body: JSON.stringify({ activa: true }),
        });
      } else {
        await api(rutaOpcionMenu(opcion.id), { method: "DELETE" });
      }
      toast(
        "exito",
        `Opción ${activa ? "reactivada" : "desactivada"}`,
        `${opcion.numero} · ${opcion.titulo}`
      );
      await cargarMenuWhatsApp();
      await cargarVistaPreviaMenu();
    } catch (err) {
      toast(
        "error",
        `No se pudo ${activa ? "reactivar" : "desactivar"} la opción`,
        err.message
      );
    }
  }

  /* POST /whatsapp/menu/restaurar: vuelve a las cuatro opciones históricas. */
  async function restaurarMenuWhatsApp() {
    if (
      !window.confirm(
        "Se borra la configuración actual y vuelven las cuatro opciones históricas " +
          "(precio, obras sociales, ubicación y secretaría). ¿Continuar?"
      )
    ) {
      return;
    }
    const btn = document.getElementById("btn-restaurar-menu");
    try {
      bloquear(btn, true);
      await api(`${MENU_WHATSAPP}/restaurar`, { method: "POST" });
      toast("exito", "Menú restaurado", "El bot vuelve a las cuatro opciones históricas.");
      await cargarMenuWhatsApp();
      await cargarVistaPreviaMenu();
    } catch (err) {
      toast("error", "No se pudo restaurar el menú", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* ------------------- Formulario de pacientes (alta/edición) ------------- */

  /* Un único formulario cumple las dos funciones: `state.pacienteEditId` guarda
     el id del paciente en edición (``null`` = alta). */
  /* Si la cobertura del paciente fue dada de baja ya no está en el desplegable:
     se agrega su opción para no borrarla sin querer al guardar otros cambios. */
  function asegurarOpcionObraSocial(id) {
    const sel = document.getElementById("pac-obra-social");
    if (!sel || id === null || id === undefined || id === "") return;
    const valor = String(id);
    const existe = Array.prototype.some.call(sel.options, (o) => o.value === valor);
    if (existe) return;
    const obra = obraSocialPorId(valor);
    const opt = document.createElement("option");
    opt.value = valor;
    opt.textContent = obra ? `${obra.nombre} (dada de baja)` : `Obra social #${valor}`;
    sel.appendChild(opt);
  }

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
      document.getElementById("pac-fecha-nacimiento").value =
        paciente.fecha_nacimiento || "";
      document.getElementById("pac-estado-civil").value = paciente.estado_civil || "";
      document.getElementById("pac-nacionalidad").value = paciente.nacionalidad || "";
      document.getElementById("pac-domicilio").value = paciente.domicilio || "";
      document.getElementById("pac-profesion").value = paciente.profesion || "";
      document.getElementById("pac-titular").value = paciente.titular || "";
      document.getElementById("pac-lugar-trabajo").value = paciente.lugar_trabajo || "";
      document.getElementById("pac-jerarquia").value = paciente.jerarquia || "";
      asegurarOpcionObraSocial(paciente.obra_social_id);
      document.getElementById("pac-obra-social").value = paciente.obra_social_id
        ? String(paciente.obra_social_id)
        : "";
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
      // La cobertura viaja como clave foránea del catálogo; vacío = sin obra social.
      obra_social_id: document.getElementById("pac-obra-social").value
        ? Number(document.getElementById("pac-obra-social").value)
        : null,
      numero_afiliado: document.getElementById("pac-numero-afiliado").value.trim() || null,
      // Filiatorios de la Historia Clínica General: se comparten con la ficha
      // médica (el backend los sincroniza en el mismo registro).
      fecha_nacimiento:
        document.getElementById("pac-fecha-nacimiento").value || null,
      estado_civil: document.getElementById("pac-estado-civil").value.trim() || null,
      nacionalidad: document.getElementById("pac-nacionalidad").value.trim() || null,
      domicilio: document.getElementById("pac-domicilio").value.trim() || null,
      profesion: document.getElementById("pac-profesion").value.trim() || null,
      titular: document.getElementById("pac-titular").value.trim() || null,
      lugar_trabajo:
        document.getElementById("pac-lugar-trabajo").value.trim() || null,
      jerarquia: document.getElementById("pac-jerarquia").value.trim() || null,
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
    // también refleja la ventana de días elegida (el <h1> ya dice
    // «Recordatorios pendientes»).
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

  /* ------------------------- Fichas de salud (Módulo 4) ------------------ */
  // Panel interno de la anamnesis. El backend entrega una fila por turno con
  // el estado de la ficha y el enlace público ya firmado (GET /anamnesis/panel);
  // acá sólo se listan, se abren y se copian los enlaces.

  function diasFichas() {
    return Number(state.fichasDias) || 30;
  }

  function actualizarSubtituloFichas() {
    const filtro = state.fichasSoloPendientes ? " Sólo se muestran las pendientes." : "";
    const texto =
      `Turnos de los próximos ${textoDias(diasFichas())} y estado de la ` +
      `declaración de salud.${filtro}`;
    const sub = document.getElementById("fichas-subtitulo");
    if (sub) sub.textContent = texto;
    if (state.vista === "fichas-salud") {
      document.getElementById("page-subtitle").textContent = texto;
    }
  }

  function actualizarContadoresFichas() {
    const pendientes = state.fichasSalud.filter((f) => !f.completado).length;
    const total = state.fichasSalud.length;
    const badge = document.getElementById("nav-fichas-pendientes");
    if (badge) {
      badge.textContent = String(pendientes);
      badge.classList.toggle("hide", pendientes === 0);
    }
    const contador = document.getElementById("fichas-contador");
    if (contador) {
      contador.textContent = state.fichasSoloPendientes
        ? `${pendientes} pendiente${pendientes === 1 ? "" : "s"}`
        : `${total} turno${total === 1 ? "" : "s"} · ${pendientes} pendiente${pendientes === 1 ? "" : "s"}`;
    }
    actualizarSubtituloFichas();
  }

  function estadoFicha(f) {
    if (f.completado && f.alerta_medica) {
      return `<span class="badge badge-red">Alerta médica</span>`;
    }
    if (f.completado) return `<span class="badge badge-green">Ficha completa</span>`;
    return `<span class="badge badge-amber">Ficha pendiente</span>`;
  }

  function filaFichaSalud(f) {
    const alerta = Boolean(f.completado && f.alerta_medica);
    const motivos =
      alerta && (f.motivos_alerta || []).length
        ? `<p class="fichas-motivos">Revisar antes de atender: ${esc(
            f.motivos_alerta.join(" · ")
          )}</p>`
        : "";
    const enviada = f.completado
      ? f.fecha_completado
        ? `Completada el ${fechaCorta(f.fecha_completado)} a las ${parteHora(f.fecha_completado)}.`
        : "Ficha completada."
      : "Ficha pendiente de carga.";

    return (
      `<div class="turno-row fichas-row${alerta ? " fichas-riesgo" : ""}">` +
        `<div class="turno-hora">` +
          `<p class="h">${esc(parteHora(f.fecha_hora_inicio))}</p>` +
          `<p class="d">${esc(fechaCorta(f.fecha_hora_inicio))}</p>` +
        `</div>` +
        `<div class="min-w-0 flex-1">` +
          `<div class="flex flex-wrap items-center gap-2">` +
            `<p class="font-bold text-slate-800">${esc(f.paciente_nombre)}</p>` +
            estadoFicha(f) +
            `<span class="badge badge-slate">Turno #${esc(f.turno_id)}</span>` +
          `</div>` +
          `<p class="text-xs text-slate-500 mt-0.5">` +
            `${esc(f.doctor_nombre || "Sin profesional")}` +
            (f.paciente_telefono ? ` · ${esc(f.paciente_telefono)}` : "") +
            ` · ${esc(etiquetaDias(f.fecha_hora_inicio))}` +
          `</p>` +
          motivos +
          `<p class="text-[11px] text-slate-400 mt-1">${esc(enviada)}</p>` +
        `</div>` +
        `<div class="fichas-acciones">` +
          `<button type="button" class="btn btn-primary btn-sm" ` +
            `data-ficha-editar="${esc(f.turno_id)}">` +
            `${f.completado ? "Editar ficha" : "Cargar ficha"}` +
          `</button>` +
          `<a class="btn btn-soft btn-sm" href="${esc(f.enlace)}" target="_blank" rel="noopener">` +
            `Abrir ficha` +
          `</a>` +
          `<button type="button" class="btn btn-ghost btn-sm" ` +
            `data-ficha-enviar="${esc(f.turno_id)}" ` +
            `title="Abre WhatsApp Web con el mensaje para el paciente">` +
            `Enviar ficha` +
          `</button>` +
          `<button type="button" class="btn btn-ghost btn-sm" ` +
            `data-ficha-opciones="${esc(f.turno_id)}" ` +
            `title="Copiar el mensaje, ver el enlace o enviarlo por la API">` +
            `Más opciones` +
          `</button>` +
        `</div>` +
      `</div>`
    );
  }

  function renderFichasSalud() {
    const cont = document.getElementById("fichas-lista");
    if (!cont) return;
    actualizarContadoresFichas();

    if (state.fichasError) {
      cont.innerHTML = `<div class="card">${vacio(
        "No se pudieron cargar las fichas de salud",
        state.fichasError
      )}</div>`;
      return;
    }
    if (!state.fichasSalud.length) {
      cont.innerHTML = `<div class="card">${vacio(
        state.fichasSoloPendientes
          ? "No hay fichas pendientes"
          : `No hay turnos en los próximos ${textoDias(diasFichas())}`,
        state.fichasSoloPendientes
          ? "Todos los turnos de la ventana ya tienen su ficha de salud completada."
          : "Cuando agendes turnos, acá vas a poder abrir o copiar el enlace de la ficha de cada paciente."
      )}</div>`;
      return;
    }
    cont.innerHTML = state.fichasSalud.map(filaFichaSalud).join("");
  }

  async function cargarFichasSalud(avisar) {
    try {
      const filtro = state.fichasSoloPendientes ? "&solo_pendientes=true" : "";
      const datos = await api(`/anamnesis/panel?dias=${diasFichas()}${filtro}`);
      state.fichasSalud = datos || [];
      state.fichasError = null;
      if (avisar) {
        toast(
          "info",
          "Fichas de salud actualizadas",
          `${state.fichasSalud.length} turno(s) en los próximos ${textoDias(diasFichas())}.`
        );
      }
    } catch (err) {
      state.fichasError = err.message;
      if (avisar) toast("error", "No se pudieron cargar las fichas de salud", err.message);
    }
    renderFichasSalud();
  }

  async function copiarEnlaceFicha(enlace, paciente) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(enlace);
      } else {
        // Fallback para navegadores viejos o contextos sin Clipboard API.
        const area = document.createElement("textarea");
        area.value = enlace;
        area.setAttribute("readonly", "");
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        document.body.removeChild(area);
      }
      toast(
        "exito",
        "Enlace copiado",
        `${paciente || "Ficha de salud"} · pegalo en WhatsApp para que complete la ficha.`
      );
    } catch (err) {
      toast("error", "No se pudo copiar el enlace", err.message);
    }
  }

  /* Envío manual del enlace de la ficha. El panel ya trae el enlace firmado, el
     texto sugerido y el Click-to-Chat: el token no se puede firmar desde el
     navegador, así que el modal sólo muestra y copia lo que armó el backend.
     Se abre con «Más opciones» (o cuando el paciente no tiene WhatsApp) y
     "Enviar por API" es el único botón que manda el mensaje solo. */
  function fichaEnvioActual() {
    return state.fichasSalud.find(
      (f) => String(f.turno_id) === String(state.fichaEnvioTurnoId)
    );
  }

  function abrirModalEnviarFicha(turnoId) {
    const ficha = state.fichasSalud.find(
      (f) => String(f.turno_id) === String(turnoId)
    );
    if (!ficha) {
      toast("error", "No se encontró la ficha", "Actualizá el panel e intentá de nuevo.");
      return;
    }

    state.fichaEnvioTurnoId = ficha.turno_id;
    document.getElementById("enviar-ficha-titulo").textContent = ficha.completado
      ? "Reenviar ficha de salud"
      : "Enviar ficha de salud";
    document.getElementById("enviar-ficha-subtitulo").textContent =
      `${ficha.paciente_nombre} · ${fechaCorta(ficha.fecha_hora_inicio)} ` +
      `${parteHora(ficha.fecha_hora_inicio)} h · ` +
      `${ficha.doctor_nombre || "Sin profesional"}`;

    const destino = document.getElementById("enviar-ficha-destino");
    destino.textContent = ficha.telefono_whatsapp
      ? `Destino: ${ficha.paciente_telefono} · el paciente completa la ficha desde el enlace.`
      : "El paciente no tiene un teléfono válido para WhatsApp: copiá el mensaje y mandalo por otro medio.";

    document.getElementById("enviar-ficha-mensaje").value =
      ficha.mensaje || ficha.enlace || "";

    // El enlace se muestra como <a> (azul y clickeable), no como texto readonly:
    // el paciente lo recibe igual y la secretaría puede probarlo antes de mandarlo.
    const enlaceFicha = document.getElementById("enviar-ficha-enlace");
    enlaceFicha.textContent = ficha.enlace || "El servidor no devolvió el enlace";
    if (ficha.enlace) {
      enlaceFicha.href = ficha.enlace;
    } else {
      enlaceFicha.removeAttribute("href");
    }
    // WhatsApp no convierte en enlace una URL sin dominio (localhost, 127.0.0.1):
    // el paciente la ve gris y el toque no hace nada. Además ese host es su propio
    // celular, así que el enlace no abriría el formulario igual.
    const enlaceLocal =
      /^(?:https?:\/\/)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::|\/|$)/i;
    document
      .getElementById("enviar-ficha-local")
      .classList.toggle("hide", !enlaceLocal.test(ficha.enlace || ""));

    const wa = document.getElementById("enviar-ficha-wa");
    if (ficha.enlace_whatsapp) {
      wa.href = ficha.enlace_whatsapp;
      wa.classList.remove("hide");
    } else {
      wa.removeAttribute("href");
      wa.classList.add("hide");
    }
    document
      .getElementById("enviar-ficha-api")
      .classList.toggle("hide", !ficha.telefono_whatsapp);
    document
      .getElementById("enviar-ficha-sin-wa")
      .classList.toggle("hide", Boolean(ficha.enlace_whatsapp));

    ocultarAviso("enviar-ficha-error");
    mostrarModal("modal-enviar-ficha");
  }

  function cerrarModalEnviarFicha() {
    state.fichaEnvioTurnoId = null;
    ocultarAviso("enviar-ficha-error");
    ocultarModal("modal-enviar-ficha");
  }

  async function copiarMensajeFicha() {
    const ficha = fichaEnvioActual();
    if (!ficha) return;
    // Se copia el mensaje completo (ya trae el enlace): es lo que se pega en WhatsApp.
    await copiarEnlaceFicha(ficha.mensaje || ficha.enlace, ficha.paciente_nombre);
  }

  /* POST /anamnesis/panel/{turno_id}/enviar con ``canal=api``: la Cloud API
     manda el mensaje sola. Si el servidor no tiene credenciales responde
     ``simulado = true`` y no se envió nada, así que se avisa para copiar a mano.
     Enviar la ficha nunca marca el recordatorio del turno como enviado. */
  async function enviarFichaPorApi() {
    const ficha = fichaEnvioActual();
    if (!ficha) return;
    const btn = document.getElementById("enviar-ficha-api");
    ocultarAviso("enviar-ficha-error");
    bloquear(btn, true);
    try {
      const r = await api(`/anamnesis/panel/${ficha.turno_id}/enviar`, {
        method: "POST",
        body: JSON.stringify({ canal: "api" }),
      });
      if (!r.enviado) {
        mostrarAviso(
          "enviar-ficha-error",
          "El servidor no pudo enviar el mensaje. Copiá el texto o usá el botón de WhatsApp."
        );
        return;
      }
      if (r.simulado) {
        mostrarAviso(
          "enviar-ficha-error",
          "Envío simulado: el servidor no tiene credenciales de WhatsApp. Copiá el mensaje o abrí WhatsApp para mandarlo."
        );
        return;
      }
      cerrarModalEnviarFicha();
      toast(
        "exito",
        "Ficha enviada",
        `${r.paciente_nombre} · el enlace salió por WhatsApp (${r.telefono_whatsapp || "destino configurado"}).`
      );
    } catch (err) {
      mostrarAviso("enviar-ficha-error", err.message);
    } finally {
      bloquear(btn, false);
    }
  }

  /* "Enviar ficha" del listado: la secretaría manda el enlace desde su propio
     WhatsApp. El clic abre WhatsApp Web con el número del paciente y el mensaje
     ya escrito (los dos los arma el backend, que es el único que firma el token);
     ella revisa y envía, y lo confirma en la conversación. La ficha sigue
     pendiente hasta que el paciente complete el formulario. Si el paciente no
     tiene un teléfono válido se abre el modal para copiar el texto y mandarlo por
     otro medio. */
  function abrirWhatsAppFicha(turnoId) {
    const ficha = state.fichasSalud.find(
      (f) => String(f.turno_id) === String(turnoId)
    );
    if (!ficha) {
      toast("error", "No se encontró la ficha", "Actualizá el panel e intentá de nuevo.");
      return;
    }
    if (!ficha.enlace_whatsapp) {
      abrirModalEnviarFicha(ficha.turno_id);
      return;
    }

    // Un <a> real evita el bloqueo de pestañas emergentes que sufren los
    // window.open (y con "noopener" devuelven null, así que no se puede chequear).
    const enlace = document.createElement("a");
    enlace.href = ficha.enlace_whatsapp;
    enlace.target = "_blank";
    enlace.rel = "noopener";
    document.body.appendChild(enlace);
    enlace.click();
    enlace.remove();

    toast(
      "exito",
      "WhatsApp abierto",
      `${ficha.paciente_nombre} · enviá el mensaje desde WhatsApp; la ficha queda pendiente hasta que el paciente la complete.`
    );
  }

  /* Imprime la Historia Clínica General. El backend arma el PDF sobre la misma
     plantilla de papel del consultorio (dos páginas + anexo si hace falta), así
     lo que sale de la impresora se archiva igual que una ficha completada a
     mano. Se abre con un <a> real para no chocar con el bloqueo de pestañas. */
  function abrirPdfHistoria(pacienteId) {
    if (!pacienteId) return;
    const enlace = document.createElement("a");
    enlace.href =
      `${API}/pacientes/${encodeURIComponent(pacienteId)}/historia-clinica/pdf`;
    enlace.target = "_blank";
    enlace.rel = "noopener";
    document.body.appendChild(enlace);
    enlace.click();
    enlace.remove();
  }

  /* La secretaría carga la misma declaración del enlace público cuando el
     paciente está en el mostrador o responde por teléfono. El formulario se
     precarga con ``antecedentes_salud`` de la fila y se guarda con el PUT
     interno por turno. */
  function abrirModalFicha(turnoId) {
    const ficha = state.fichasSalud.find(
      (f) => String(f.turno_id) === String(turnoId)
    );
    if (!ficha) {
      toast("error", "No se encontró la ficha", "Actualizá el panel e intentá de nuevo.");
      return;
    }

    state.fichaEditTurnoId = ficha.turno_id;
    /* Los filiatorios viven en el paciente y llegan aparte: se fusionan con la
       declaración para que el modal se abra completo aunque la ficha esté
       pendiente (el backend los devuelve en ``datos_filiatorios``). */
    const a = Object.assign(
      {},
      ficha.datos_filiatorios || {},
      ficha.antecedentes_salud || {}
    );

    document.getElementById("ficha-titulo").textContent = ficha.completado
      ? "Editar ficha de salud"
      : "Cargar ficha de salud";
    document.getElementById("ficha-subtitulo").textContent =
      `${ficha.paciente_nombre} · ${fechaCorta(ficha.fecha_hora_inicio)} ` +
      `${parteHora(ficha.fecha_hora_inicio)} h · ` +
      `${ficha.doctor_nombre || "Sin profesional"}`;
    document.getElementById("modal-ficha-save").textContent = ficha.completado
      ? "Guardar cambios"
      : "Guardar ficha";

    const estado = document.getElementById("ficha-estado");
    if (ficha.completado) {
      const cuando = ficha.fecha_completado
        ? ` el ${fechaCorta(ficha.fecha_completado)} a las ${parteHora(ficha.fecha_completado)}`
        : "";
      const alerta =
        ficha.alerta_medica && (ficha.motivos_alerta || []).length
          ? ` Alerta médica: ${ficha.motivos_alerta.join(" · ")}.`
          : " Sin antecedentes de riesgo.";
      estado.textContent = `Ficha completada${cuando}.${alerta}`;
    } else {
      estado.textContent =
        "Ficha pendiente: completá las respuestas que da el paciente y confirmá el consentimiento.";
    }
    estado.classList.remove("hide");

    // El cuestionario completo lo genera el catálogo compartido: el panel carga
    // exactamente las mismas preguntas que el enlace público, más el examen
    // bucal y el diagnóstico que completa el profesional.
    const contenedor = document.getElementById("ficha-campos");
    if (contenedor && window.FICHA_MEDICA) {
      FICHA_MEDICA.renderFormulario(contenedor, a);
    }
    document.getElementById("ficha-consentimiento").checked = false;
    ocultarAviso("ficha-error");
    mostrarModal("modal-ficha-salud");
  }

  /* El PUT de la ficha sincroniza los datos filiatorios en el paciente, así que
     después de guardarla se refresca la lista: la tabla y el formulario de
     edición muestran lo último sin recargar la página. */
  async function refrescarPacientes() {
    try {
      state.pacientes = (await api("/pacientes")) || [];
      renderPacientes();
    } catch (err) {
      /* Es un refresco de cortesía: la ficha ya quedó guardada. */
    }
  }

  function cerrarModalFicha() {
    state.fichaEditTurnoId = null;
    const form = document.getElementById("form-ficha-salud");
    if (form) form.reset();
    ocultarAviso("ficha-error");
    document.getElementById("ficha-estado").classList.add("hide");
    ocultarModal("modal-ficha-salud");
  }

  function payloadFicha() {
    const contenedor = document.getElementById("ficha-campos");
    if (!contenedor || !window.FICHA_MEDICA) return {};
    return FICHA_MEDICA.leerFormulario(contenedor);
  }

  async function guardarFichaSalud(ev) {
    ev.preventDefault();
    const turnoId = state.fichaEditTurnoId;
    if (!turnoId) return;

    if (!document.getElementById("ficha-consentimiento").checked) {
      mostrarAviso(
        "ficha-error",
        "Para guardar la ficha, el paciente tiene que confirmar que los datos son correctos."
      );
      return;
    }

    const ficha = state.fichasSalud.find(
      (f) => String(f.turno_id) === String(turnoId)
    );
    const paciente = ficha ? ficha.paciente_nombre : `Turno #${turnoId}`;
    const btn = document.getElementById("modal-ficha-save");
    ocultarAviso("ficha-error");
    bloquear(btn, true);

    try {
      const datos = await api(`/anamnesis/panel/${turnoId}`, {
        method: "PUT",
        body: JSON.stringify(payloadFicha()),
      });
      cerrarModalFicha();
      const alerta = Boolean(datos && datos.alerta_medica);
      const motivos =
        datos && Array.isArray(datos.motivos_alerta)
          ? datos.motivos_alerta.join(" · ")
          : "";
      toast(
        alerta ? "info" : "exito",
        "Ficha de salud guardada",
        alerta
          ? `${paciente} · alerta médica: ${motivos}`
          : `${paciente} · ficha sin antecedentes de riesgo.`
      );
      await cargarFichasSalud();
      // El PUT sincroniza los datos filiatorios en el paciente: la tabla y el
      // formulario de pacientes tienen que mostrar lo último.
      await refrescarPacientes();
    } catch (err) {
      mostrarAviso("ficha-error", err.message);
    } finally {
      bloquear(btn, false);
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

    /* Módulo 2 · Historia clínica: buscador del Panel Principal y delegación de
       clics (las fichas de resultado y el botón "Historia" de la tabla de
       pacientes se redibujan en cada carga). Sólo lectura: no hay alta acá. */
    const buscadorHistoria = document.getElementById("hist-panel-buscar");
    if (buscadorHistoria) {
      buscadorHistoria.addEventListener("input", function () {
        programarBusquedaHistoria(this.value);
      });
    }
    document.addEventListener("click", function (e) {
      if (e.target.closest("[data-cerrar-alerta]")) {
        ocultarAlertaPanel();
        return;
      }
      const editarFicha = e.target.closest("[data-ficha-editar]");
      if (editarFicha) {
        abrirModalFicha(editarFicha.dataset.fichaEditar);
        return;
      }
      const copiarFicha = e.target.closest("[data-ficha-copiar]");
      if (copiarFicha) {
        copiarEnlaceFicha(
          copiarFicha.dataset.fichaCopiar,
          copiarFicha.dataset.fichaPaciente
        );
        return;
      }
      const enviarFicha = e.target.closest("[data-ficha-enviar]");
      if (enviarFicha) {
        abrirWhatsAppFicha(enviarFicha.dataset.fichaEnviar);
        return;
      }
      const imprimirFicha = e.target.closest("[data-ficha-pdf]");
      if (imprimirFicha) {
        abrirPdfHistoria(imprimirFicha.dataset.fichaPdf);
        return;
      }
      const opcionesFicha = e.target.closest("[data-ficha-opciones]");
      if (opcionesFicha) {
        abrirModalEnviarFicha(opcionesFicha.dataset.fichaOpciones);
        return;
      }
      const ficha = e.target.closest("[data-hist-paciente]");
      if (ficha) {
        abrirHistoriaPanel(ficha.dataset.histPaciente);
        return;
      }
      const boton = e.target.closest("[data-historia]");
      if (boton) {
        // Desde la tabla de pacientes: se abre el Panel Principal con la ficha.
        cambiarVista("dashboard");
        abrirHistoriaPanel(boton.dataset.historia);
      }
    });

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
      if (e.key !== "Escape") return;
      cerrarModal();
      cerrarModalDoctor();
      cerrarModalObraSocial();
      cerrarModalCobro();
      cerrarModalPrestacion();
      cerrarModalArancel();
      cerrarDebito();
      cerrarModalOpcionMenu();
      cerrarModalFicha();
      cerrarModalEnviarFicha();
    });

    document.getElementById("form-doctor").addEventListener("submit", crearDoctor);
    // Gestión de doctores: edición en modal (PUT) y baja/reactivación lógica.
    document.getElementById("form-doctor-edicion").addEventListener("submit", guardarEdicionDoctor);
    // Forma de pago: el select habilita el campo numérico (porcentaje o sueldo).
    document.getElementById("doc-tipo-pago").addEventListener("change", function (e) {
      configurarCamposPago(e.target.value, IDS_PAGO_ALTA, null);
    });
    document.getElementById("doc-edit-tipo-pago").addEventListener("change", function (e) {
      configurarCamposPago(e.target.value, IDS_PAGO_EDIT, null);
    });
    configurarCamposPago("DUENO", IDS_PAGO_ALTA, null);
    document.getElementById("modal-doctor-close").addEventListener("click", cerrarModalDoctor);
    document.getElementById("modal-doctor-cancel").addEventListener("click", cerrarModalDoctor);
    document.getElementById("modal-doctor").addEventListener("click", function (e) {
      if (e.target.id === "modal-doctor") cerrarModalDoctor();
    });

    // Catálogo de obras sociales: alta/edición en modal y filtro local.
    document.getElementById("btn-nueva-obra-social").addEventListener("click", function () {
      abrirModalObraSocial(null);
    });
    document.getElementById("form-obra-social").addEventListener("submit", guardarObraSocial);
    document.getElementById("modal-os-close").addEventListener("click", cerrarModalObraSocial);
    document.getElementById("modal-os-cancel").addEventListener("click", cerrarModalObraSocial);
    document.getElementById("modal-obra-social").addEventListener("click", function (e) {
      if (e.target.id === "modal-obra-social") cerrarModalObraSocial();
    });
    const chkInactivas = document.getElementById("os-inactivas");
    if (chkInactivas) {
      chkInactivas.checked = state.osMostrarInactivas;
      chkInactivas.addEventListener("change", function (e) {
        state.osMostrarInactivas = e.target.checked;
        renderObrasSociales();
      });
    }

    // Menú del Bot WhatsApp: alta/edición en modal, baja lógica, restauración
    // del menú histórico y vista previa del mensaje que recibe el paciente.
    document.getElementById("btn-nueva-opcion-menu").addEventListener("click", function () {
      abrirModalOpcionMenu(null);
    });
    document.getElementById("form-opcion-menu").addEventListener("submit", guardarOpcionMenu);
    document.getElementById("menu-tipo").addEventListener("change", sincronizarTipoMenu);
    document.getElementById("modal-menu-close").addEventListener("click", cerrarModalOpcionMenu);
    document.getElementById("modal-menu-cancel").addEventListener("click", cerrarModalOpcionMenu);
    document.getElementById("modal-opcion-menu").addEventListener("click", function (e) {
      if (e.target.id === "modal-opcion-menu") cerrarModalOpcionMenu();
    });
    document.getElementById("btn-restaurar-menu").addEventListener("click", restaurarMenuWhatsApp);
    document.getElementById("btn-vista-previa-menu").addEventListener("click", cargarVistaPreviaMenu);
    const chkMenuInactivas = document.getElementById("menu-wa-inactivas");
    if (chkMenuInactivas) {
      chkMenuInactivas.checked = state.menuMostrarInactivas;
      chkMenuInactivas.addEventListener("change", function (e) {
        state.menuMostrarInactivas = e.target.checked;
        renderMenuWhatsApp();
      });
    }
    // Delegación: los botones de cada fila se redibujan en cada carga.
    document.addEventListener("click", function (e) {
      const editar = e.target.closest("[data-editar-menu]");
      if (editar) {
        abrirModalOpcionMenu(editar.dataset.editarMenu);
        return;
      }
      const baja = e.target.closest("[data-baja-menu]");
      if (baja) {
        cambiarEstadoOpcionMenu(baja.dataset.bajaMenu, false);
        return;
      }
      const reactivar = e.target.closest("[data-reactivar-menu]");
      if (reactivar) cambiarEstadoOpcionMenu(reactivar.dataset.reactivarMenu, true);
    });

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

    // Fichas de salud: ventana de días, filtro de pendientes y refresco manual.
    const fichasDias = document.getElementById("fichas-dias");
    if (fichasDias) {
      fichasDias.value = String(diasFichas());
      fichasDias.addEventListener("change", function (e) {
        state.fichasDias = parseInt(e.target.value, 10) || 30;
        cargarFichasSalud(true);
      });
    }
    const fichasSolo = document.getElementById("fichas-solo-pendientes");
    if (fichasSolo) {
      fichasSolo.checked = state.fichasSoloPendientes;
      fichasSolo.addEventListener("change", function (e) {
        state.fichasSoloPendientes = Boolean(e.target.checked);
        cargarFichasSalud();
      });
    }
    const fichasActualizar = document.getElementById("fichas-actualizar");
    if (fichasActualizar) {
      fichasActualizar.addEventListener("click", function () {
        cargarFichasSalud(true);
      });
    }

    // Carga interna de la ficha: la secretaría la completa por el paciente.
    document
      .getElementById("form-ficha-salud")
      .addEventListener("submit", guardarFichaSalud);
    document
      .getElementById("modal-ficha-close")
      .addEventListener("click", cerrarModalFicha);
    document
      .getElementById("modal-ficha-cancel")
      .addEventListener("click", cerrarModalFicha);
    // Imprime la Historia Clínica General del turno que se está cargando.
    const imprimirFichaModal = document.getElementById("modal-ficha-print");
    if (imprimirFichaModal) {
      imprimirFichaModal.addEventListener("click", function () {
        const ficha = state.fichasSalud.find(
          (f) => String(f.turno_id) === String(state.fichaEditTurnoId)
        );
        if (ficha) abrirPdfHistoria(ficha.paciente_id);
      });
    }
    document
      .getElementById("modal-ficha-salud")
      .addEventListener("click", function (e) {
        if (e.target.id === "modal-ficha-salud") cerrarModalFicha();
      });

    // Envío del enlace de la ficha: copiar el mensaje listo o abrirlo en
    // WhatsApp; el botón de la API es el único que manda el mensaje solo.
    document
      .getElementById("modal-enviar-ficha-close")
      .addEventListener("click", cerrarModalEnviarFicha);
    document
      .getElementById("modal-enviar-ficha-cancel")
      .addEventListener("click", cerrarModalEnviarFicha);
    document
      .getElementById("enviar-ficha-copiar")
      .addEventListener("click", copiarMensajeFicha);
    document
      .getElementById("enviar-ficha-api")
      .addEventListener("click", enviarFichaPorApi);
    document
      .getElementById("modal-enviar-ficha")
      .addEventListener("click", function (e) {
        if (e.target.id === "modal-enviar-ficha") cerrarModalEnviarFicha();
      });

    // Disponibilidad: selector de odontólogo, alta y baja de rangos, plantilla
    // sugerida, vaciado de la semana y vista previa de bloques de un día.
    const selDisp = document.getElementById("disp-doctor");
    if (selDisp) {
      selDisp.addEventListener("change", function (e) {
        state.dispDoctor = e.target.value;
        renderDisponibilidad();
        // Los tramos personalizados son por odontólogo: se redibuja la tarjeta.
        renderExcepciones();
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

    // Días personalizados (excepciones por fecha): fecha, alta y baja de tramos,
    // copiar la agenda semanal, bloquear el día completo y volver a la semana.
    const fechaExcep = document.getElementById("excep-fecha");
    if (fechaExcep) {
      if (!fechaExcep.value) fechaExcep.value = hoyISO();
      fechaExcep.addEventListener("change", renderExcepciones);
    }
    const btnExcepVer = document.getElementById("excep-ver");
    if (btnExcepVer) btnExcepVer.addEventListener("click", verDiaPersonalizado);
    const formExcep = document.getElementById("excep-form");
    if (formExcep) formExcep.addEventListener("submit", agregarExcepcion);
    const listaExcep = document.getElementById("excep-lista");
    if (listaExcep) {
      listaExcep.addEventListener("click", function (e) {
        const btn = e.target.closest("[data-borrar-excepcion]");
        if (!btn) return;
        // El chip guarda el id REAL de la excepción (el que devolvió la API en el
        // alta), junto con el odontólogo y la fecha a los que pertenece.
        borrarExcepcion(
          btn.dataset.excepcionDoctor,
          btn.dataset.borrarExcepcion,
          btn.dataset.excepcionFecha,
          btn
        );
      });
    }
    const btnExcepCopiar = document.getElementById("excep-copiar");
    if (btnExcepCopiar) btnExcepCopiar.addEventListener("click", copiarAgendaSemanal);
    const btnExcepBloquear = document.getElementById("excep-bloquear");
    if (btnExcepBloquear) btnExcepBloquear.addEventListener("click", bloquearDiaCompleto);
    const btnExcepVolver = document.getElementById("excep-volver");
    if (btnExcepVolver) btnExcepVolver.addEventListener("click", volverAAgendaSemanal);

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

    // Delegación: acciones de la tabla de doctores (las filas se redibujan en
    // cada carga, así que se escucha en el documento y no por botón).
    document.addEventListener("click", function (e) {
      const editar = e.target.closest("[data-editar-doctor]");
      if (editar) {
        abrirModalDoctor(editar.dataset.editarDoctor);
        return;
      }
      const baja = e.target.closest("[data-baja-doctor]");
      if (baja) {
        cambiarEstadoDoctor(baja.dataset.bajaDoctor, false);
        return;
      }
      const reactivar = e.target.closest("[data-reactivar-doctor]");
      if (reactivar) cambiarEstadoDoctor(reactivar.dataset.reactivarDoctor, true);
    });

    // Delegación: acciones del catálogo de obras sociales.
    document.addEventListener("click", function (e) {
      const editar = e.target.closest("[data-editar-os]");
      if (editar) {
        abrirModalObraSocial(editar.dataset.editarOs);
        return;
      }
      const baja = e.target.closest("[data-baja-os]");
      if (baja) {
        cambiarEstadoObraSocial(baja.dataset.bajaOs, false);
        return;
      }
      const reactivar = e.target.closest("[data-reactivar-os]");
      if (reactivar) cambiarEstadoObraSocial(reactivar.dataset.reactivarOs, true);
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

    /* Delegación: botón "Cobrar" de la tarjeta de un turno (agenda del día y
       tarjetas del calendario). Abre el formulario de cierre de la visita. */
    document.addEventListener("click", function (e) {
      const btn = e.target.closest("[data-cobrar-turno]");
      if (!btn) return;
      abrirModalCobro(btn.dataset.cobrarTurno);
    });

    /* --- Vista "Facturación y Caja" (Módulo 3) --------------------------- */
    const btnFactRefrescar = document.getElementById("fact-refrescar");
    if (btnFactRefrescar) btnFactRefrescar.addEventListener("click", cargarFacturacion);

    const btnFactCobrar = document.getElementById("fact-cobrar-visita");
    if (btnFactCobrar) {
      btnFactCobrar.addEventListener("click", function () {
        abrirModalCobro(null);
      });
    }

    document.querySelectorAll("[data-fact-tab]").forEach((b) =>
      b.addEventListener("click", function () {
        cambiarTabFacturacion(b.dataset.factTab);
      })
    );

    // Caja del día: el selector de fecha recarga la caja.
    const factFecha = document.getElementById("fact-fecha");
    if (factFecha) {
      factFecha.addEventListener("change", function () {
        state.factFecha = factFecha.value || hoyISO();
        cargarCajaDelDia();
      });
    }

    // Liquidación de obras sociales.
    const lisOs = document.getElementById("fact-lis-os");
    if (lisOs) {
      lisOs.addEventListener("change", function () {
        state.factArrObraSocial = lisOs.value;
        const otro = document.getElementById("fact-arr-os");
        if (otro) otro.value = lisOs.value;
        cargarPresentacion();
      });
    }
    const lisMes = document.getElementById("fact-lis-mes");
    if (lisMes) lisMes.addEventListener("change", cargarPresentacion);
    const lisVer = document.getElementById("fact-lis-ver");
    if (lisVer) lisVer.addEventListener("click", cargarPresentacion);
    const lisGenerar = document.getElementById("fact-lis-generar");
    if (lisGenerar) lisGenerar.addEventListener("click", generarPresentacion);
    const lisExportar = document.getElementById("fact-lis-exportar");
    if (lisExportar) lisExportar.addEventListener("click", exportarPresentacion);
    const lisPdf = document.getElementById("fact-lis-pdf");
    if (lisPdf) lisPdf.addEventListener("click", exportarPresentacionPDF);
    const lisCobrarTildadas = document.getElementById("fact-lis-cobrar-tildadas");
    if (lisCobrarTildadas) {
      lisCobrarTildadas.addEventListener("click", function () {
        aplicarTildes("cobrar");
      });
    }
    const lisDebitarTildadas = document.getElementById("fact-lis-debitar-tildadas");
    if (lisDebitarTildadas) {
      lisDebitarTildadas.addEventListener("click", function () {
        aplicarTildes("debitar");
      });
    }
    const lisTodos = document.getElementById("fact-lis-todos");
    if (lisTodos) {
      lisTodos.addEventListener("change", function () {
        const p = state.factPresentacion;
        state.factTildadas =
          lisTodos.checked && p
            ? p.movimientos
                .filter(
                  (m) =>
                    m.estado_pago === "PENDIENTE" ||
                    m.estado_pago === "EN_PROCESO_LIQUIDACION"
                )
                .map((m) => m.id)
            : [];
        renderPresentacion();
      });
    }
    document.addEventListener("change", function (e) {
      const chk = e.target.closest("[data-fact-tilde]");
      if (chk) alternarTilde(chk.dataset.factTilde);
    });

    // Liquidación de médicos.
    const medVer = document.getElementById("fact-med-ver");
    if (medVer) medVer.addEventListener("click", cargarLiquidacionMedicos);
    const medMes = document.getElementById("fact-med-mes");
    if (medMes) medMes.addEventListener("change", cargarLiquidacionMedicos);
    const medDoctor = document.getElementById("fact-med-doctor");
    if (medDoctor) medDoctor.addEventListener("change", cargarLiquidacionMedicos);
    const medExportar = document.getElementById("fact-med-exportar");
    if (medExportar) medExportar.addEventListener("click", exportarLiquidacionMedicos);
    const medPdf = document.getElementById("fact-med-pdf");
    if (medPdf) medPdf.addEventListener("click", exportarLiquidacionMedicosPDF);

    // Catálogo de prestaciones y aranceles.
    const btnNuevaPrest = document.getElementById("fact-nueva-prestacion");
    if (btnNuevaPrest) {
      btnNuevaPrest.addEventListener("click", function () {
        abrirModalPrestacion(null);
      });
    }
    const btnImportarPrestPdf = document.getElementById("fact-importar-prestaciones-pdf");
    if (btnImportarPrestPdf) {
      btnImportarPrestPdf.addEventListener("click", abrirImportadorPrestacionesPdf);
    }
    const inputPrestPdf = document.getElementById("fact-prest-pdf");
    if (inputPrestPdf) {
      inputPrestPdf.addEventListener("change", function (e) {
        const archivos = e.target.files;
        if (archivos && archivos.length) elegirPdfPrestaciones(archivos[0]);
      });
    }
    const btnConfirmarPrestPdf = document.getElementById("fact-prest-pdf-confirmar");
    if (btnConfirmarPrestPdf) {
      btnConfirmarPrestPdf.addEventListener("click", function () {
        importarPrestacionesPdf(false);
      });
    }
    const btnCancelarPrestPdf = document.getElementById("fact-prest-pdf-cancelar");
    if (btnCancelarPrestPdf) {
      btnCancelarPrestPdf.addEventListener("click", cerrarImportacionPrestacionesPdf);
    }
    const chkPrestInactivas = document.getElementById("fact-prest-inactivas");
    if (chkPrestInactivas) {
      chkPrestInactivas.checked = state.factMostrarInactivas;
      chkPrestInactivas.addEventListener("change", function (e) {
        state.factMostrarInactivas = e.target.checked;
        renderPrestaciones();
      });
    }
    const arrOs = document.getElementById("fact-arr-os");
    if (arrOs) {
      arrOs.addEventListener("change", function () {
        state.factArrObraSocial = arrOs.value;
        const otro = document.getElementById("fact-lis-os");
        if (otro) otro.value = arrOs.value;
        cargarAranceles();
      });
    }
    const btnNuevoArancel = document.getElementById("fact-nuevo-arancel");
    if (btnNuevoArancel) {
      btnNuevoArancel.addEventListener("click", function () {
        abrirModalArancel(null);
      });
    }
    ["fact-arr-cubre", "fact-arr-coseguro"].forEach((id) => {
      const campo = document.getElementById(id);
      if (campo) campo.addEventListener("input", renderTotalArancel);
    });

    // Convenios: grilla de precios por cobertura e importador de planillas.
    const convOs = document.getElementById("conv-os");
    if (convOs) {
      convOs.addEventListener("change", function () {
        state.factArrObraSocial = convOs.value;
        ["fact-lis-os", "fact-arr-os"].forEach((id) => {
          const otro = document.getElementById(id);
          if (otro) otro.value = convOs.value;
        });
        limpiarImportacionConvenio(false);
        cargarConvenio();
      });
    }
    const convBuscar = document.getElementById("conv-buscar");
    if (convBuscar) {
      convBuscar.addEventListener("input", function (e) {
        state.convBusqueda = e.target.value;
        renderConvenio();
      });
    }
    const convGuardar = document.getElementById("conv-guardar");
    if (convGuardar) convGuardar.addEventListener("click", guardarConvenio);
    const convPlantilla = document.getElementById("conv-plantilla");
    if (convPlantilla) {
      convPlantilla.addEventListener("click", descargarPlantillaConvenio);
    }
    const convConfirmar = document.getElementById("conv-confirmar");
    if (convConfirmar) {
      convConfirmar.addEventListener("click", confirmarImportacion);
    }
    const convLimpiar = document.getElementById("conv-limpiar");
    if (convLimpiar) {
      convLimpiar.addEventListener("click", function () {
        limpiarImportacionConvenio(false);
      });
    }

    // Recuadro de arrastrar y soltar (con clic para elegir el archivo a mano).
    const convZona = document.getElementById("conv-drop");
    const convArchivo = document.getElementById("conv-archivo");
    if (convZona && convArchivo) {
      convZona.addEventListener("click", function () {
        convArchivo.click();
      });
      ["dragenter", "dragover"].forEach((evento) => {
        convZona.addEventListener(evento, function (e) {
          e.preventDefault();
          convZona.classList.add("activo");
        });
      });
      ["dragleave", "dragend", "drop"].forEach((evento) => {
        convZona.addEventListener(evento, function (e) {
          e.preventDefault();
          convZona.classList.remove("activo");
        });
      });
      convZona.addEventListener("drop", function (e) {
        const archivos = e.dataTransfer ? e.dataTransfer.files : null;
        if (archivos && archivos.length) elegirArchivoConvenio(archivos[0]);
      });
    }
    if (convArchivo) {
      convArchivo.addEventListener("change", function (e) {
        const archivos = e.target.files;
        if (archivos && archivos.length) elegirArchivoConvenio(archivos[0]);
      });
    }

    // Formularios y selector del modal de cobro.
    const formCobro = document.getElementById("form-cobro");
    if (formCobro) formCobro.addEventListener("submit", registrarCobro);
    // Buscador rápido del nomenclador dentro del formulario de cobro.
    const cobroBuscar = document.getElementById("fact-cobro-buscar");
    if (cobroBuscar) {
      cobroBuscar.addEventListener("input", function (e) {
        state.cobroBusqueda = e.target.value;
        renderCobroPrestaciones();
      });
    }
    const formPrest = document.getElementById("form-prestacion");
    if (formPrest) formPrest.addEventListener("submit", guardarPrestacion);
    const formArancel = document.getElementById("form-arancel");
    if (formArancel) formArancel.addEventListener("submit", guardarArancel);
    const formDebito = document.getElementById("form-debito");
    if (formDebito) formDebito.addEventListener("submit", confirmarDebito);

    const selCobroTurno = document.getElementById("fact-cobro-turno");
    if (selCobroTurno) {
      selCobroTurno.addEventListener("change", function () {
        cargarTurnoParaCobro(selCobroTurno.value).then(function (listo) {
          if (!listo) abrirModalCobro(null);
        });
      });
    }

    // Cierres de los modales del módulo (botón, cancelar y clic en el fondo).
    [
      ["modal-cobro-close", cerrarModalCobro],
      ["modal-cobro-cancel", cerrarModalCobro],
      ["modal-prest-close", cerrarModalPrestacion],
      ["modal-prest-cancel", cerrarModalPrestacion],
      ["modal-arr-close", cerrarModalArancel],
      ["modal-arr-cancel", cerrarModalArancel],
      ["modal-deb-close", cerrarDebito],
      ["modal-deb-cancel", cerrarDebito],
    ].forEach(function (par) {
      const boton = document.getElementById(par[0]);
      if (boton) boton.addEventListener("click", par[1]);
    });
    [
      ["modal-cobro", cerrarModalCobro],
      ["modal-prestacion", cerrarModalPrestacion],
      ["modal-arancel", cerrarModalArancel],
      ["modal-debito", cerrarDebito],
    ].forEach(function (par) {
      const fondo = document.getElementById(par[0]);
      if (fondo) {
        fondo.addEventListener("click", function (e) {
          if (e.target.id === par[0]) par[1]();
        });
      }
    });

    // Delegación: tildes y cantidades del formulario de cobro.
    document.addEventListener("change", function (e) {
      const chk = e.target.closest("[data-cobro-tilde]");
      if (chk) alternarSeleccionCobro(chk.dataset.cobroTilde, chk.checked);
    });
    document.addEventListener("input", function (e) {
      // Monto que cubre la OS en la grilla de convenios: recalcula el coseguro.
      const monto = e.target.closest("[data-conv-monto]");
      if (monto) {
        fijarMontoConvenio(monto.dataset.convMonto, monto.value);
        return;
      }
      const campo = e.target.closest("[data-cobro-cantidad]");
      if (campo) fijarCantidadCobro(campo.dataset.cobroCantidad, campo.value);
    });

    // Delegación: acciones de caja, presentación, prestaciones y aranceles.
    document.addEventListener("click", function (e) {
      const cobrar = e.target.closest("[data-fact-cobrar]");
      if (cobrar) {
        cobrarMovimiento(cobrar.dataset.factCobrar);
        return;
      }
      const debitar = e.target.closest("[data-fact-debitar]");
      if (debitar) {
        abrirDebito(debitar.dataset.factDebitar);
        return;
      }
      const editarPrest = e.target.closest("[data-fact-editar-prest]");
      if (editarPrest) {
        abrirModalPrestacion(editarPrest.dataset.factEditarPrest);
        return;
      }
      const bajaPrest = e.target.closest("[data-fact-baja-prest]");
      if (bajaPrest) {
        cambiarEstadoPrestacion(bajaPrest.dataset.factBajaPrest, false);
        return;
      }
      const reactivarPrest = e.target.closest("[data-fact-reactivar-prest]");
      if (reactivarPrest) {
        cambiarEstadoPrestacion(reactivarPrest.dataset.factReactivarPrest, true);
        return;
      }
      const editarArancel = e.target.closest("[data-fact-editar-arancel]");
      if (editarArancel) {
        abrirModalArancel(editarArancel.dataset.factEditarArancel);
        return;
      }
      const quitarArancel = e.target.closest("[data-fact-eliminar-arancel]");
      if (quitarArancel) eliminarArancel(quitarArancel.dataset.factEliminarArancel);
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
    // Fichas de salud (Módulo 4: anamnesis)
    renderFichasSalud,
    cargarFichasSalud,
    abrirModalFicha,
    cerrarModalFicha,
    abrirPdfHistoria,
    // Doctores y obras sociales (catálogo de coberturas)
    doctoresActivos,
    obrasSocialesActivas,
    obraSocialPorId,
    nombreObraSocial,
    renderObrasSociales,
    cargarObrasSocialesSelect,
    // Bot WhatsApp (menú configurable del FAQ Bot)
    renderMenuWhatsApp,
    cargarMenuWhatsApp,
    cargarVistaPreviaMenu,
    // Disponibilidad (agenda semanal por odontólogo)
    cargarDisponibilidad,
    renderDisponibilidad,
    rangosDe,
    probarBloquesDisponibilidad,
    DIAS_SEMANA,
    PLANTILLA_SEMANAL,
    // Días personalizados (excepciones por fecha) que usan app.js y calendar.js
    renderExcepciones,
    excepcionesDeFecha,
    cargarExcepcionesDeFecha,
    cargarExcepcionesDeRango,
    rangosVigentesDeFecha,
    diaSemanaDe,
    // Facturación y caja (Módulo 3): cierre de visitas, obra social y liquidaciones
    cargarFacturacion,
    cargarCajaDelDia,
    renderCaja,
    abrirModalCobro,
    cargarPresentacion,
    cargarLiquidacionMedicos,
    cargarPrestaciones,
    cargarAranceles,
    fmtMoneda,
    badgeEstadoPago,
    practicasTexto,
    rutaPrestacion,
    rutaArancel,
    ESTADO_PAGO,
    TIPO_PAGO_TEXTO,
  };
})();






