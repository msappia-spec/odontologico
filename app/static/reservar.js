/* ==========================================================================
   Portal público de turnos — Franciosi Odontología
   Flujo de 4 pasos contra /api/v1/portal-turnos. Sin frameworks: el celular
   del paciente no debe depender de una SPA para sacar un turno.
   ========================================================================== */
(function () {
  "use strict";

  var API = "/api/v1/portal-turnos";
  var DIAS_CORTOS = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"];
  var MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
  ];

  var state = {
    doctores: [],
    obras: [],
    doctor: null,
    fecha: "",
    horarios: [],
    hora: "",
    paciente: null,   // resultado de /buscar-paciente para el DNI consultado
    dni: "",          // último DNI buscado (para detectar cambios)
    reserva: null,
    paso: 1,
  };

  function $(id) {
    return document.getElementById(id);
  }

  function esc(valor) {
    return String(valor == null ? "" : valor)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function soloDigitos(valor) {
    return String(valor || "").replace(/\D+/g, "");
  }

  /* Fechas locales (no UTC): el paciente ve el día de su calendario. */
  function isoLocal(fecha) {
    var anio = fecha.getFullYear();
    var mes = String(fecha.getMonth() + 1).padStart(2, "0");
    var dia = String(fecha.getDate()).padStart(2, "0");
    return anio + "-" + mes + "-" + dia;
  }

  function desdeIso(texto) {
    var partes = String(texto).split("-").map(Number);
    return new Date(partes[0], partes[1] - 1, partes[2]);
  }

  function sumarDias(fecha, dias) {
    var copia = new Date(fecha.getFullYear(), fecha.getMonth(), fecha.getDate());
    copia.setDate(copia.getDate() + dias);
    return copia;
  }

  function fechaLarga(texto) {
    if (!texto) return "";
    var fecha = texto.length > 10 ? new Date(texto) : desdeIso(texto);
    if (isNaN(fecha.getTime())) return texto;
    return (
      DIAS_CORTOS[fecha.getDay()].toLowerCase() + " " + fecha.getDate() +
      " de " + MESES[fecha.getMonth()] + " de " + fecha.getFullYear()
    );
  }

  function mostrarError(mensaje) {
    var caja = $("rp-error");
    if (!caja) return;
    if (!mensaje) {
      caja.classList.add("hide");
      $("rp-error-texto").textContent = "";
      return;
    }
    $("rp-error-texto").textContent = mensaje;
    caja.classList.remove("hide");
  }

  function api(ruta, opciones) {
    var config = opciones || {};
    config.headers = Object.assign({ Accept: "application/json" }, config.headers || {});
    if (config.body) config.headers["Content-Type"] = "application/json";
    return fetch(API + ruta, config).then(function (respuesta) {
      return respuesta.json().catch(function () { return null; }).then(function (datos) {
        if (respuesta.ok) return datos;
        var detalle = datos && datos.detail;
        var mensaje = "No pudimos completar la solicitud. Intentá de nuevo.";
        if (Array.isArray(detalle)) {
          mensaje = detalle.map(function (item) { return item.msg || ""; }).filter(Boolean).join(" ");
        } else if (typeof detalle === "string" && detalle) {
          mensaje = detalle;
        }
        var error = new Error(mensaje);
        error.status = respuesta.status;
        error.datos = datos;
        throw error;
      });
    });
  }

  /* ------------------------------- Pasos --------------------------------- */
  function mostrarPaso(paso) {
    state.paso = paso;
    [1, 2, 3, 4].forEach(function (numero) {
      var seccion = $("rp-paso-" + numero);
      if (seccion) seccion.classList.toggle("hide", numero !== paso);
    });
    document.querySelectorAll("[data-paso-indicador]").forEach(function (item) {
      var numero = Number(item.dataset.pasoIndicador);
      item.classList.toggle("activo", numero === paso);
      item.classList.toggle("hecho", numero < paso);
    });
    mostrarError("");
    var destino = $("rp-paso-" + paso) || $("rp-titulo");
    if (destino && destino.scrollIntoView) {
      destino.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  /* --------------------------- Catálogos iniciales ------------------------ */
  function iniciales(nombre) {
    var palabras = String(nombre || "").replace(/^(Dr\.?|Dra\.?)\s*/i, "").trim().split(/\s+/);
    var letras = palabras.slice(0, 2).map(function (p) { return p.charAt(0); }).join("");
    return (letras || "DR").toUpperCase();
  }

  function renderDoctores() {
    var cont = $("rp-doctores");
    cont.innerHTML = "";
    if (!state.doctores.length) {
      cont.innerHTML = '<div class="rp-vacio">No hay profesionales disponibles en este momento. Escribinos por WhatsApp al consultorio.</div>';
      return;
    }
    state.doctores.forEach(function (doctor) {
      var elegido = state.doctor && state.doctor.id === doctor.id;
      var boton = document.createElement("button");
      boton.type = "button";
      boton.className = "rp-doctor" + (elegido ? " seleccionado" : "");
      boton.setAttribute("aria-pressed", elegido ? "true" : "false");
      boton.innerHTML =
        '<span class="rp-doctor-avatar" aria-hidden="true">' + esc(iniciales(doctor.nombre)) + "</span>" +
        '<span class="rp-doctor-info"><strong>' + esc(doctor.nombre) + "</strong><span>" + esc(doctor.especialidad) + "</span></span>" +
        '<span class="rp-doctor-check" aria-hidden="true">✓</span>';
      boton.addEventListener("click", function () {
        seleccionarDoctor(doctor);
      });
      cont.appendChild(boton);
    });
  }

  function seleccionarDoctor(doctor) {
    state.doctor = doctor;
    state.hora = "";
    renderDoctores();
    $("rp-1-siguiente").disabled = false;
    $("rp-doctor-elegido").textContent = doctor.nombre + " · " + doctor.especialidad;
  }

  function renderObras(seleccionada) {
    var select = $("rp-obra-social");
    select.innerHTML = '<option value="">Particular / sin obra social</option>';
    state.obras.forEach(function (obra) {
      var opcion = document.createElement("option");
      opcion.value = String(obra.id);
      opcion.textContent = obra.nombre;
      select.appendChild(opcion);
    });
    if (seleccionada && !state.obras.some(function (o) { return String(o.id) === String(seleccionada); })) {
      // La cobertura actual del paciente puede estar dada de baja en el catálogo:
      // se muestra igual para no cambiarla sin que la persona lo decida.
      var actual = document.createElement("option");
      actual.value = String(seleccionada);
      actual.textContent = "Cobertura actual (no disponible en el listado)";
      select.appendChild(actual);
    }
    select.value = seleccionada ? String(seleccionada) : "";
  }

  function cargarInicial() {
    return api("/inicial").then(function (datos) {
      state.doctores = (datos && datos.doctores) || [];
      state.obras = (datos && datos.obras_sociales) || [];
      renderDoctores();
      renderObras(null);
    }).catch(function (error) {
      $("rp-doctores").innerHTML = '<div class="rp-vacio">No pudimos cargar los profesionales.</div>';
      mostrarError(error.message);
    });
  }

  /* ------------------------------ Fecha y hora ---------------------------- */
  function renderDias() {
    var cont = $("rp-dias");
    cont.innerHTML = "";
    var hoy = new Date();
    for (var i = 0; i < 10; i += 1) {
      var fecha = sumarDias(hoy, i);
      var iso = isoLocal(fecha);
      var boton = document.createElement("button");
      boton.type = "button";
      boton.className = "rp-dia" + (state.fecha === iso ? " seleccionado" : "");
      boton.setAttribute("aria-pressed", state.fecha === iso ? "true" : "false");
      boton.setAttribute("aria-label", fechaLarga(iso));
      boton.innerHTML = "<small>" + esc(i === 0 ? "Hoy" : DIAS_CORTOS[fecha.getDay()]) + "</small>" +
        "<strong>" + esc(String(fecha.getDate())) + "</strong>" +
        "<small>" + esc(MESES[fecha.getMonth()].slice(0, 3)) + "</small>";
      boton.addEventListener("click", function (valor) {
        return function () {
          state.fecha = valor;
          $("rp-fecha").value = valor;
          state.hora = "";
          $("rp-2-siguiente").disabled = true;
          renderDias();
          cargarHorarios();
        };
      }(iso));
      cont.appendChild(boton);
    }
  }

  function renderHorarios() {
    var cont = $("rp-horarios");
    cont.innerHTML = "";
    if (!state.horarios.length) {
      cont.innerHTML = '<div class="rp-vacio">No quedan horarios libres para este día.<br>Probá con otra fecha de la tira de arriba.</div>';
      return;
    }
    state.horarios.forEach(function (hora) {
      var boton = document.createElement("button");
      boton.type = "button";
      boton.className = "rp-hora" + (state.hora === hora ? " seleccionado" : "");
      boton.textContent = hora;
      boton.setAttribute("aria-pressed", state.hora === hora ? "true" : "false");
      boton.addEventListener("click", function () {
        state.hora = hora;
        renderHorarios();
        $("rp-2-siguiente").disabled = false;
      });
      cont.appendChild(boton);
    });
  }

  function cargarHorarios() {
    if (!state.doctor || !state.fecha) return;
    $("rp-horarios").innerHTML = '<p class="rp-cargando">Buscando horarios libres…</p>';
    $("rp-2-siguiente").disabled = true;
    return api(
      "/disponibilidad?doctor_id=" + encodeURIComponent(state.doctor.id) +
      "&fecha=" + encodeURIComponent(state.fecha)
    ).then(function (datos) {
      state.horarios = (datos && datos.horarios) || [];
      renderHorarios();
    }).catch(function (error) {
      state.horarios = [];
      $("rp-horarios").innerHTML = '<div class="rp-vacio">' + esc(error.message) + "</div>";
      mostrarError(error.message);
    });
  }

  /* -------------------------------- Paso 1/2 ------------------------------- */
  function irAlPaso2() {
    if (!state.doctor) {
      mostrarError("Elegí un profesional para continuar.");
      return;
    }
    if (!state.fecha) {
      state.fecha = isoLocal(new Date());
    }
    $("rp-fecha").min = isoLocal(new Date());
    $("rp-fecha").value = state.fecha;
    $("rp-doctor-elegido").textContent = state.doctor.nombre + " · " + state.doctor.especialidad;
    renderDias();
    mostrarPaso(2);
    cargarHorarios();
  }

  function irAlPaso3() {
    if (!state.hora) {
      mostrarError("Elegí un horario para continuar.");
      return;
    }
    actualizarResumen();
    mostrarPaso(3);
    setTimeout(function () { $("rp-dni").focus(); }, 250);
  }

  /* ------------------------------- Paciente -------------------------------- */
  function normalizarDni(valor) {
    return String(valor || "").replace(/[\s.\-]/g, "");
  }

  function actualizarResumen() {
    var caja = $("rp-resumen-elegido");
    if (!caja || !state.doctor || !state.fecha || !state.hora) return;
    caja.innerHTML =
      "Vas a reservar con <strong>" + esc(state.doctor.nombre) + "</strong><br>" +
      "<strong>" + esc(fechaLarga(state.fecha)) + "</strong> a las <strong>" + esc(state.hora) + " hs</strong>";
  }

  function ocultarInfoPaciente() {
    var caja = $("rp-paciente-info");
    caja.classList.add("hide");
    caja.innerHTML = "";
  }

  function mostrarInfoPaciente(datos) {
    var caja = $("rp-paciente-info");
    if (datos.nuevo) {
      caja.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>' +
        "<div><strong>Primera vez en el consultorio</strong><small>Completá tu nombre y apellido para crear tu ficha.</small></div>";
    } else {
      caja.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 4 4L19 6"/></svg>' +
        "<div><strong>Hola, " + esc(datos.nombre || "paciente") + "</strong>" +
        "<small>" + (datos.obra_social ? "Obra social: " + esc(datos.obra_social) + " · " : "") +
        "WhatsApp registrado: " + esc(datos.telefono_enmascarado || "***") + "</small>" +
        "<small>Actualizá tu WhatsApp si cambió y elegí la cobertura de esta consulta.</small></div>";
    }
    caja.classList.remove("hide");
  }

  function buscarDni() {
    var documento = normalizarDni($("rp-dni").value);
    if (!/^[0-9A-Za-z]{5,20}$/.test(documento)) {
      mostrarError("Ingresá un DNI válido (entre 5 y 20 caracteres, sin puntos).");
      return Promise.resolve();
    }
    mostrarError("");
    var boton = $("rp-buscar-dni");
    boton.disabled = true;
    boton.textContent = "…";
    return api("/buscar-paciente?dni=" + encodeURIComponent(documento))
      .then(function (datos) {
        state.paciente = datos;
        state.dni = documento;
        $("rp-dni").value = documento;
        mostrarInfoPaciente(datos);
        if (datos.nuevo) {
          $("rp-datos-nombre").classList.remove("hide");
          $("rp-nombre").value = "";
          $("rp-apellido").value = "";
          renderObras(null);
          $("rp-telefono-ayuda").textContent = "Con código de área. Ahí te llega la confirmación del turno.";
        } else {
          $("rp-datos-nombre").classList.add("hide");
          $("rp-nombre").value = datos.nombre || "";
          $("rp-apellido").value = "";
          renderObras(datos.obra_social_id);
          $("rp-telefono").value = "";
          $("rp-telefono-ayuda").textContent =
            "Ingresá tu WhatsApp actual para avisarte del turno (reemplaza al registrado).";
        }
      })
      .catch(function (error) {
        state.paciente = null;
        state.dni = "";
        ocultarInfoPaciente();
        $("rp-datos-nombre").classList.remove("hide");
        mostrarError(error.message);
      })
      .then(function () {
        boton.disabled = false;
        boton.textContent = "Buscar";
      });
  }

  function alCambiarDni() {
    var documento = normalizarDni($("rp-dni").value);
    if (state.paciente && documento !== state.dni) {
      state.paciente = null;
      state.dni = "";
      ocultarInfoPaciente();
      $("rp-datos-nombre").classList.remove("hide");
      renderObras(null);
      $("rp-telefono-ayuda").textContent = "Con código de área. Ahí te llega la confirmación del turno.";
    }
  }

  /* --------------------------------- Reserva ------------------------------- */
  function armarPayload(dni) {
    var obra = $("rp-obra-social").value;
    return {
      dni: dni,
      nombre: $("rp-nombre").value.trim(),
      apellido: $("rp-apellido").value.trim(),
      telefono_whatsapp: $("rp-telefono").value.trim(),
      obra_social_id: obra ? Number(obra) : null,
      doctor_id: state.doctor.id,
      fecha_hora: state.fecha + "T" + state.hora + ":00",
      motivo_consulta: $("rp-motivo").value.trim() || null,
    };
  }

  function reservar(evento) {
    evento.preventDefault();
    mostrarError("");
    if (!state.doctor || !state.fecha || !state.hora) {
      mostrarError("Faltan el profesional, la fecha o el horario. Volvé al paso anterior.");
      return;
    }

    var dni = normalizarDni($("rp-dni").value);
    if (!/^[0-9A-Za-z]{5,20}$/.test(dni)) {
      mostrarError("Ingresá un DNI válido (entre 5 y 20 caracteres, sin puntos).");
      $("rp-dni").focus();
      return;
    }
    if (!state.paciente || state.dni !== dni) {
      state.paciente = null;
      $("rp-datos-nombre").classList.remove("hide");
    }
    if (!state.paciente && (!$("rp-nombre").value.trim() || !$("rp-apellido").value.trim())) {
      mostrarError("Completá tu nombre y apellido: es la primera vez que te registrás.");
      $("rp-nombre").focus();
      return;
    }
    if (soloDigitos($("rp-telefono").value).length < 6) {
      mostrarError("Ingresá un WhatsApp válido (al menos 6 dígitos).");
      $("rp-telefono").focus();
      return;
    }

    var payload = armarPayload(dni);
    var boton = $("rp-reservar");
    boton.disabled = true;
    boton.textContent = "Reservando…";
    api("/reservar", { method: "POST", body: JSON.stringify(payload) })
      .then(function (datos) {
        state.reserva = datos;
        state.dni = dni;
        renderConfirmacion(datos);
        mostrarPaso(4);
      })
      .catch(function (error) {
        mostrarError(error.message);
        if (error.status === 409 && state.doctor) cargarHorarios();
      })
      .then(function () {
        boton.disabled = false;
        boton.innerHTML = "Confirmar turno " +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 4 4L19 6"/></svg>';
      });
  }

  function filaTicket(etiqueta, valor) {
    var fila = document.createElement("div");
    fila.className = "rp-ticket-fila";
    var nombre = document.createElement("span");
    nombre.textContent = etiqueta;
    var dato = document.createElement("strong");
    dato.textContent = valor;
    fila.appendChild(nombre);
    fila.appendChild(dato);
    return fila;
  }

  function renderConfirmacion(datos) {
    var ticket = $("rp-ticket");
    ticket.innerHTML = "";
    ticket.appendChild(filaTicket("Profesional", datos.doctor));
    ticket.appendChild(filaTicket("Especialidad", datos.especialidad));
    ticket.appendChild(filaTicket("Fecha", fechaLarga(datos.fecha)));
    ticket.appendChild(filaTicket("Hora", datos.hora + " hs"));
    if (datos.obra_social) ticket.appendChild(filaTicket("Obra social", datos.obra_social));
    if (datos.motivo_consulta) ticket.appendChild(filaTicket("Motivo", datos.motivo_consulta));
    ticket.appendChild(filaTicket("Paciente", datos.paciente));

    $("rp-codigo-valor").textContent = datos.codigo_cancelacion;
    $("rp-cancelar-codigo").value = datos.codigo_cancelacion;
    $("rp-cancelar-dni").value = state.dni;
    $("rp-resultado-cancelar").classList.add("hide");
    $("rp-aviso-wa-texto").textContent = datos.whatsapp_enviado
      ? "Te enviamos la confirmación por WhatsApp."
      : "Guardá este código: el consultorio verá tu turno en la agenda.";
  }

  function copiarTexto(texto) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(texto);
    }
    return new Promise(function (resolver, rechazar) {
      var area = document.createElement("textarea");
      area.value = texto;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
        resolver();
      } catch (error) {
        rechazar(error);
      }
      document.body.removeChild(area);
    });
  }

  function copiarCodigo() {
    var codigo = $("rp-codigo-valor").textContent || "";
    var boton = $("rp-copiar-codigo");
    copiarTexto(codigo).then(function () {
      boton.textContent = "¡Copiado!";
      setTimeout(function () { boton.textContent = "Copiar"; }, 1800);
    }).catch(function () {
      mostrarError("No pudimos copiar el código. Anotalo manualmente.");
    });
  }

  function agendarOtro() {
    state.doctor = null;
    state.hora = "";
    state.horarios = [];
    state.paciente = null;
    state.dni = "";
    state.reserva = null;
    $("rp-dni").value = "";
    $("rp-nombre").value = "";
    $("rp-apellido").value = "";
    $("rp-telefono").value = "";
    $("rp-motivo").value = "";
    $("rp-datos-nombre").classList.remove("hide");
    ocultarInfoPaciente();
    renderObras(null);
    renderDoctores();
    renderHorarios();
    $("rp-1-siguiente").disabled = true;
    $("rp-2-siguiente").disabled = true;
    mostrarPaso(1);
  }

  /* ------------------------------- Cancelación ----------------------------- */
  function mostrarResultadoCancelacion(datos) {
    var caja = $("rp-resultado-cancelar");
    caja.classList.remove("hide");
    caja.textContent =
      datos.mensaje + " Turno N.º " + datos.turno_id + " · " +
      fechaLarga(String(datos.fecha_hora).slice(0, 10)) + " a las " +
      String(datos.fecha_hora).slice(11, 16) + " hs.";
    caja.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function mostrarErrorCancelacion(mensaje) {
    var caja = $("rp-resultado-cancelar");
    caja.classList.remove("hide");
    caja.textContent = mensaje;
    caja.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function cancelarTurno(dni, codigo, boton) {
    if (boton) boton.disabled = true;
    api("/cancelar", {
      method: "POST",
      body: JSON.stringify({ dni: dni, codigo: codigo }),
    }).then(function (datos) {
      mostrarResultadoCancelacion(datos);
      if (state.reserva && datos.turno_id === state.reserva.turno_id) {
        $("rp-exito-titulo").textContent = "Turno cancelado";
        $("rp-paso-4").classList.add("hide");
        mostrarPaso(1);
        $("panel-cancelar").scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }).catch(function (error) {
      mostrarErrorCancelacion(error.message);
    }).then(function () {
      if (boton) boton.disabled = false;
    });
  }

  function cancelarDesdeFormulario(evento) {
    evento.preventDefault();
    var dni = normalizarDni($("rp-cancelar-dni").value);
    var codigo = $("rp-cancelar-codigo").value.trim();
    if (!/^[0-9A-Za-z]{5,20}$/.test(dni)) {
      mostrarErrorCancelacion("Ingresá un DNI válido (sin puntos).");
      return;
    }
    if (!codigo) {
      mostrarErrorCancelacion("Ingresá el código que recibiste al reservar.");
      return;
    }
    cancelarTurno(dni, codigo, $("rp-confirmar-cancelar"));
  }

  function cancelarTurnoActual() {
    if (!state.reserva) return;
    var seguro = window.confirm(
      "¿Querés cancelar el turno del " + fechaLarga(state.reserva.fecha) +
      " a las " + state.reserva.hora + " hs?"
    );
    if (!seguro) return;
    cancelarTurno(state.dni, state.reserva.codigo_cancelacion, $("rp-cancelar-este"));
  }

  /* -------------------------------- Eventos -------------------------------- */
  function registrarEventos() {
    $("rp-1-siguiente").addEventListener("click", irAlPaso2);
    $("rp-2-atras").addEventListener("click", function () { mostrarPaso(1); });
    $("rp-2-siguiente").addEventListener("click", irAlPaso3);
    $("rp-3-atras").addEventListener("click", function () { mostrarPaso(2); });

    $("rp-fecha").addEventListener("change", function () {
      var fecha = $("rp-fecha").value;
      if (!fecha) return;
      if (fecha < isoLocal(new Date())) {
        mostrarError("Elegí una fecha de hoy en adelante.");
        $("rp-fecha").value = state.fecha;
        return;
      }
      state.fecha = fecha;
      state.hora = "";
      $("rp-2-siguiente").disabled = true;
      renderDias();
      cargarHorarios();
    });

    $("rp-buscar-dni").addEventListener("click", buscarDni);
    $("rp-dni").addEventListener("input", alCambiarDni);
    $("rp-dni").addEventListener("keydown", function (evento) {
      if (evento.key === "Enter") {
        evento.preventDefault();
        buscarDni();
      }
    });

    $("rp-form-reserva").addEventListener("submit", reservar);
    $("rp-form-cancelar").addEventListener("submit", cancelarDesdeFormulario);
    $("rp-copiar-codigo").addEventListener("click", copiarCodigo);
    $("rp-agendar-otro").addEventListener("click", agendarOtro);
    $("rp-cancelar-este").addEventListener("click", cancelarTurnoActual);
    $("rp-ir-cancelar").addEventListener("click", function () {
      if (state.reserva) {
        $("rp-cancelar-dni").value = state.dni;
        $("rp-cancelar-codigo").value = state.reserva.codigo_cancelacion;
      }
    });
  }

  function iniciar() {
    state.fecha = isoLocal(new Date());
    $("rp-fecha").value = state.fecha;
    $("rp-fecha").min = state.fecha;
    renderDias();
    registrarEventos();
    cargarInicial();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", iniciar);
  } else {
    iniciar();
  }
})();




