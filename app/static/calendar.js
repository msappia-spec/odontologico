/* =========================================================================
   Franciosi Odontología - Módulo de Calendario Mensual y Disponibilidad del Día
   -------------------------------------------------------------------------
   JavaScript puro (sin librerías externas) montado sobre el puente
   `window.OdontoApp` que expone app.js. Responsabilidades:

     * Grilla de calendario mensual con todos los turnos del mes, mostrando el
       nombre del paciente y el color por estado del turno: **CONFIRMADO** va en
       verde con prioridad (aunque tenga asignado el color del odontólogo),
       **PENDIENTE** mantiene el color del profesional y **CANCELADO** queda en
       gris atenuado (la paleta de odontólogos se reutiliza para los pendientes,
       de modo que leyenda y grilla coincidan siempre).
     * Filtro por odontólogo en la parte superior: "Todos los odontólogos" más un
       botón por profesional, con el color que lo identifica y resaltado cuando
       está activo. Al elegir uno, la grilla, los turnos ocupados del día y los
       horarios disponibles muestran únicamente sus turnos.
     * Tarjeta flotante (tooltip) al pasar el mouse por un turno con el detalle:
       paciente, teléfono, horario exacto, odontólogo y estados de turno y WhatsApp.
     * Panel del día seleccionado con dos columnas: "Turnos ocupados" (editar,
       cancelar y reactivar) y "Horarios disponibles" (bloques de 30 minutos
       clicables que abren el formulario con la fecha, la hora y —cuando hay un
       filtro por odontólogo activo— ese profesional ya seleccionado y bloqueado,
       de modo que el turno no se agende por error en otra agenda).
     * Los bloques de "Horarios disponibles" se derivan de la **agenda semanal
       configurada por odontólogo** (tabla `disponibilidad_doctores`, leída con
       GET /doctores/{id}/disponibilidad): no hay horarios fijos en el código.
       Un profesional sin rangos para ese día no ofrece bloques, y cada bloque
       se descarta si algún odontólogo ya tiene un turno vigente que se cruza.

   Endpoints de la API usados (vía OdontoApp.api → fetch a /api/v1):
     GET    /turnos                          lectura (ya cargada por app.js)
     GET    /doctores/{id}/disponibilidad    agenda semanal por odontólogo
     POST   /turnos                          alta desde un bloque disponible / botón "Nuevo turno"
     PATCH  /turnos/{id}                     reprogramar, cancelar o reactivar

   Nota: los rechazos del backend (409 agenda del odontólogo y 400 "el paciente
   ya tiene un turno con otro odontólogo en este horario") los explica en
   pantalla app.js mediante la alerta del modal y un toast.

   Nota: el panel de "Turnos ocupados" reutiliza la tarjeta de turno de app.js
   (ids `agenda-*`) para no duplicar el render ni las acciones de WhatsApp.
   ========================================================================= */
(function () {
  "use strict";

  /* ---------------------------- Configuración ---------------------------- */
  /* Agenda semanal: NO está en el código. Cada odontólogo define sus rangos en
     la pestaña "Disponibilidad" (tabla `disponibilidad_doctores`) y app.js los
     carga en `state.dispPorDoctor`; acá sólo se leen. La convención de días es
     la de JavaScript: 0 = domingo … 6 = sábado. */
  const SLOT_MIN = 30;        // duración de cada bloque de disponibilidad
  const MAX_EVENTOS_DIA = 3;  // turnos dibujados en la celda antes del "+N"
  const MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
  ];

  /* ------------------------------- Estado -------------------------------- */
  const cal = {
    anio: 0,
    mes: 0,          // 0 = enero
    filtro: "",      // "" = todos los odontólogos; "<doctor_id>" = sólo ese profesional
    tooltip: null,
  };

  /* ------------------------------ Utilidades ----------------------------- */
  function app() { return window.OdontoApp || null; }
  function $id(id) { return document.getElementById(id); }

  function isoDe(d) {
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${d.getFullYear()}-${m}-${dd}`;
  }
  function fechaDe(iso) {
    const p = String(iso).split("-").map(Number);
    return new Date(p[0], p[1] - 1, p[2]);
  }
  function moverDias(d, n) {
    const c = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    c.setDate(c.getDate() + n);
    return c;
  }
  function aMinutos(hhmm) {
    const p = String(hhmm).split(":").map(Number);
    return p[0] * 60 + (p[1] || 0);
  }
  function hhmm(minutos) {
    const h = String(Math.floor(minutos / 60)).padStart(2, "0");
    const m = String(minutos % 60).padStart(2, "0");
    return `${h}:${m}`;
  }

  /* ---------------------------- Datos del sitio -------------------------- */
  function turnos() { return (app() && app().state.turnos) || []; }
  function doctores() { return (app() && app().state.doctores) || []; }
  function doctoresActivos() { return doctores().filter((d) => d.activo); }
  function turnoPorId(id) {
    const num = Number(id);
    return turnos().find((t) => Number(t.id) === num) || null;
  }
  function fechaSeleccionada() {
    return (app() && app().state.agendaFecha) || isoDe(new Date());
  }
  function turnosDelDia(iso) {
    return turnos()
      .filter((t) => app().parteFecha(t.fecha_hora_inicio) === iso)
      .sort((a, b) => a.fecha_hora_inicio.localeCompare(b.fecha_hora_inicio));
  }

  /* ------------------------- Agenda de los médicos ------------------------ */
  /* La disponibilidad llega de la API (tabla `disponibilidad_doctores`) y app.js
     la deja en `state.dispPorDoctor`; acá sólo se lee. */
  function dispCargada() {
    return Boolean(app() && app().state.dispCargada);
  }
  function rangosDe(doctorId) {
    const A = app();
    if (!A || typeof A.rangosDe !== "function") return [];
    return A.rangosDe(doctorId) || [];
  }

  /* -------------------- Excepciones por fecha (un día) ------------------- */
  /* Un día concreto puede tener sus propios tramos (tabla
     `disponibilidad_especifica`, GET /doctores/{id}/disponibilidad-especifica):
     si existen, mandan sobre la agenda semanal. app.js los cachea y desde acá
     sólo se leen. */
  function excepcionesDe(doctorId, iso) {
    const A = app();
    if (!A || typeof A.excepcionesDeFecha !== "function") return [];
    return A.excepcionesDeFecha(doctorId, iso) || [];
  }

  /* Rangos que rigen una fecha concreta: los propios del día si los tiene y, si
     no, la agenda semanal del día de la semana. app.js aplica la misma regla que
     el backend (HABILITADO menos BLOQUEADO), así la grilla nunca ofrece un bloque
     que el servidor vaya a rechazar. */
  function rangosDeFecha(doctorId, iso) {
    const A = app();
    if (A && typeof A.rangosVigentesDeFecha === "function") {
      return A.rangosVigentesDeFecha(doctorId, iso) || [];
    }
    const dia = fechaDe(iso).getDay();
    return rangosDe(doctorId).filter((r) => Number(r.dia_semana) === dia);
  }

  /* ¿Ese odontólogo tiene tramos cargados a mano para esa fecha? */
  function esDiaPersonalizado(doctorId, iso) {
    return excepcionesDe(doctorId, iso).length > 0;
  }

  /* ¿Alguno de los odontólogos que se están mostrando personalizó esa fecha? Se
     usa para marcar la celda del mes y avisar en el panel del día. */
  function diaPersonalizado(fecha) {
    if (!dispCargada()) return false;
    const iso = isoDe(fecha);
    return candidatosDelDia().some((d) => esDiaPersonalizado(d.id, iso));
  }

  /* El mes visible puede caer fuera de la ventana de excepciones que app.js trae
     al arrancar: se piden las del rango completo (una sola vez por rango; app.js
     recuerda qué ya pidió) y, cuando llegan, el evento `odonto:disponibilidad`
     vuelve a dibujar grilla, panel del día y tarjeta de disponibilidad. */
  function pedirExcepcionesDelRango(desde, hasta) {
    const A = app();
    if (!dispCargada() || !A || typeof A.cargarExcepcionesDeRango !== "function") return;
    A.cargarExcepcionesDeRango(desde, hasta)
      .then(function (nuevos) {
        if (nuevos) document.dispatchEvent(new CustomEvent("odonto:disponibilidad"));
      })
      .catch(function () { /* silencioso: la grilla ya se dibujó */ });
  }
  /* Doctores que el calendario puede ofrecer: los activos y, si la leyenda tiene
     un filtro activo, únicamente ese profesional. */
  function candidatosDelDia() {
    const activos = doctoresActivos();
    if (!cal.filtro) return activos;
    return activos.filter((d) => String(d.id) === String(cal.filtro));
  }

  /* Filtro activo por odontólogo: sin selección devuelve la lista completa. */
  function filtrados(lista) {
    if (!cal.filtro) return lista;
    return lista.filter((t) => String(t.doctor_id) === String(cal.filtro));
  }

  function doctorFiltrado() {
    if (!cal.filtro) return null;
    return doctores().find((d) => String(d.id) === String(cal.filtro)) || null;
  }

  /* -------------------------------- Tooltip ------------------------------ */
  /* Tarjeta flotante con el detalle del turno (paciente, teléfono, horario,
     odontólogo, estado del turno y estado de WhatsApp con su ícono). */
  function htmlTooltip(t) {
    const A = app();
    const paciente = A.porId(A.state.pacientes, t.paciente_id);
    const doctor = A.porId(A.state.doctores, t.doctor_id);
    const nombre = A.nombrePaciente(t.paciente_id);
    const color = A.colorDe(t.doctor_id);
    const dur = A.duracionTexto(t.fecha_hora_inicio, t.fecha_hora_fin);
    const tel = paciente && paciente.telefono ? paciente.telefono : "Sin teléfono registrado";
    const dni = paciente && paciente.dni ? ` · DNI ${paciente.dni}` : "";

    return (
      `<div class="cal-tooltip-cab">` +
      A.avatar(nombre, t.paciente_id) +
      `<div class="min-w-0">` +
      `<p class="text-sm font-bold text-slate-900 truncate">${A.esc(nombre)}</p>` +
      `<p class="text-[11px] text-slate-500 truncate">${A.esc(tel + dni)}</p>` +
      `</div></div>` +
      `<dl class="m-0">` +
      `<div class="cal-tooltip-fila"><dt>Horario</dt><dd>` +
      `${A.esc(A.parteHora(t.fecha_hora_inicio))} a ${A.esc(A.parteHora(t.fecha_hora_fin))} hs` +
      (dur ? ` <span class="text-slate-400 font-normal">(${A.esc(dur)})</span>` : "") +
      `</dd></div>` +
      `<div class="cal-tooltip-fila"><dt>Odontólogo</dt><dd>` +
      `<span class="cal-dot" style="background:${color}"></span> ` +
      A.esc(doctor ? doctor.nombre : A.nombreDoctor(t.doctor_id)) +
      (doctor && doctor.especialidad
        ? ` <span class="text-slate-400 font-normal">${A.esc(doctor.especialidad)}</span>`
        : "") +
      `</dd></div>` +
      `<div class="cal-tooltip-fila"><dt>Estado del turno</dt><dd>${A.badgeTurno(t.estado_turno)}</dd></div>` +
      `<div class="cal-tooltip-fila"><dt>WhatsApp</dt><dd>${A.badgeWhatsApp(t)}</dd></div>` +
      `</dl>` +
      `<p class="text-[11px] text-slate-400 mt-2 mb-0">` +
      `Hacé clic en el día para editarlo o cancelarlo` +
      `</p>`
    );
  }

  function mostrarTooltip(el) {
    if (cal.tooltip && cal.tooltip.dataset.turno === el.dataset.eventoId) return;
    const t = turnoPorId(el.dataset.eventoId);
    if (!t) return;
    ocultarTooltip();
    const card = document.createElement("div");
    card.className = "cal-tooltip";
    card.dataset.turno = String(t.id);
    card.innerHTML = htmlTooltip(t);
    document.body.appendChild(card);
    cal.tooltip = card;
    colocarTooltip(el);
  }

  function colocarTooltip(el) {
    const card = cal.tooltip;
    if (!card) return;
    const r = el.getBoundingClientRect();
    const w = card.offsetWidth;
    const h = card.offsetHeight;
    const margen = 10;
    const left = Math.min(Math.max(r.left, 8), Math.max(8, window.innerWidth - w - 8));
    let top = r.bottom + margen;
    if (top + h > window.innerHeight - 8) {
      top = Math.max(8, r.top - h - margen);
    }
    card.style.left = `${Math.round(left)}px`;
    card.style.top = `${Math.round(top)}px`;
  }

  function ocultarTooltip() {
    if (cal.tooltip) {
      cal.tooltip.remove();
      cal.tooltip = null;
    }
  }

  /* --------------------------- Render del mes ---------------------------- */
  /* Agrupa los turnos visibles (incluidos los días de borde) por fecha ISO. */
  function mapaDelMes(desde, hasta) {
    const mapa = {};
    turnos().forEach(function (t) {
      const iso = app().parteFecha(t.fecha_hora_inicio);
      if (iso < desde || iso > hasta) return;
      (mapa[iso] = mapa[iso] || []).push(t);
    });
    Object.keys(mapa).forEach(function (k) {
      mapa[k].sort((a, b) => a.fecha_hora_inicio.localeCompare(b.fecha_hora_inicio));
    });
    return mapa;
  }

  function eventoChip(t) {
    const A = app();
    const color = A.colorDe(t.doctor_id);
    const estado = t.estado_turno;
    const nombre = A.nombrePaciente(t.paciente_id);
    const hora = A.parteHora(t.fecha_hora_inicio);

    /* Prioridad de color en la grilla:
         * CONFIRMADO -> verde (gana sobre el color del odontólogo);
         * CANCELADO  -> gris atenuado y tachado;
         * PENDIENTE  -> color asignado al odontólogo.                    */
    let clases = "cal-evento";
    let estilo = `background:${color}22;border-left-color:${color}`;
    if (estado === "CONFIRMADO") {
      clases += " confirmado bg-green-500 text-white";
      estilo = "border-left-color:#15803d";
    } else if (estado === "CANCELADO") {
      clases += " cancelado bg-slate-100 text-slate-400";
      estilo = "border-left-color:#cbd5e1";
    }

    return (
      `<button type="button" class="${clases}" data-evento-id="${t.id}" ` +
      `style="${estilo}" ` +
      `aria-label="${A.esc(nombre)} a las ${A.esc(hora)} (${A.esc(estado)})">` +
      `<span class="cal-evento-hora hidden sm:inline">${A.esc(hora)}</span>` +
      `<span class="cal-evento-nombre">${A.esc(nombre)}</span>` +
      `</button>`
    );
  }

  function celdaHtml(fecha, esOtroMes, mapa) {
    const A = app();
    const iso = isoDe(fecha);
    const delDia = mapa[iso] || [];
    const visibles = filtrados(delDia);
    const activos = visibles.filter((t) => t.estado_turno !== "CANCELADO").length;
    const clases = ["cal-dia"];
    if (esOtroMes) clases.push("otro-mes");
    if (iso === isoDe(new Date())) clases.push("hoy");
    if (iso === fechaSeleccionada()) clases.push("seleccionado");
    const personalizado = diaPersonalizado(fecha);
    if (personalizado) clases.push("personalizado");
    if (!hayAgenda(fecha)) clases.push("sin-agenda");

    let html =
      `<div class="${clases.join(" ")}" data-dia="${iso}" role="button" tabindex="0" ` +
      `aria-label="${A.esc(A.fechaLarga(iso))}: ${activos} turno${activos === 1 ? "" : "s"}` +
      `${personalizado ? " (horario personalizado)" : ""}">` +
      `<div class="cal-dia-top"><span class="cal-dia-num">${fecha.getDate()}</span>` +
      (personalizado
        ? `<span class="cal-dia-custom" title="Este día tiene tramos propios: se usa ese horario ` +
          `en lugar de la agenda semanal">Personalizado</span>`
        : "") +
      (activos ? `<span class="cal-dia-conteo">${activos}</span>` : "") +
      `</div>`;

    visibles.slice(0, MAX_EVENTOS_DIA).forEach(function (t) {
      html += eventoChip(t);
    });
    if (visibles.length > MAX_EVENTOS_DIA) {
      html +=
        `<button type="button" class="cal-evento-mas" data-ver-dia="${iso}">` +
        `+${visibles.length - MAX_EVENTOS_DIA} más` +
        `</button>`;
    }
    return html + `</div>`;
  }

  /* Leyenda y filtro de odontólogos: un botón inicial ("Todos los odontólogos")
     y un chip por profesional. La selección es única y excluyente: el botón
     activo (o "Todos") define qué turnos se muestran en la grilla, en los turnos
     ocupados del día y en los horarios disponibles. */
  function renderLeyenda() {
    const A = app();
    const cont = $id("cal-leyenda");
    if (!A || !cont) return;

    const activos = doctoresActivos();

    // Si el odontólogo filtrado dejó de estar activo, se vuelve a la vista combinada.
    if (cal.filtro && !activos.some((d) => String(d.id) === String(cal.filtro))) {
      cal.filtro = "";
      A.state.agendaDoctor = "";
      const select = $id("agenda-doctor");
      if (select) select.value = "";
      if (typeof A.renderAgenda === "function") A.renderAgenda();
    }

    const todosActivo = !cal.filtro;
    let html =
      `<span class="cal-filtro-label">Filtrar por</span>` +
      `<button type="button" class="cal-leyenda-chip todos${todosActivo ? " activo" : ""}" ` +
      `data-filtro="" aria-pressed="${todosActivo ? "true" : "false"}" ` +
      `title="Ver los turnos de todos los odontólogos">` +
      `Todos los odontólogos</button>`;

    if (!activos.length) {
      html += `<span class="text-xs text-slate-400">Sin odontólogos activos cargados.</span>`;
      cont.innerHTML = html;
      return;
    }

    html += activos
      .map(function (d) {
        const color = A.colorDe(d.id);
        const activo = String(cal.filtro) === String(d.id);
        return (
          `<button type="button" class="cal-leyenda-chip${activo ? " activo" : ""}" ` +
          `data-filtro="${d.id}" aria-pressed="${activo ? "true" : "false"}" ` +
          `style="--chip-color:${color};--chip-ring:${color}22" ` +
          `title="Ver sólo los turnos de ${A.esc(d.nombre)}">` +
          `<span class="cal-dot" style="background:${color}"></span>` +
          `${A.esc(d.nombre)} <span class="cal-leyenda-esp">${A.esc(d.especialidad)}</span>` +
          `</button>`
        );
      })
      .join("");

    cont.innerHTML = html;
  }

  function renderCalendario() {
    const A = app();
    const grid = $id("cal-grid");
    if (!A || !grid) return;

    const primero = new Date(cal.anio, cal.mes, 1);
    const offset = (primero.getDay() + 6) % 7;                        // lunes = 0
    const diasMes = new Date(cal.anio, cal.mes + 1, 0).getDate();
    const celdas = Math.ceil((offset + diasMes) / 7) * 7;
    const inicio = moverDias(primero, -offset);
    const mapa = mapaDelMes(isoDe(inicio), isoDe(moverDias(inicio, celdas - 1)));

    /* Las excepciones por fecha pueden caer fuera de la ventana que app.js trae al
       arrancar (el mes visible se mueve): se piden para las 5 o 6 semanas que
       muestra la grilla y, cuando llegan, todo se redibuja con `render`. */
    pedirExcepcionesDelRango(isoDe(inicio), isoDe(moverDias(inicio, celdas - 1)));

    let html = "";
    for (let i = 0; i < celdas; i += 1) {
      const fecha = moverDias(inicio, i);
      html += celdaHtml(fecha, fecha.getMonth() !== cal.mes, mapa);
    }
    grid.innerHTML = html;
    renderLeyenda();

    const titulo = $id("cal-mes-titulo");
    if (titulo) titulo.textContent = `${MESES[cal.mes]} de ${cal.anio}`;

    const clave = `${cal.anio}-${String(cal.mes + 1).padStart(2, "0")}`;
    const delMes = filtrados(turnos()).filter(
      (t) => A.parteFecha(t.fecha_hora_inicio).slice(0, 7) === clave
    );
    const cancelados = delMes.filter((t) => t.estado_turno === "CANCELADO").length;
    const resumen = $id("cal-resumen");
    if (resumen) {
      resumen.textContent =
        `${delMes.length} turno${delMes.length === 1 ? "" : "s"}` +
        (cancelados ? ` · ${cancelados} cancelado${cancelados === 1 ? "" : "s"}` : "");
    }

    // El subtítulo del mes recuerda qué odontólogo está filtrado.
    const sub = $id("cal-mes-sub");
    if (sub) {
      const doctor = doctorFiltrado();
      sub.textContent = doctor
        ? `Mostrando sólo los turnos de ${doctor.nombre}`
        : "Turnos del mes por odontólogo";
    }
  }

  /* -------------------- Horarios disponibles del día --------------------- */
  /* Mapa minuto a minuto (1440) con la cobertura de los rangos configurados de
     cada odontólogo para ese día. Permite unir las agendas de varios
     profesionales y saber exactamente quién cubre cada bloque. */
  function coberturaDelDia(iso) {
    const cobertura = new Array(1440).fill(false);
    const porDoctor = {};
    candidatosDelDia().forEach(function (d) {
      const minutos = new Set();
      // Los tramos del día ya vienen resueltos: excepciones propias de la fecha
      // o, si no hay ninguna, la agenda semanal del día de la semana.
      rangosDeFecha(d.id, iso).forEach(function (rango) {
        const desde = Math.max(0, aMinutos(rango.hora_inicio));
        const hasta = Math.min(1440, aMinutos(rango.hora_fin));
        for (let m = desde; m < hasta; m += 1) {
          cobertura[m] = true;
          minutos.add(m);
        }
      });
      porDoctor[String(d.id)] = minutos;
    });
    return { cobertura: cobertura, porDoctor: porDoctor };
  }

  /* Bloques de SLOT_MIN minutos dentro de la cobertura del día: se recorren los
     tramos contiguos y se corta cada SLOT_MIN minutos. Un bloque sólo se ofrece
     si cae entero dentro de la agenda de al menos un odontólogo. */
  function slotsDelDia(iso) {
    const calculo = coberturaDelDia(iso);
    const cobertura = calculo.cobertura;
    const slots = [];
    let m = 0;
    while (m < 1440) {
      if (!cobertura[m]) { m += 1; continue; }
      let fin = m;
      while (fin < 1440 && cobertura[fin]) fin += 1;
      for (let s = m; s + SLOT_MIN <= fin; s += SLOT_MIN) {
        slots.push({ inicio: s, fin: s + SLOT_MIN });
      }
      m = fin;
    }
    return { slots: slots, porDoctor: calculo.porDoctor };
  }

  /* El bloque cae entero dentro de la agenda del odontólogo. */
  function cubreBloque(minutos, slot) {
    if (!minutos) return false;
    for (let m = slot.inicio; m < slot.fin; m += 1) {
      if (!minutos.has(m)) return false;
    }
    return true;
  }

  /* ¿Algún odontólogo del filtro atiende ese día? Se usa para marcar las celdas
     "sin agenda" del mes: un día con excepciones cuenta sólo si le quedan tramos
     (bloquear el día completo deja la celda sin agenda). Mientras la
     disponibilidad no llegó del backend no se marca nada, para no parpadear en el
     primer render. */
  function hayAgenda(fecha) {
    if (!dispCargada()) return true;
    const iso = isoDe(fecha);
    return candidatosDelDia().some((d) => rangosDeFecha(d.id, iso).length > 0);
  }

  /* Devuelve los minutos del día del turno, o null si no cae en ese día. */
  function minutosDelTurno(isoFechaHora, isoDia) {
    if (String(isoFechaHora || "").slice(0, 10) !== isoDia) return null;
    return aMinutos(String(isoFechaHora).slice(11, 16));
  }

  function solapaSlot(slot, t, isoDia) {
    const ini = minutosDelTurno(t.fecha_hora_inicio, isoDia);
    if (ini === null) return false;
    const dur = Math.max(
      1,
      Math.round((new Date(t.fecha_hora_fin) - new Date(t.fecha_hora_inicio)) / 60000)
    );
    return slot.inicio < ini + dur && ini < slot.fin;
  }

  function renderDisponibles() {
    const A = app();
    const cont = $id("cal-disponibles");
    const resumen = $id("cal-disponibles-resumen");
    if (!A || !cont) return;

    const iso = fechaSeleccionada();
    const delDia = turnosDelDia(iso);
    // Mismo filtro que la grilla: lo fijan los chips de la leyenda.
    const candidatos = candidatosDelDia();

    /* El día puede tener tramos propios (excepciones por fecha): ahí la agenda
       semanal no se usa y conviene decirlo, porque el panel puede mostrar más o
       menos bloques que un día equivalente de la semana. */
    const personalizados = candidatos.filter((d) => esDiaPersonalizado(d.id, iso));
    const origen = $id("agenda-origen");
    if (origen) {
      origen.textContent = personalizados.length
        ? `Día con horario personalizado para ${personalizados
            .map((d) => d.nombre)
            .join(", ")}: ese día se usa el horario cargado a mano, no la agenda semanal.`
        : "";
    }

    /* La disponibilidad se pide a la API cuando arranca el panel; hasta que llega
       no se puede afirmar que no haya horarios. */
    if (!dispCargada()) {
      cont.innerHTML = A.vacio(
        "Leyendo la agenda de los odontólogos…",
        "La disponibilidad viene del backend (GET /doctores/{id}/disponibilidad)."
      );
      if (resumen) resumen.textContent = "…";
      return;
    }

    if (!candidatos.length) {
      cont.innerHTML = A.vacio(
        "Sin odontólogos para ofrecer",
        "Activá un odontólogo o quitá el filtro del calendario."
      );
      if (resumen) resumen.textContent = "0";
      return;
    }

    const calculo = slotsDelDia(iso);
    const slots = calculo.slots;

    if (!slots.length) {
      cont.innerHTML = A.vacio(
        "Ningún odontólogo atiende este día",
        personalizados.length
          ? "Ese día quedó bloqueado por sus tramos personalizados: revisá la tarjeta " +
            "«Gestionar un día específico», en la pestaña «Disponibilidad»."
          : "Los rangos horarios se configuran en la pestaña «Disponibilidad»."
      );
      if (resumen) resumen.textContent = "0";
      return;
    }

    const ahora = Date.now();
    let libres = 0;

    cont.innerHTML = slots
      .map(function (slot) {
        const ocupados = delDia.filter(
          (t) => t.estado_turno !== "CANCELADO" && solapaSlot(slot, t, iso)
        );
        /* Sólo se ofrecen los odontólogos cuya agenda semanal cubre el bloque
           completo y que no tengan ya un turno vigente en ese horario. */
        const libresDocs = candidatos.filter(function (d) {
          if (!cubreBloque(calculo.porDoctor[String(d.id)], slot)) return false;
          return !ocupados.some((t) => String(t.doctor_id) === String(d.id));
        });
        const paso = new Date(`${iso}T${hhmm(slot.inicio)}:00`).getTime() <= ahora;
        const deshabilitado = paso || !libresDocs.length;
        if (!deshabilitado) libres += 1;

        let marcas;
        if (libresDocs.length) {
          marcas = libresDocs
            .map(function (d) {
              return (
                `<span class="cal-dot" style="background:${A.colorDe(d.id)}" ` +
                `title="${A.esc(d.nombre)}"></span>`
              );
            })
            .join("");
        } else if (paso) {
          marcas = `<span class="text-[11px] text-slate-400">Hora pasada</span>`;
        } else {
          marcas = `<span class="text-[11px] text-slate-400">Fuera de agenda u ocupado</span>`;
        }

        return (
          `<button type="button" class="cal-slot" data-slot="${hhmm(slot.inicio)}" ` +
          `data-doctor="${libresDocs.length ? libresDocs[0].id : ""}" ` +
          `${deshabilitado ? "disabled" : ""} ` +
          `title="${deshabilitado ? "Bloque no disponible" : "Agendar a las " + hhmm(slot.inicio)}">` +
          `<span class="cal-slot-hora">${hhmm(slot.inicio)}` +
          `<small>a ${hhmm(slot.fin)}</small></span>` +
          `<span class="cal-slot-docs">${marcas}</span>` +
          `</button>`
        );
      })
      .join("");

    if (resumen) resumen.textContent = String(libres);
  }

  /* --------------------- Selección de día y de mes ----------------------- */
  /* Estado del día activo: `estado.agendaFecha` es la única fuente de verdad,
     así el calendario y la lista de turnos de app.js quedan siempre iguales. */
  function render() {
    if (!app()) return;
    const d = fechaDe(fechaSeleccionada());
    cal.anio = d.getFullYear();
    cal.mes = d.getMonth();
    renderCalendario();
    renderDisponibles();
  }

  function seleccionarDia(iso) {
    const A = app();
    if (!A || !iso) return;
    ocultarTooltip();
    A.state.agendaFecha = iso;
    if (typeof A.renderAgenda === "function") A.renderAgenda();
    const d = fechaDe(iso);
    cal.anio = d.getFullYear();
    cal.mes = d.getMonth();
    renderCalendario();
    renderDisponibles();
  }

  function moverMes(n) {
    const d = new Date(cal.anio, cal.mes + n, 1);
    cal.anio = d.getFullYear();
    cal.mes = d.getMonth();
    ocultarTooltip();
    renderCalendario();
  }

  function irAlMesActual() { seleccionarDia(isoDe(new Date())); }

  /* -------------------- Filtro por odontólogo (chips) -------------------- */
  /* `cal.filtro` es la única fuente de verdad: los chips de la leyenda y el
     selector "Odontólogo" del panel del día (que renderiza app.js) lo fijan
     desde acá, y los tres paneles del calendario se redibujan en consecuencia. */
  function cambiarFiltro(id) {
    const A = app();
    cal.filtro = id ? String(id) : "";
    if (A) {
      A.state.agendaDoctor = cal.filtro;
      const select = $id("agenda-doctor");
      if (select && select.value !== cal.filtro) select.value = cal.filtro;
      if (typeof A.renderAgenda === "function") A.renderAgenda();
    }
    ocultarTooltip();
    renderCalendario();
    renderDisponibles();
  }

  /* ------------------ Acciones sobre turnos (PATCH /turnos) -------------- */

  /* Título del aviso de éxito según el estado al que se movió el turno. */
  const AVISO_ESTADO = {
    CANCELADO: "Turno cancelado",
    PENDIENTE: "Turno reactivado",
    CONFIRMADO: "Asistencia confirmada",
  };

  function cambiarEstado(id, estado, etiqueta, pedirConfirmacion = true) {
    const A = app();
    const t = turnoPorId(id);
    if (!A || !t) return;
    const detalle =
      `${A.nombrePaciente(t.paciente_id)} · ${A.fechaCorta(t.fecha_hora_inicio)} ` +
      `${A.parteHora(t.fecha_hora_inicio)} · ${A.nombreDoctor(t.doctor_id)}`;
    if (pedirConfirmacion && !window.confirm(`¿${etiqueta} el turno de ${detalle}?`)) return;
    ocultarTooltip();
    A.api(`/turnos/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ estado_turno: estado }),
    })
      .then(function () {
        A.toast("exito", AVISO_ESTADO[estado] || "Turno actualizado", detalle);
        return A.cargarTodo();
      })
      .catch(function (err) {
        A.toast("error", "No se pudo actualizar el turno", err.message);
      });
  }

  /* Confirmar la asistencia del paciente: acción de un toque —sin diálogo, a
     diferencia de cancelar o reactivar— que deja el turno en verde cuando el
     paciente avisa por teléfono o llega al consultorio. */
  function confirmarAsistencia(id) {
    cambiarEstado(id, "CONFIRMADO", "Confirmar", false);
  }

  /* ------------------------ Formulario del turno ------------------------- */
  function asignar(id, valor) {
    const el = $id(id);
    if (!el || valor === undefined || valor === null || valor === "") return;
    el.value = String(valor);
  }

  /* Elige la opción de duración más parecida a la duración real del turno. */
  function asignarDuracion(min) {
    const sel = $id("turno-duracion");
    if (!sel || !sel.options.length) return;
    const valores = Array.prototype.map
      .call(sel.options, (o) => Number(o.value))
      .filter((v) => !isNaN(v));
    if (!valores.length) return;
    let mejor = valores[0];
    valores.forEach(function (v) {
      if (Math.abs(v - min) < Math.abs(mejor - min)) mejor = v;
    });
    sel.value = String(mejor);
  }

  function prepararModal(titulo) {
    const A = app();
    A.abrirModal();
    const modal = $id("modal-turno");
    if (!modal || modal.classList.contains("hide")) return false;
    const h = $id("modal-turno-titulo");
    if (h) h.textContent = titulo;
    const error = $id("turno-error");
    if (error) error.classList.add("hide");
    return true;
  }

  function abrirEdicion(id) {
    const A = app();
    const t = turnoPorId(id);
    if (!A || !t) return;
    ocultarTooltip();
    if (!prepararModal("Editar turno")) return;

    const min = Math.round(
      (new Date(t.fecha_hora_fin) - new Date(t.fecha_hora_inicio)) / 60000
    );
    asignar("turno-fecha", A.parteFecha(t.fecha_hora_inicio));
    asignar("turno-hora", A.parteHora(t.fecha_hora_inicio));
    asignar("turno-paciente", t.paciente_id);
    asignar("turno-doctor", t.doctor_id);
    asignarDuracion(min);

    A.state.turnoEditId = t.id;
    const guardar = $id("modal-turno-save");
    if (guardar) guardar.textContent = "Guardar cambios";
  }

  function agendarEnSlot(hora, doctorId) {
    const A = app();
    if (!A) return;
    ocultarTooltip();
    if (!prepararModal("Nuevo turno")) return;

    asignar("turno-fecha", fechaSeleccionada());
    asignar("turno-hora", hora);
    asignarDuracion(SLOT_MIN);
    A.state.turnoEditId = null;

    /* Odontólogo predeterminado del formulario: si el calendario está filtrado
       por un profesional, el turno se crea para ese odontólogo y el selector
       queda BLOQUEADO (así la secretaría no agenda por error en otra agenda);
       sin filtro se preselecciona el primer profesional libre del bloque. */
    const doctorFiltrado = cal.filtro ? String(cal.filtro) : "";
    const doctorSugerido = doctorFiltrado || (doctorId ? String(doctorId) : "");
    if (typeof A.fijarDoctorTurno === "function") {
      A.fijarDoctorTurno(doctorSugerido, Boolean(doctorFiltrado));
    } else {
      asignar("turno-doctor", doctorSugerido);   // compatibilidad
    }

    const guardar = $id("modal-turno-save");
    if (guardar) guardar.textContent = "Guardar turno";
    const pac = $id("turno-paciente");
    if (pac && pac.focus) pac.focus();
  }

  /* ------------------------------- Eventos ------------------------------- */
  function eventosGrilla() {
    const grid = $id("cal-grid");
    if (!grid) return;

    grid.addEventListener("click", function (e) {
      const mas = e.target.closest("[data-ver-dia]");
      if (mas) { seleccionarDia(mas.dataset.verDia); return; }
      const celda = e.target.closest("[data-dia]");
      if (celda) seleccionarDia(celda.dataset.dia);
    });

    grid.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      const celda = e.target.closest("[data-dia]");
      if (!celda || e.target !== celda) return;
      e.preventDefault();
      seleccionarDia(celda.dataset.dia);
    });

    /* Tooltip: se muestra al pasar el mouse o al enfocar con el teclado. */
    grid.addEventListener("mouseover", function (e) {
      const chip = e.target.closest("[data-evento-id]");
      if (chip) mostrarTooltip(chip);
    });
    grid.addEventListener("mouseout", function (e) {
      const chip = e.target.closest("[data-evento-id]");
      if (chip && !chip.contains(e.relatedTarget)) ocultarTooltip();
    });
    grid.addEventListener("focusin", function (e) {
      const chip = e.target.closest("[data-evento-id]");
      if (chip) mostrarTooltip(chip);
    });
    grid.addEventListener("focusout", function (e) {
      if (e.target.closest("[data-evento-id]")) ocultarTooltip();
    });
  }

  function eventosPanel() {
    const leyenda = $id("cal-leyenda");
    if (leyenda) {
      leyenda.addEventListener("click", function (e) {
        const chip = e.target.closest("[data-filtro]");
        if (!chip) return;
        const id = String(chip.dataset.filtro || "");
        // Volver a hacer clic en el odontólogo activo (o en "Todos") restablece
        // la vista combinada.
        cambiarFiltro(id && id !== String(cal.filtro) ? id : "");
      });
    }

    const disp = $id("cal-disponibles");
    if (disp) {
      disp.addEventListener("click", function (e) {
        const btn = e.target.closest("[data-slot]");
        if (btn && !btn.disabled) agendarEnSlot(btn.dataset.slot, btn.dataset.doctor);
      });
    }
  }

  /* Delegación global de las acciones de turno: sirve igual para las tarjetas
     del calendario, del dashboard y para cualquier botón futuro con data-*. */
  function eventosAcciones() {
    document.addEventListener("click", function (e) {
      const editar = e.target.closest("[data-editar-turno]");
      if (editar) { abrirEdicion(editar.dataset.editarTurno); return; }
      const cancelar = e.target.closest("[data-cancelar-turno]");
      if (cancelar) { cambiarEstado(cancelar.dataset.cancelarTurno, "CANCELADO", "Cancelar"); return; }
      const reactivar = e.target.closest("[data-reactivar-turno]");
      if (reactivar) { cambiarEstado(reactivar.dataset.reactivarTurno, "PENDIENTE", "Reactivar"); return; }
      const confirmar = e.target.closest("[data-confirmar-turno]");
      if (confirmar) confirmarAsistencia(confirmar.dataset.confirmarTurno);
    });
  }

  /* app.js ya maneja los botones de día; acá sólo re-renderizamos el calendario
     para que el mes visible siga a la fecha seleccionada. */
  function eventosSincronizacion() {
    ["agenda-prev", "agenda-next", "agenda-hoy"].forEach(function (id) {
      const b = $id(id);
      if (b) b.addEventListener("click", render);
    });
    const fecha = $id("agenda-fecha");
    if (fecha) fecha.addEventListener("change", render);
    const doctor = $id("agenda-doctor");
    if (doctor) {
      // El selector del panel del día y los chips de la leyenda son el mismo filtro.
      doctor.addEventListener("change", function () {
        cambiarFiltro(doctor.value);
      });
    }
  }

  function iniciar() {
    if (!app()) {
      if (window.console) console.error("[calendar] No se encontró window.OdontoApp (app.js).");
      return;
    }
    if (!$id("cal-grid")) return;   // la vista del calendario no está montada

    const hoy = new Date();
    cal.anio = hoy.getFullYear();
    cal.mes = hoy.getMonth();

    eventosGrilla();
    eventosPanel();
    eventosAcciones();
    eventosSincronizacion();

    const prev = $id("cal-prev");
    if (prev) prev.addEventListener("click", function () { moverMes(-1); });
    const next = $id("cal-next");
    if (next) next.addEventListener("click", function () { moverMes(1); });
    const mesHoy = $id("cal-hoy");
    if (mesHoy) mesHoy.addEventListener("click", irAlMesActual);

    window.addEventListener("scroll", ocultarTooltip, true);
    window.addEventListener("resize", ocultarTooltip);

    // Primer render con los datos que ya tenga app.js y en cada recarga de datos
    // o de disponibilidad (la agenda semanal viaja en su propio evento).
    render();
    document.addEventListener("odonto:datos", render);
    document.addEventListener("odonto:disponibilidad", render);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", iniciar);
  } else {
    iniciar();
  }

  /* --------------------------- API del módulo ---------------------------- */
  window.OdontoCalendar = {
    iniciar: iniciar,
    render: render,
    renderCalendario: renderCalendario,
    renderDisponibles: renderDisponibles,
    seleccionarDia: seleccionarDia,
    irAlMesActual: irAlMesActual,
    moverMes: moverMes,
    cambiarFiltro: cambiarFiltro,
    filtroActual: function () { return cal.filtro; },
    abrirEdicion: abrirEdicion,
    cambiarEstado: cambiarEstado,
    // Disponibilidad configurada (la agenda semanal vive en el backend; app.js
    // la cachea y este módulo sólo la lee para armar los bloques del día).
    rangosDe: rangosDe,
    // Excepciones por fecha: los tramos propios de un día reemplazan a la agenda
    // semanal mientras existan (app.js resuelve la precedencia).
    rangosDeFecha: rangosDeFecha,
    excepcionesDe: excepcionesDe,
    esDiaPersonalizado: esDiaPersonalizado,
    diaPersonalizado: diaPersonalizado,
    dispCargada: dispCargada,
    hayAgenda: hayAgenda,
    slotsDelDia: slotsDelDia,
    cargarDisponibilidad: function () {
      if (app() && typeof app().cargarDisponibilidad === "function") {
        return app().cargarDisponibilidad();
      }
      return Promise.resolve();
    },
    slotMinutos: SLOT_MIN,
  };
})();
