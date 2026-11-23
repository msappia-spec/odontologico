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
     * Vista semanal por odontólogo (botones «Mes» / «Semana»): muestra de lunes
       a viernes **todos los turnos** del profesional elegido. La semana se mueve
       con las flechas de la barra superior y el selector «Odontólogo de la
       semana» cambia de agenda sin mezclar profesionales. Al cambiar de semana
       siempre se muestra la semana del odontólogo seleccionado.
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
     * **Impresión de la agenda del día**: el botón «Imprimir» del panel usa la
       fecha y el odontólogo elegidos, y cada columna de la vista semanal imprime
       su propio día. La hoja A4 se arma en `#agenda-impresion` con todos los
       turnos (también los cancelados, atenuados) y sus datos de contacto; el
       `@media print` de styles.css oculta el resto del panel.

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
  const DIAS_VISTA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"];
  const MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
  ];
  const ESTADO_IMPRESION = {
    PENDIENTE: { texto: "Pendiente", clase: "pendiente" },
    CONFIRMADO: { texto: "Confirmado", clase: "confirmado" },
    CANCELADO: { texto: "Cancelado", clase: "cancelado" },
  };
  const ICONO_IMPRESORA =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M6 9V2h12v7M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/>' +
    '<path d="M6 14h12v8H6z"/></svg>';

  /* ------------------------------- Estado -------------------------------- */
  const cal = {
    anio: 0,
    mes: 0,          // 0 = enero
    vista: "mes",    // "mes" | "semana"
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

  /* Lunes de la semana que contiene la fecha. La vista semanal siempre arranca
     un lunes y termina el viernes: el fin de semana no se dibuja. */
  function lunesDe(d) {
    const c = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    return moverDias(c, -((c.getDay() + 6) % 7));
  }
  function semanaInicioDe(iso) {
    return lunesDe(fechaDe(iso));
  }
  function tituloSemana(inicio) {
    const fin = moverDias(inicio, 4);
    if (inicio.getFullYear() !== fin.getFullYear()) {
      return `${inicio.getDate()} de ${MESES[inicio.getMonth()]} de ${inicio.getFullYear()} ` +
        `al ${fin.getDate()} de ${MESES[fin.getMonth()]} de ${fin.getFullYear()}`;
    }
    if (inicio.getMonth() !== fin.getMonth()) {
      return `${inicio.getDate()} de ${MESES[inicio.getMonth()]} al ` +
        `${fin.getDate()} de ${MESES[fin.getMonth()]} de ${fin.getFullYear()}`;
    }
    return `${inicio.getDate()} al ${fin.getDate()} de ${MESES[fin.getMonth()]} de ${fin.getFullYear()}`;
  }
  function fechaCortaSemana(fecha) {
    return `${fecha.getDate()} ${MESES[fecha.getMonth()].slice(0, 3)}`;
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
  function doctorPorId(id) {
    return doctores().find((d) => String(d.id) === String(id)) || null;
  }
  function pacientePorId(id) {
    const A = app();
    return (A && A.state && A.state.pacientes || []).find(
      (p) => String(p.id) === String(id)
    ) || null;
  }

  /* Todos los turnos del odontólogo filtrado entre el lunes y el viernes de la
     fecha seleccionada. La vista semanal nunca mezcla profesionales: si no hay
     filtro se elige automáticamente el primer odontólogo activo. */
  function turnosSemana() {
    if (!cal.filtro) return [];
    const inicio = semanaInicioDe(fechaSeleccionada());
    const desde = isoDe(inicio);
    const hasta = isoDe(moverDias(inicio, 4));
    return turnos()
      .filter(function (t) {
        const dia = app().parteFecha(t.fecha_hora_inicio);
        return String(t.doctor_id) === String(cal.filtro) && dia >= desde && dia <= hasta;
      })
      .sort((a, b) => a.fecha_hora_inicio.localeCompare(b.fecha_hora_inicio));
  }

  function asegurarDoctorSemana() {
    if (cal.vista !== "semana") return;
    const activos = doctoresActivos();
    if (!activos.length) {
      cal.filtro = "";
      const A = app();
      if (A && A.state.agendaDoctor) {
        A.state.agendaDoctor = "";
        if (typeof A.renderAgenda === "function") A.renderAgenda();
      }
      return;
    }
    if (!cal.filtro || !activos.some((d) => String(d.id) === String(cal.filtro))) {
      cal.filtro = String(activos[0].id);
      const A = app();
      if (A) {
        A.state.agendaDoctor = cal.filtro;
        if (typeof A.renderAgenda === "function") A.renderAgenda();
      }
    }
  }

  function renderSemanaDoctorSelect() {
    const A = app();
    const select = $id("cal-semana-doctor");
    if (!A || !select) return;
    const activos = doctoresActivos();

    if (!activos.length) {
      select.innerHTML = `<option value="">Sin odontólogos activos</option>`;
      select.disabled = true;
      return;
    }

    select.disabled = false;
    select.innerHTML = activos
      .map(function (d) {
        const esp = d.especialidad ? ` · ${A.esc(d.especialidad)}` : "";
        return `<option value="${d.id}">${A.esc(d.nombre)}${esp}</option>`;
      })
      .join("");
    select.value = cal.filtro;
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

  /* --------------------------- Modo de vista ---------------------------- */
  /* «Mes» y «Semana» comparten la barra superior. En la vista semanal se
     esconde la grilla mensual y se muestra el tablero lunes-viernes; el panel
     del día sigue disponible para editar, cancelar o cobrar el turno elegido. */
  function aplicarModoVista() {
    const semana = cal.vista === "semana";
    const mesPanel = $id("cal-mes-panel");
    const semanaPanel = $id("cal-semana-panel");
    const leyenda = $id("cal-leyenda");
    const doctorWrap = $id("cal-semana-doctor-wrap");

    if (mesPanel) mesPanel.classList.toggle("hide", semana);
    if (semanaPanel) semanaPanel.classList.toggle("hide", !semana);
    if (leyenda) leyenda.classList.toggle("hide", semana);
    if (doctorWrap) doctorWrap.classList.toggle("hide", !semana);

    document.querySelectorAll("[data-cal-vista]").forEach(function (btn) {
      const activo = btn.dataset.calVista === cal.vista;
      btn.classList.toggle("activo", activo);
      btn.setAttribute("aria-pressed", activo ? "true" : "false");
    });

    /* La vista semanal siempre muestra un solo odontólogo: en el selector del
       panel del día se oculta «Todos» para no romper esa regla. */
    const doctorDia = $id("agenda-doctor");
    if (doctorDia && doctorDia.options.length > 1) {
      doctorDia.options[0].hidden = semana;
    }
  }

  function resumenTurnos(lista) {
    const total = lista.length;
    const cancelados = lista.filter((t) => t.estado_turno === "CANCELADO").length;
    return (
      `${total} turno${total === 1 ? "" : "s"}` +
      (cancelados ? ` · ${cancelados} cancelado${cancelados === 1 ? "" : "s"}` : "")
    );
  }

  function renderCabecera() {
    const A = app();
    if (!A) return;
    const titulo = $id("cal-mes-titulo");
    const sub = $id("cal-mes-sub");
    const resumen = $id("cal-resumen");
    const prev = $id("cal-prev");
    const next = $id("cal-next");
    const hoy = $id("cal-hoy");
    const semana = cal.vista === "semana";

    if (semana) {
      const inicio = semanaInicioDe(fechaSeleccionada());
      const doctor = doctorFiltrado();
      if (titulo) titulo.textContent = tituloSemana(inicio);
      if (sub) {
        sub.textContent = doctor
          ? `Semana de ${doctor.nombre} · lunes a viernes`
          : "Elegí un odontólogo para ver su semana";
      }
      if (resumen) resumen.textContent = resumenTurnos(turnosSemana());
      if (prev) {
        prev.title = "Semana anterior";
        prev.setAttribute("aria-label", "Semana anterior");
      }
      if (next) {
        next.title = "Semana siguiente";
        next.setAttribute("aria-label", "Semana siguiente");
      }
      if (hoy) hoy.textContent = "Semana actual";
      return;
    }

    if (titulo) titulo.textContent = `${MESES[cal.mes]} de ${cal.anio}`;

    const clave = `${cal.anio}-${String(cal.mes + 1).padStart(2, "0")}`;
    const delMes = filtrados(turnos()).filter(
      (t) => A.parteFecha(t.fecha_hora_inicio).slice(0, 7) === clave
    );
    if (resumen) resumen.textContent = resumenTurnos(delMes);

    const doctor = doctorFiltrado();
    if (sub) {
      sub.textContent = doctor
        ? `Mostrando sólo los turnos de ${doctor.nombre}`
        : "Turnos del mes por odontólogo";
    }
    if (prev) {
      prev.title = "Mes anterior";
      prev.setAttribute("aria-label", "Mes anterior");
    }
    if (next) {
      next.title = "Mes siguiente";
      next.setAttribute("aria-label", "Mes siguiente");
    }
    if (hoy) hoy.textContent = "Mes actual";
  }

  /* --------------------------- Vista semanal ----------------------------- */
  function tarjetaSemana(t) {
    const A = app();
    const paciente = A.nombrePaciente(t.paciente_id);
    const color = A.colorDe(t.doctor_id);
    const cancelado = t.estado_turno === "CANCELADO";
    const confirmado = t.estado_turno === "CONFIRMADO";
    const dur = A.duracionTexto(t.fecha_hora_inicio, t.fecha_hora_fin);
    let clases = "cal-semana-turno";
    if (confirmado) clases += " confirmado";
    if (cancelado) clases += " cancelado";
    const borde = confirmado ? "#15803d" : cancelado ? "#cbd5e1" : color;

    const alterno = cancelado
      ? `<button type="button" class="cal-semana-accion" data-reactivar-turno="${t.id}">Reactivar</button>`
      : `<button type="button" class="cal-semana-accion cal-semana-accion-peligro" data-cancelar-turno="${t.id}">Cancelar</button>`;
    const confirmar =
      t.estado_turno === "PENDIENTE"
        ? `<button type="button" class="cal-semana-accion" data-confirmar-turno="${t.id}">Confirmar</button>`
        : "";

    return (
      `<article class="${clases}" data-semana-turno="${t.id}" style="border-left-color:${borde}">` +
      `<button type="button" class="cal-semana-turno-info" data-ver-turno="${t.id}" ` +
      `title="Ver ${A.esc(paciente)} en el panel del día">` +
      `<span class="cal-semana-hora">${A.esc(A.parteHora(t.fecha_hora_inicio))}` +
      `<small>${A.esc(A.parteHora(t.fecha_hora_fin))}${dur ? ` · ${A.esc(dur)}` : ""}</small></span>` +
      `<span class="cal-semana-paciente">${A.esc(paciente)}</span>` +
      `<span class="cal-semana-badges">${A.badgeTurno(t.estado_turno)}${A.badgeWhatsApp(t)}</span>` +
      `</button>` +
      `<div class="cal-semana-acciones">` +
      `<button type="button" class="cal-semana-accion" data-editar-turno="${t.id}">Editar</button>` +
      confirmar +
      `<button type="button" class="cal-semana-accion" data-cobrar-turno="${t.id}">Cobrar</button>` +
      alterno +
      `</div></article>`
    );
  }

  function renderSemana() {
    const A = app();
    const grid = $id("cal-semana-grid");
    if (!A || !grid) return;

    asegurarDoctorSemana();
    renderSemanaDoctorSelect();
    const doctor = doctorFiltrado();
    if (!doctor) {
      grid.innerHTML = A.vacio(
        "Elegí un odontólogo",
        "La vista semanal muestra la agenda de un solo profesional."
      );
      return;
    }

    const inicio = semanaInicioDe(fechaSeleccionada());
    const mapa = {};
    turnosSemana().forEach(function (t) {
      const iso = A.parteFecha(t.fecha_hora_inicio);
      (mapa[iso] = mapa[iso] || []).push(t);
    });

    const hoy = isoDe(new Date());
    const seleccionado = fechaSeleccionada();
    let html = "";
    for (let i = 0; i < DIAS_VISTA.length; i += 1) {
      const fecha = moverDias(inicio, i);
      const iso = isoDe(fecha);
      const lista = mapa[iso] || [];
      const clases = ["cal-semana-dia"];
      if (iso === hoy) clases.push("hoy");
      if (iso === seleccionado) clases.push("seleccionado");
      const conteo =
        `${lista.length} turno${lista.length === 1 ? "" : "s"}`;
      const etiquetaImpresion = `Imprimir la agenda del ${A.fechaLarga(iso)}`;

      html +=
        `<section class="${clases.join(" ")}" data-semana-dia="${iso}" ` +
        `aria-label="${A.esc(DIAS_VISTA[i])} ${A.esc(A.fechaLarga(iso))}: ${A.esc(conteo)}">` +
        `<header class="cal-semana-cabecera">` +
        `<div><p class="cal-semana-dia-nombre">${A.esc(DIAS_VISTA[i])}</p>` +
        `<p class="cal-semana-dia-fecha">${A.esc(fechaCortaSemana(fecha))}</p></div>` +
        `<div class="cal-semana-cabecera-acciones">` +
        `<span class="cal-semana-conteo">${lista.length}</span>` +
        `<button type="button" class="cal-semana-imprimir" data-imprimir-dia="${iso}" ` +
        `title="${A.esc(etiquetaImpresion)}" aria-label="${A.esc(etiquetaImpresion)}">` +
        ICONO_IMPRESORA + `</button>` +
        `</div>` +
        `</header>` +
        `<div class="cal-semana-lista">` +
        (lista.length
          ? lista.map(tarjetaSemana).join("")
          : `<p class="cal-semana-vacio">Sin turnos</p>`) +
        `</div></section>`;
    }
    grid.innerHTML = html;

    /* El selector del panel del día y el de la semana son la misma agenda: se
       sincronizan acá por si el odontólogo se eligió automáticamente. */
    if (A.state.agendaDoctor !== cal.filtro) {
      A.state.agendaDoctor = cal.filtro;
      if (typeof A.renderAgenda === "function") A.renderAgenda();
    }
    const doctorDia = $id("agenda-doctor");
    if (doctorDia && doctorDia.value !== cal.filtro) doctorDia.value = cal.filtro;
  }

  /* ------------------- Impresión de la agenda del día -------------------- */
  /* La hoja se arma siempre con la fecha que se está imprimiendo y el filtro
     recibido por parámetro: el botón del panel usa la fecha y el odontólogo
     elegidos, y cada columna de la vista semanal pasa su propio día. */
  function resumenEstados(lista) {
    const confirmados = lista.filter((t) => t.estado_turno === "CONFIRMADO").length;
    const pendientes = lista.filter((t) => t.estado_turno === "PENDIENTE").length;
    const cancelados = lista.filter((t) => t.estado_turno === "CANCELADO").length;
    const partes = [];
    if (confirmados) partes.push(`${confirmados} confirmado${confirmados === 1 ? "" : "s"}`);
    if (pendientes) partes.push(`${pendientes} pendiente${pendientes === 1 ? "" : "s"}`);
    if (cancelados) partes.push(`${cancelados} cancelado${cancelados === 1 ? "" : "s"}`);
    return partes.length ? partes.join(" · ") : "Sin turnos";
  }

  function gruposImpresion(iso, filtro) {
    const A = app();
    const todos = turnosDelDia(iso);
    if (filtro) {
      const propios = todos.filter((t) => String(t.doctor_id) === String(filtro));
      const doctor = doctorPorId(filtro) || {
        id: filtro,
        nombre: A.nombreDoctor(filtro),
        especialidad: "",
      };
      return [{ doctor: doctor, turnos: propios }];
    }

    const grupos = [];
    doctores().forEach(function (d) {
      const propios = todos.filter((t) => String(t.doctor_id) === String(d.id));
      if (propios.length) grupos.push({ doctor: d, turnos: propios });
    });
    // Turnos de un profesional dado de baja que todavía tiene agenda: no se
    // pierden al imprimir, aunque ya no esté en el selector.
    todos.forEach(function (t) {
      const id = String(t.doctor_id);
      if (grupos.some((g) => String(g.doctor.id) === id)) return;
      const doctor = doctorPorId(id) || {
        id: t.doctor_id,
        nombre: A.nombreDoctor(t.doctor_id),
        especialidad: "",
      };
      grupos.push({
        doctor: doctor,
        turnos: todos.filter((x) => String(x.doctor_id) === id),
      });
    });
    return grupos;
  }

  function filaImpresion(t) {
    const A = app();
    const paciente = pacientePorId(t.paciente_id);
    const nombre = paciente ? paciente.nombre : A.nombrePaciente(t.paciente_id);
    const dni = paciente && paciente.dni ? paciente.dni : "—";
    const telefono = paciente && paciente.telefono ? paciente.telefono : "—";
    const obra = paciente
      ? (paciente.obra_social ||
          (paciente.obra_social_id ? A.nombreObraSocial(paciente.obra_social_id) : ""))
      : "";
    const afiliado =
      paciente && paciente.numero_afiliado
        ? `<small>Nº ${A.esc(paciente.numero_afiliado)}</small>`
        : "";
    const edad =
      paciente && paciente.edad !== null && paciente.edad !== undefined
        ? `<small>${A.esc(paciente.edad)} años</small>`
        : "";
    const duracion = A.duracionTexto(t.fecha_hora_inicio, t.fecha_hora_fin);
    const estado =
      ESTADO_IMPRESION[t.estado_turno] ||
      { texto: t.estado_turno || "—", clase: "pendiente" };
    const cancelado = t.estado_turno === "CANCELADO" ? " cancelado" : "";

    return (
      `<tr class="agenda-impresion-fila${cancelado}">` +
      `<td><strong>${A.esc(A.parteHora(t.fecha_hora_inicio))}</strong>` +
      (duracion ? `<small>${A.esc(duracion)}</small>` : "") +
      `</td>` +
      `<td><strong>${A.esc(nombre)}</strong>${edad}</td>` +
      `<td>${A.esc(dni)}</td>` +
      `<td>${A.esc(telefono)}</td>` +
      `<td>${obra ? `<strong>${A.esc(obra)}</strong>` : `<span class="agenda-impresion-tenue">—</span>`}` +
      `${afiliado}</td>` +
      `<td><span class="agenda-impresion-estado ${estado.clase}">${A.esc(estado.texto)}</span></td>` +
      `</tr>`
    );
  }

  function bloqueImpresion(grupo) {
    const A = app();
    const doctor = grupo.doctor || {};
    const total = grupo.turnos.length;
    const detalle = [
      doctor.especialidad || "",
      resumenEstados(grupo.turnos),
    ].filter(Boolean).join(" · ");

    return (
      `<section class="agenda-impresion-bloque">` +
      `<header class="agenda-impresion-bloque-cab">` +
      `<div><h2>${A.esc(doctor.nombre || "Odontólogo")}</h2>` +
      `<p>${A.esc(detalle)}</p></div>` +
      `<span class="agenda-impresion-bloque-total">${total} turno${total === 1 ? "" : "s"}</span>` +
      `</header>` +
      (total
        ? `<table class="agenda-impresion-tabla">` +
          `<thead><tr><th>Hora</th><th>Paciente</th><th>DNI</th>` +
          `<th>Teléfono</th><th>Cobertura</th><th>Estado</th></tr></thead>` +
          `<tbody>${grupo.turnos.map(filaImpresion).join("")}</tbody></table>`
        : `<p class="agenda-impresion-vacio">Sin turnos agendados para este profesional.</p>`) +
      `</section>`
    );
  }

  function documentoImpresion(iso, filtro) {
    const A = app();
    if (!A) return "";

    const grupos = gruposImpresion(iso, filtro);
    const total = grupos.reduce((suma, g) => suma + g.turnos.length, 0);
    const cancelados = grupos.reduce(
      (suma, g) => suma + g.turnos.filter((t) => t.estado_turno === "CANCELADO").length,
      0
    );
    const doctor = filtro ? doctorPorId(filtro) : null;
    const profesional = filtro
      ? (doctor
          ? `${doctor.nombre}${doctor.especialidad ? ` · ${doctor.especialidad}` : ""}`
          : `Odontólogo #${filtro}`)
      : "Todos los odontólogos";
    const emitido = new Date().toLocaleString("es-AR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
    const nota = cancelados
      ? `<p class="agenda-impresion-nota">Los turnos cancelados se muestran atenuados y ` +
        `siguen en la hoja para conservar el historial del día.</p>`
      : "";
    const bloques = grupos.length
      ? grupos.map(bloqueImpresion).join("")
      : `<p class="agenda-impresion-vacio">No hay turnos agendados para esta fecha` +
        `${filtro ? " con el profesional seleccionado" : ""}.</p>`;

    return (
      `<header class="agenda-impresion-cab">` +
      `<div class="agenda-impresion-marca">` +
      `<svg class="agenda-impresion-logo" viewBox="0 0 140 180" role="img" aria-label="Franciosi Odontología">` +
      `<g fill="none" stroke="#c99a96" stroke-width="14" stroke-linecap="round" stroke-linejoin="round">` +
      `<path d="M66 47C66 24 80 9 101 9c15 0 27 8 27 21 0 8-4 14-11 15"/>` +
      `<path d="M66 47v99"/><path d="M42 154c14 0 23-6 26-16v16h26"/></g>` +
      `<circle cx="117" cy="45" r="9" fill="#c99a96"/>` +
      `<circle cx="101" cy="103" r="9" fill="#c99a96"/></svg>` +
      `<div><p class="agenda-impresion-clinica">Franciosi Odontología</p>` +
      `<p class="agenda-impresion-lema">Agenda diaria del consultorio</p></div></div>` +
      `<div class="agenda-impresion-emitido"><span>Emitido</span>` +
      `<strong>${A.esc(emitido)}</strong></div>` +
      `</header>` +
      `<section class="agenda-impresion-titulo">` +
      `<div><p class="agenda-impresion-kicker">Agenda del día</p>` +
      `<h1>${A.esc(A.fechaLarga(iso))}</h1>` +
      `<p class="agenda-impresion-profesional">${A.esc(profesional)}</p></div>` +
      `<div class="agenda-impresion-total"><strong>${total}</strong>` +
      `<span>turno${total === 1 ? "" : "s"}</span></div>` +
      `</section>` +
      nota +
      `<div class="agenda-impresion-bloques">${bloques}</div>` +
      `<footer class="agenda-impresion-pie">` +
      `<span>Panel Principal · Franciosi Odontología</span>` +
      `<span>Agenda generada el ${A.esc(emitido)}</span>` +
      `</footer>` +
      `<div class="agenda-impresion-firma"><span>Firma y sello</span></div>`
    );
  }

  function imprimirDia(iso, filtro) {
    const A = app();
    if (!A) return;
    const fecha = /^\d{4}-\d{2}-\d{2}$/.test(String(iso || ""))
      ? String(iso)
      : fechaSeleccionada();
    const doctorId = filtro === null || filtro === undefined ? "" : String(filtro);

    let hoja = $id("agenda-impresion");
    if (!hoja) {
      hoja = document.createElement("div");
      hoja.id = "agenda-impresion";
      hoja.className = "agenda-impresion";
      hoja.setAttribute("aria-hidden", "true");
      document.body.appendChild(hoja);
    }
    hoja.innerHTML = documentoImpresion(fecha, doctorId);

    // El nombre que el navegador propone para el PDF sale del <title>.
    const tituloOriginal = document.title;
    const doctor = doctorId ? doctorPorId(doctorId) : null;
    document.title =
      `Agenda ${doctor ? `de ${doctor.nombre}` : "del día"} · ${fecha}`;
    const restaurarTitulo = function () { document.title = tituloOriginal; };
    window.addEventListener("afterprint", restaurarTitulo, { once: true });

    ocultarTooltip();
    window.print();
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

  /* Por qué un bloque de "Horarios disponibles" no se puede agendar. Se usa en
     el `title` del botón y en el aviso que aparece al hacerle clic: un bloque
     gris que no reacciona deja a la secretaría sin saber qué pasó. */
  const MOTIVO_TITULO = {
    pasado: "Ese horario ya pasó",
    ocupado: "Ya hay un turno en ese horario",
    fuera: "Ningún odontólogo atiende en ese horario",
  };
  const MOTIVO_DETALLE = {
    pasado: "Hoy esa hora ya pasó. Elegí un bloque libre o cambiá de día.",
    ocupado: "Podés cancelarlo o moverlo desde «Turnos ocupados».",
    fuera: "La agenda semanal de los odontólogos no cubre ese bloque.",
  };

  function avisarBloqueNoDisponible(btn) {
    const A = app();
    if (!A || typeof A.toast !== "function") return;
    const motivo = btn.dataset.motivo || "fuera";
    const titulo = MOTIVO_TITULO[motivo] || "Bloque no disponible";
    A.toast("info", `${titulo} (${btn.dataset.slot})`, MOTIVO_DETALLE[motivo] || "");
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

    const botones = slots
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

        /* Motivo del bloque gris: se muestra en el texto, en el `title` y en el
           aviso del clic. Sin esto el botón no dice *por qué* no se puede
           agendar y el usuario no sabe si el panel está roto. */
        let motivo = "";
        if (paso) motivo = "pasado";
        else if (!libresDocs.length) motivo = ocupados.length ? "ocupado" : "fuera";

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
        } else if (motivo === "ocupado") {
          marcas = `<span class="text-[11px] text-slate-400">Ocupado</span>`;
        } else {
          marcas = `<span class="text-[11px] text-slate-400">Fuera de agenda</span>`;
        }

        return (
          `<button type="button" class="cal-slot${deshabilitado ? " cal-slot-off" : ""}" ` +
          `data-slot="${hhmm(slot.inicio)}" ` +
          `data-doctor="${libresDocs.length ? libresDocs[0].id : ""}" ` +
          (motivo ? `data-motivo="${motivo}" ` : "") +
          `aria-disabled="${deshabilitado ? "true" : "false"}" ` +
          `title="${deshabilitado ? MOTIVO_TITULO[motivo] : "Agendar a las " + hhmm(slot.inicio)}">` +
          `<span class="cal-slot-hora">${hhmm(slot.inicio)}` +
          `<small>a ${hhmm(slot.fin)}</small></span>` +
          `<span class="cal-slot-docs">${marcas}</span>` +
          `</button>`
        );
      })
      .join("");

    /* Cuando no queda ningún bloque libre se dice arriba de la lista, con el
       atajo al formulario: el día puede estar lleno o fuera de la agenda y la
       secretaría tiene que poder pedir el turno igual. */
    const aviso =
      libres === 0
        ? `<div class="cal-aviso">` +
          `<p>${diaTerminado(iso, slots)
            ? "Los horarios de este día ya pasaron."
            : "Ningún horario libre con la agenda actual o con los turnos ya cargados."}</p>` +
          `<button type="button" class="cal-aviso-btn" data-nuevo-turno="1">` +
          `Solicitar el turno a mano</button>` +
          `</div>`
        : "";

    cont.innerHTML = aviso + botones;
    if (resumen) resumen.textContent = String(libres);
  }

  /* ¿Todos los bloques del día son de horas ya pasadas? Distingue un día
     terminado de un día lleno en el aviso del panel. */
  function diaTerminado(iso, slots) {
    const ahora = Date.now();
    return slots.every(
      (slot) => new Date(`${iso}T${hhmm(slot.inicio)}:00`).getTime() <= ahora
    );
  }

  /* --------------------- Selección de día y de mes ----------------------- */
  /* Estado del día activo: `estado.agendaFecha` es la única fuente de verdad,
     así el calendario y la lista de turnos de app.js quedan siempre iguales. */
  function render() {
    if (!app()) return;
    asegurarDoctorSemana();
    aplicarModoVista();
    const d = fechaDe(fechaSeleccionada());
    cal.anio = d.getFullYear();
    cal.mes = d.getMonth();
    renderCalendario();
    asegurarDoctorSemana();
    renderSemana();
    renderDisponibles();
    renderCabecera();
  }

  function seleccionarDia(iso) {
    const A = app();
    if (!A || !iso) return;
    ocultarTooltip();
    A.state.agendaFecha = iso;
    if (typeof A.renderAgenda === "function") A.renderAgenda();
    render();
  }

  function moverMes(n) {
    const d = new Date(cal.anio, cal.mes + n, 1);
    cal.anio = d.getFullYear();
    cal.mes = d.getMonth();
    ocultarTooltip();
    renderCalendario();
    renderCabecera();
  }

  /* La barra superior mueve el período activo: un mes o una semana completa.
     En la vista semanal se mueve también el día seleccionado para que el panel
     inferior siga mostrando una fecha de la semana visible. */
  function moverPeriodo(n) {
    if (cal.vista === "semana") {
      seleccionarDia(isoDe(moverDias(fechaDe(fechaSeleccionada()), n * 7)));
      return;
    }
    moverMes(n);
  }

  function moverSemana(n) {
    if (cal.vista !== "semana") return;
    seleccionarDia(isoDe(moverDias(fechaDe(fechaSeleccionada()), n * 7)));
  }

  function irAlMesActual() { seleccionarDia(isoDe(new Date())); }

  function cambiarVistaCalendario(vista) {
    const nueva = vista === "semana" ? "semana" : "mes";
    if (cal.vista === nueva) return;
    cal.vista = nueva;
    ocultarTooltip();
    if (nueva === "semana") asegurarDoctorSemana();
    render();
  }

  /* -------------------- Filtro por odontólogo (chips) -------------------- */
  /* `cal.filtro` es la única fuente de verdad: los chips de la leyenda y el
     selector "Odontólogo" del panel del día (que renderiza app.js) lo fijan
     desde acá, y los tres paneles del calendario se redibujan en consecuencia. */
  function cambiarFiltro(id) {
    const A = app();
    let valor = id ? String(id) : "";
    const activos = doctoresActivos();

    /* La vista semanal es siempre de UN odontólogo: si no hay selección (o el
       profesional ya no está activo) se elige el primero disponible. */
    if (cal.vista === "semana" && (!valor || !activos.some((d) => String(d.id) === valor))) {
      valor = activos.length ? String(activos[0].id) : "";
    }

    cal.filtro = valor;
    if (A) {
      A.state.agendaDoctor = cal.filtro;
      const select = $id("agenda-doctor");
      if (select && select.value !== cal.filtro) select.value = cal.filtro;
      if (typeof A.renderAgenda === "function") A.renderAgenda();
    }
    ocultarTooltip();
    render();
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
    // El aviso de paciente MALO depende del paciente precargado.
    if (typeof A.actualizarAlertaPacienteTurno === "function") {
      A.actualizarAlertaPacienteTurno();
    }
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
    if (typeof A.actualizarAlertaPacienteTurno === "function") {
      A.actualizarAlertaPacienteTurno();
    }
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

  function eventosSemana() {
    const grid = $id("cal-semana-grid");
    if (!grid) return;

    grid.addEventListener("click", function (e) {
      const ver = e.target.closest("[data-ver-turno]");
      if (ver) {
        const t = turnoPorId(ver.dataset.verTurno);
        if (t) seleccionarDia(app().parteFecha(t.fecha_hora_inicio));
        return;
      }
      // Los botones de acción (editar/cancelar/cobrar) los resuelve la
      // delegación global; el resto de la tarjeta sólo selecciona el día.
      if (e.target.closest("button")) return;
      const dia = e.target.closest("[data-semana-dia]");
      if (dia) seleccionarDia(dia.dataset.semanaDia);
    });
  }

  function eventosPanel() {
    document.querySelectorAll("[data-cal-vista]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        cambiarVistaCalendario(btn.dataset.calVista);
      });
    });

    const semanaDoctor = $id("cal-semana-doctor");
    if (semanaDoctor) {
      semanaDoctor.addEventListener("change", function () {
        cambiarFiltro(semanaDoctor.value);
      });
    }

    const imprimirPanel = $id("agenda-imprimir");
    if (imprimirPanel) {
      imprimirPanel.addEventListener("click", function () {
        const select = $id("agenda-doctor");
        const filtro = select
          ? select.value
          : (app() && app().state.agendaDoctor) || cal.filtro;
        imprimirDia(fechaSeleccionada(), filtro);
      });
    }

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
        /* Atajo del aviso "sin horarios libres": abre el formulario igual, con
           la fecha del día y el odontólogo del filtro ya cargados. */
        const aMano = e.target.closest("[data-nuevo-turno]");
        if (aMano) {
          const A = app();
          if (A && typeof A.abrirModal === "function") A.abrirModal();
          return;
        }
        const btn = e.target.closest("[data-slot]");
        if (!btn) return;
        /* El bloque gris no agenda, pero tampoco queda mudo: explica el motivo
           (hora pasada, ocupado o fuera de agenda). */
        if (btn.getAttribute("aria-disabled") === "true") {
          avisarBloqueNoDisponible(btn);
          return;
        }
        agendarEnSlot(btn.dataset.slot, btn.dataset.doctor);
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
      const imprimir = e.target.closest("[data-imprimir-dia]");
      if (imprimir) {
        imprimirDia(imprimir.dataset.imprimirDia, cal.filtro);
      }
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
    eventosSemana();
    eventosPanel();
    eventosAcciones();
    eventosSincronizacion();

    const prev = $id("cal-prev");
    if (prev) prev.addEventListener("click", function () { moverPeriodo(-1); });
    const next = $id("cal-next");
    if (next) next.addEventListener("click", function () { moverPeriodo(1); });
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
    renderSemana: renderSemana,
    renderDisponibles: renderDisponibles,
    seleccionarDia: seleccionarDia,
    irAlMesActual: irAlMesActual,
    moverMes: moverMes,
    moverSemana: moverSemana,
    moverPeriodo: moverPeriodo,
    cambiarVista: cambiarVistaCalendario,
    cambiarFiltro: cambiarFiltro,
    filtroActual: function () { return cal.filtro; },
    vistaActual: function () { return cal.vista; },
    abrirEdicion: abrirEdicion,
    cambiarEstado: cambiarEstado,
    // Impresión de la agenda: el panel imprime la fecha/odontólogo elegidos y
    // cada columna de la semana imprime su propio día.
    imprimirDia: imprimirDia,
    documentoImpresion: documentoImpresion,
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
