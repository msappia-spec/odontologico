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
  /* El detalle lo arma ``FICHA_MEDICA.renderResumen`` (mismo catálogo que el
     formulario): así una pregunta nueva aparece en los dos lados. */
  /* Los filiatorios viven en el registro del paciente y el backend los manda
     aparte (``datos_filiatorios``); la ficha guardada también los trae. Se
     fusionan para que el formulario y el resumen muestren siempre lo último. */
  function datosDeLaFicha() {
    var datos = state.datos || {};
    var fusion = {};
    [datos.datos_filiatorios || {}, datos.antecedentes_salud || {}].forEach(
      function (fuente) {
        Object.keys(fuente).forEach(function (clave) {
          fusion[clave] = fuente[clave];
        });
      }
    );
    return fusion;
  }

  function pintarResumen(datos) {
    var a = datosDeLaFicha();
    var contenedor = $("an-resumen-datos");
    contenedor.replaceChildren();

    if (window.FICHA_MEDICA) {
      FICHA_MEDICA.renderResumen(contenedor, a, { soloPaciente: true });
    }

    if (a.fecha_completado) {
      var actualizado = document.createElement("p");
      actualizado.className = "an-resumen-actualizado";
      actualizado.textContent =
        "Última actualización: " + fechaHora(a.fecha_completado);
      contenedor.appendChild(actualizado);
    }

    var previa = document.querySelector(".an-resumen-nota");
    if (previa) previa.remove();

    if (a.alerta_medica) {
      var nota = document.createElement("p");
      nota.className = "an-resumen-nota";
      nota.textContent =
        "Marcaste antecedentes importantes: el profesional los revisará antes de la consulta.";
      contenedor.parentNode.insertBefore(nota, $("an-corregir"));
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

    pintarResumen(datos);

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
    cargarRespuestas();

    if (enfocar) {
      $("an-form").scrollIntoView({ behavior: "smooth", block: "start" });
      var primero = $("an-campos").querySelector("[data-campo]");
      if (primero) primero.focus({ preventScroll: true });
    }
  }

  function cargarRespuestas() {
    if (window.FICHA_MEDICA) {
      FICHA_MEDICA.escribirFormulario($("an-campos"), datosDeLaFicha());
    }
  }

  /* -------------------------------- Envío -------------------------------- */
  function construirPayload() {
    if (!window.FICHA_MEDICA) return {};
    return FICHA_MEDICA.leerFormulario($("an-campos"));
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
    if (window.FICHA_MEDICA) {
      FICHA_MEDICA.renderFormulario($("an-campos"), null, { soloPaciente: true });
    }
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
