/* ==========================================================================
   Ficha pública de salud (Módulo 4: anamnesis)
   --------------------------------------------------------------------------
   Se sirve junto con ``anamnesis.html`` desde ``GET /anamnesis/{token}``.

   Reglas de esta capa:
   * El token vive únicamente en la ruta; nunca se guarda ni se envía en el
     cuerpo. Las llamadas van al API con ``credentials: same-origin``.
   * La página es de sólo lectura hasta que el paciente confirma y envía. Un
     ``GET`` no crea historia clínica; el ``POST`` sí (lo decide el backend).
   * Todo dato del API se inserta con ``textContent``: los textos libres del
     paciente jamás se interpretan como HTML.
   * Reenviar el formulario corrige la declaración existente.
   ========================================================================== */

(function () {
  "use strict";

  var API = "/api/v1/anamnesis/";
  var MAX_TEXTO = 1000;

  var state = {
    token: "",
    datos: null,
    enviando: false,
  };

  function $(id) { return document.getElementById(id); }

  /* --------------------------- Utilidades fecha -------------------------- */
  /* La API entrega UTC naive, igual que el panel del odontólogo. Se muestra la
     fecha tal como fue agendada, sin convertirla a la zona del dispositivo. */
  function parteFecha(iso) { return String(iso || "").slice(0, 10); }
  function parteHora(iso) { return String(iso || "").slice(11, 16); }

  function fechaLarga(iso) {
    var partes = parteFecha(iso).split("-").map(Number);
    if (!partes[0] || !partes[1] || !partes[2]) return "";
    return new Date(partes[0], partes[1] - 1, partes[2]).toLocaleDateString(
      "es-AR",
      { weekday: "long", day: "numeric", month: "long" }
    );
  }

  function fechaHora(iso) {
    var dia = fechaLarga(iso);
    var hora = parteHora(iso);
    if (!dia) return "—";
    return hora ? dia + " · " + hora + " h" : dia;
  }

  /* ------------------------------- Red ----------------------------------- */
  function obtenerToken() {
    var partes = window.location.pathname.split("/").filter(Boolean);
    if (partes.length < 2 || partes[0] !== "anamnesis") return "";
    try {
      return decodeURIComponent(partes[1]);
    } catch (error) {
      return partes[1];
    }
  }

  function mensajeDeError(cuerpo, estadoHttp) {
    var detalle = cuerpo && cuerpo.detail;
    if (typeof detalle === "string" && detalle.trim()) return detalle;
    if (Array.isArray(detalle) && detalle.length) {
      return "Algunos datos no son válidos. Revisá las respuestas e intentá de nuevo.";
    }
    if (estadoHttp === 400) return "El enlace no es válido. Pedí uno nuevo en el consultorio.";
    if (estadoHttp === 404) return "No encontramos tu turno. Pedí un enlace nuevo en el consultorio.";
    if (estadoHttp === 422) return "Algunos datos no son válidos. Revisá las respuestas e intentá de nuevo.";
    if (estadoHttp >= 500) return "Tuvimos un problema para guardar tu ficha. Intentá de nuevo en unos minutos.";
    return "No pudimos completar la operación. Intentá de nuevo.";
  }

  async function pedir(metodo, cuerpo) {
    var opciones = {
      method: metodo,
      headers: { Accept: "application/json" },
      credentials: "same-origin",
      cache: "no-store",
    };
    if (cuerpo !== undefined) {
      opciones.headers["Content-Type"] = "application/json";
      opciones.body = JSON.stringify(cuerpo);
    }

    var respuesta;
    try {
      respuesta = await fetch(API + encodeURIComponent(state.token), opciones);
    } catch (error) {
      var red = new Error(
        "No pudimos conectarnos. Revisá tu conexión a internet e intentá de nuevo."
      );
      red.esRed = true;
      throw red;
    }

    var datos = null;
    try {
      datos = await respuesta.json();
    } catch (error) {
      datos = null;
    }

    if (!respuesta.ok) {
      var fallo = new Error(mensajeDeError(datos, respuesta.status));
      fallo.status = respuesta.status;
      throw fallo;
    }
    return datos;
  }

  /* --------------------------- Estados visuales -------------------------- */
  function mostrarCargando() {
    $("an-cargando").hidden = false;
    $("an-error").hidden = true;
    $("an-contenido").hidden = true;
  }

  function mostrarError(mensaje) {
    $("an-cargando").hidden = true;
    $("an-contenido").hidden = true;
    $("an-error-texto").textContent = mensaje;
    $("an-error").hidden = false;
    var titulo = $("an-error").querySelector("h1");
    if (titulo) {
      titulo.setAttribute("tabindex", "-1");
      titulo.focus({ preventScroll: true });
    }
  }

  function mostrarContenido() {
    $("an-cargando").hidden = true;
    $("an-error").hidden = true;
    $("an-contenido").hidden = false;
    $("an-contenido").classList.add("an-contenido-animado");
  }

  function pintarContexto(datos) {
    $("an-nombre").textContent = datos.paciente_nombre || "paciente";
    $("an-turno-fecha").textContent = fechaHora(datos.fecha_hora_inicio) || "Tu próximo turno";

    var profesional = datos.doctor_nombre || "Tu profesional";
    if (datos.doctor_especialidad) {
      profesional += " · " + datos.doctor_especialidad;
    }
    $("an-turno-profesional").textContent = profesional;
  }

  /* ------------------------------ Resumen -------------------------------- */
  function crearFila(etiqueta, valor, tipo) {
    var fila = document.createElement("div");
    fila.className = "an-resumen-fila";

    var termino = document.createElement("dt");
    termino.textContent = etiqueta;

    var detalle = document.createElement("dd");
    if (tipo === "booleano") {
      var chip = document.createElement("span");
      chip.className = "an-chip " + (valor ? "an-chip-si" : "an-chip-no");
      chip.textContent = valor ? "Sí" : "No";
      detalle.appendChild(chip);
    } else if (valor) {
      detalle.textContent = valor;
    } else {
      detalle.textContent = "Sin datos";
      detalle.classList.add("an-vacio");
    }

    fila.appendChild(termino);
    fila.appendChild(detalle);
    return fila;
  }

  function pintarResumen(antecedentes) {
    var a = antecedentes || {};
    var lista = $("an-resumen-datos");
    lista.replaceChildren();

    lista.appendChild(crearFila("Alergias a medicamentos", a.alergias_medicamentos, "texto"));
    lista.appendChild(crearFila("Presión arterial alta", Boolean(a.presion_alta), "booleano"));
    lista.appendChild(crearFila("Problemas cardíacos", Boolean(a.problemas_cardiacos), "booleano"));
    lista.appendChild(crearFila("Medicación actual", a.medicacion_actual, "texto"));
    lista.appendChild(crearFila("Embarazo", Boolean(a.embarazo), "booleano"));
    if (a.fecha_completado) {
      lista.appendChild(crearFila("Última actualización", fechaHora(a.fecha_completado), "texto"));
    }

    var previa = document.querySelector(".an-resumen-nota");
    if (previa) previa.remove();

    if (a.alerta_medica) {
      var nota = document.createElement("p");
      nota.className = "an-resumen-nota";
      nota.textContent =
        "Marcaste antecedentes importantes: el profesional los revisará antes de la consulta.";
      lista.parentNode.insertBefore(nota, $("an-corregir"));
    }
  }

  function mostrarResumen(datos, esNuevo) {
    state.datos = datos;
    pintarContexto(datos);
    mostrarContenido();

    $("an-form").hidden = true;
    $("an-resumen").hidden = false;
    $("an-resumen-titulo").textContent = esNuevo ? "¡Ficha enviada!" : "Tu ficha de salud";
    $("an-resumen-mensaje").textContent = esNuevo
      ? (datos.mensaje || "Recibimos tu ficha. El profesional la verá antes de tu consulta.")
      : "Ya nos contaste cómo estás de salud. Si algo cambió, podés corregirlo.";

    pintarResumen(datos.antecedentes_salud);

    if (esNuevo) {
      var titulo = $("an-resumen-titulo");
      titulo.setAttribute("tabindex", "-1");
      titulo.focus({ preventScroll: true });
    }
  }

  /* ------------------------------ Formulario ------------------------------ */
  function limpiarErrorFormulario() {
    var caja = $("an-form-error");
    caja.hidden = true;
    caja.textContent = "";
  }

  function mostrarErrorFormulario(mensaje, enfocarConsentimiento) {
    var caja = $("an-form-error");
    caja.textContent = mensaje;
    caja.hidden = false;

    if (enfocarConsentimiento) {
      $("an-consentimiento").focus({ preventScroll: true });
    } else {
      caja.setAttribute("tabindex", "-1");
      caja.focus({ preventScroll: true });
    }
  }

  function mostrarFormulario(enfocar) {
    $("an-resumen").hidden = true;
    $("an-form").hidden = false;
    limpiarErrorFormulario();
    cargarRespuestas(state.datos && state.datos.antecedentes_salud);

    if (enfocar) {
      $("an-form").scrollIntoView({ behavior: "smooth", block: "start" });
      $("an-alergias").focus({ preventScroll: true });
    }
  }

  function ponerContador(id) {
    var campo = $(id);
    var contador = document.querySelector('[data-contador="' + id + '"]');
    if (!campo || !contador) return;
    var cantidad = campo.value.length;
    contador.textContent = cantidad + " / " + MAX_TEXTO;
    contador.classList.toggle("an-contador-limite", cantidad >= MAX_TEXTO * 0.9);
  }

  function cargarRespuestas(antecedentes) {
    var a = antecedentes || {};
    $("an-alergias").value = a.alergias_medicamentos || "";
    $("an-medicacion").value = a.medicacion_actual || "";
    $("an-presion").checked = Boolean(a.presion_alta);
    $("an-cardiacos").checked = Boolean(a.problemas_cardiacos);
    $("an-embarazo").checked = Boolean(a.embarazo);
    ponerContador("an-alergias");
    ponerContador("an-medicacion");
  }

  /* -------------------------------- Envío -------------------------------- */
  function construirPayload() {
    return {
      alergias_medicamentos: $("an-alergias").value.trim() || null,
      presion_alta: $("an-presion").checked,
      problemas_cardiacos: $("an-cardiacos").checked,
      medicacion_actual: $("an-medicacion").value.trim() || null,
      embarazo: $("an-embarazo").checked,
    };
  }

  function botonCargando(activo) {
    var boton = $("an-enviar");
    boton.disabled = activo;
    boton.setAttribute("aria-busy", activo ? "true" : "false");
    boton.querySelector(".an-btn-texto").hidden = activo;
    boton.querySelector(".an-btn-cargando").hidden = !activo;
    boton.querySelector(".an-btn-flecha").hidden = activo;
  }

  async function enviar(evento) {
    evento.preventDefault();
    if (state.enviando) return;

    limpiarErrorFormulario();
    if (!$("an-consentimiento").checked) {
      mostrarErrorFormulario(
        "Para enviar la ficha tenés que declarar que la información es correcta.",
        true
      );
      return;
    }

    state.enviando = true;
    botonCargando(true);
    $("an-form").setAttribute("aria-busy", "true");

    try {
      var datos = await pedir("POST", construirPayload());
      mostrarResumen(datos, true);
    } catch (error) {
      if (error.status === 400 || error.status === 404) {
        mostrarError(error.message);
      } else {
        mostrarErrorFormulario(error.message);
      }
    } finally {
      state.enviando = false;
      botonCargando(false);
      $("an-form").removeAttribute("aria-busy");
    }
  }

  /* -------------------------- Eventos e inicio --------------------------- */
  function conectarEventos() {
    ["an-alergias", "an-medicacion"].forEach(function (id) {
      var campo = $(id);
      if (campo) {
        campo.addEventListener("input", function () { ponerContador(id); });
      }
    });

    $("an-form").addEventListener("submit", enviar);
    $("an-corregir").addEventListener("click", function () {
      mostrarFormulario(true);
    });
    $("an-reintentar").addEventListener("click", function () {
      window.location.reload();
    });
    $("an-consentimiento").addEventListener("change", limpiarErrorFormulario);
  }

  async function iniciar() {
    state.token = obtenerToken();
    conectarEventos();

    if (!state.token) {
      mostrarError("El enlace está incompleto. Abrilo de nuevo desde el mensaje de WhatsApp.");
      return;
    }

    try {
      var datos = await pedir("GET");
      if (!datos) throw new Error("La respuesta del servidor llegó vacía.");
      state.datos = datos;
      pintarContexto(datos);
      mostrarContenido();

      if (datos.completado && datos.antecedentes_salud) {
        mostrarResumen(datos, false);
      } else {
        mostrarFormulario(false);
      }
    } catch (error) {
      mostrarError(error.message);
    }
  }

  iniciar();
})();
