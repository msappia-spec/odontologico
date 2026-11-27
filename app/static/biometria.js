/* =========================================================================
   Franciosi Odontología — Huella digital / Face ID (WebAuthn / passkeys)
   -------------------------------------------------------------------------
   Atajo de acceso para los odontólogos desde el celular o la notebook:

   * LOGIN: el botón «Ingresar con Huella Digital / FaceID» toma el email del
     formulario, pide el desafío a POST /auth/webauthn/login/opciones y llama a
     `navigator.credentials.get()`. La verificación devuelve el mismo JWT +
     cookie HttpOnly que el login con contraseña (SesionApp.aplicarSesion).
   * REGISTRO: con la sesión abierta, el botón del menú lateral abre el modal y
     llama a `navigator.credentials.create()`; el servidor guarda la clave
     pública del dispositivo (POST /auth/webauthn/registro/verificar).

   El navegador nunca envía la huella: sólo firma un desafío con la clave
   privada que queda en el Secure Enclave / TPM. El servidor guarda la clave
   pública y comprueba la firma con py_webauthn.

   Si el dispositivo no soporta biometría, el botón de login se oculta, se
   explica el motivo y la contraseña sigue funcionando siempre.
   ========================================================================= */
(function () {
  "use strict";

  const state = { credenciales: [], eventos: false };

  function $(id) {
    return document.getElementById(id);
  }

  function API() {
    return (window.OdontoApp && window.OdontoApp.API) || "/api/v1";
  }

  function toast(tipo, titulo, mensaje) {
    if (window.OdontoApp && window.OdontoApp.toast) {
      window.OdontoApp.toast(tipo, titulo, mensaje);
    }
  }

  function esc(valor) {
    const texto = valor == null ? "" : String(valor);
    return window.OdontoApp ? window.OdontoApp.esc(texto) : texto;
  }

  /* ---------------------- base64url <-> ArrayBuffer ---------------------- */
  /* WebAuthn transporta los binarios en base64url sin relleno; el JSON del
     servidor usa exactamente ese formato, así que sólo hay que convertir. */
  function aBuffer(texto) {
    const base64 = String(texto || "").replace(/-/g, "+").replace(/_/g, "/");
    const relleno = "=".repeat((4 - (base64.length % 4)) % 4);
    const binario = window.atob(base64 + relleno);
    const bytes = new Uint8Array(binario.length);
    for (let i = 0; i < binario.length; i += 1) {
      bytes[i] = binario.charCodeAt(i);
    }
    return bytes.buffer;
  }

  function aBase64url(buffer) {
    const bytes = new Uint8Array(buffer);
    let binario = "";
    for (let i = 0; i < bytes.length; i += 1) {
      binario += String.fromCharCode(bytes[i]);
    }
    return window
      .btoa(binario)
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  }

  /* ----------------------------- Soporte -------------------------------- */
  /* WebAuthn exige un contexto seguro: HTTPS o localhost. */
  function soportado() {
    return !!(
      window.PublicKeyCredential &&
      navigator.credentials &&
      window.isSecureContext
    );
  }

  async function biometriaDisponible() {
    if (!soportado()) return false;
    try {
      return await window.PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
    } catch (_err) {
      return false;
    }
  }

  /* ------------------------------ HTTP ---------------------------------- */
  async function peticion(ruta, opciones) {
    const config = Object.assign(
      {
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
      },
      opciones || {}
    );
    const respuesta = await fetch(API() + ruta, config);
    const texto = await respuesta.text();
    let datos = null;
    if (texto) {
      try {
        datos = JSON.parse(texto);
      } catch (_err) {
        datos = texto;
      }
    }
    if (!respuesta.ok) {
      const detalle = datos && datos.detail;
      const error = new Error(
        typeof detalle === "string"
          ? detalle
          : "No se pudo completar la operación (HTTP " + respuesta.status + ")."
      );
      error.status = respuesta.status;
      throw error;
    }
    return datos;
  }


  /* --------------------- Opciones y credenciales ------------------------ */
  /* Convierte los base64url del servidor en ArrayBuffer para la API WebAuthn. */
  function opcionesDelNavegador(publicKey) {
    const opciones = Object.assign({}, publicKey || {});
    opciones.challenge = aBuffer(publicKey.challenge);
    if (publicKey.user && publicKey.user.id) {
      opciones.user = Object.assign({}, publicKey.user, {
        id: aBuffer(publicKey.user.id),
      });
    }
    ["excludeCredentials", "allowCredentials"].forEach(function (clave) {
      if (Array.isArray(publicKey[clave])) {
        opciones[clave] = publicKey[clave].map(function (credencial) {
          return Object.assign({}, credencial, { id: aBuffer(credencial.id) });
        });
      }
    });
    return opciones;
  }

  /* Serializa el PublicKeyCredential a JSON (con base64url) para el servidor. */
  function serializarCredencial(credencial) {
    const respuesta = credencial.response || {};
    const salida = {
      id: credencial.id,
      rawId: aBase64url(credencial.rawId),
      type: credencial.type || "public-key",
      clientExtensionResults: credencial.getClientExtensionResults
        ? credencial.getClientExtensionResults()
        : {},
      response: {
        clientDataJSON: aBase64url(respuesta.clientDataJSON),
      },
    };
    if (respuesta.attestationObject) {
      salida.response.attestationObject = aBase64url(respuesta.attestationObject);
    }
    if (respuesta.authenticatorData) {
      salida.response.authenticatorData = aBase64url(respuesta.authenticatorData);
    }
    if (respuesta.signature) {
      salida.response.signature = aBase64url(respuesta.signature);
    }
    if (typeof respuesta.userHandle !== "undefined") {
      salida.response.userHandle = respuesta.userHandle
        ? aBase64url(respuesta.userHandle)
        : null;
    }
    if (typeof respuesta.getTransports === "function") {
      try {
        salida.response.transports = respuesta.getTransports();
      } catch (_err) {
        /* los transportes son opcionales */
      }
    }
    return salida;
  }

  /* Mensajes claros: el dispositivo puede cancelar, no tener lector o estar
     en un dominio sin HTTPS. En todos los casos se puede usar la contraseña. */
  function mensajeError(error) {
    if (!error) return "No se pudo usar la biometría.";
    if (error.status === 429) return error.message;
    if (error.name === "NotAllowedError") {
      return "Cancelaste el pedido o el dispositivo no reconoció tu huella. Probá de nuevo o ingresá con tu contraseña.";
    }
    if (error.name === "InvalidStateError") {
      return "Este dispositivo ya está registrado en tu cuenta.";
    }
    if (error.name === "SecurityError") {
      return "Este dominio no está habilitado para biometría. Revisá RP_ID / RP_ORIGIN y usá HTTPS.";
    }
    if (error.name === "NotSupportedError") {
      return "Este dispositivo no tiene un lector de huellas o Face ID disponible.";
    }
    return error.message || String(error);
  }

  function bloquear(boton, activo) {
    if (window.OdontoApp && window.OdontoApp.bloquear) {
      window.OdontoApp.bloquear(boton, activo);
      return;
    }
    if (boton) boton.disabled = !!activo;
  }

  function errorEnCaja(id, mensaje) {
    const caja = $(id);
    if (!caja) return;
    caja.textContent = mensaje || "";
    caja.classList.toggle("hide", !mensaje);
  }

  function fechaLegible(valor) {
    if (!valor) return "—";
    const texto = /Z|[+-]\d\d:?\d\d$/.test(valor) ? valor : valor + "Z";
    const fecha = new Date(texto);
    if (isNaN(fecha.getTime())) return valor;
    return fecha.toLocaleDateString("es-AR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
  }

  /* --------------------------- Login con huella ------------------------- */
  function mostrarErrorLogin(mensaje) {
    errorEnCaja("login-error", mensaje);
  }

  async function iniciarLoginConHuella() {
    const boton = $("login-biometria");
    const email = (($("login-email") || {}).value || "").trim();
    if (!soportado()) {
      mostrarErrorLogin(
        "Este navegador no soporta biometría. Ingresá con tu contraseña."
      );
      return;
    }
    if (!email) {
      mostrarErrorLogin("Escribí tu email y volvé a tocar «Ingresar con Huella».");
      const campo = $("login-email");
      if (campo) campo.focus();
      return;
    }
    mostrarErrorLogin("");
    bloquear(boton, true);
    try {
      const datos = await peticion("/auth/webauthn/login/opciones", {
        method: "POST",
        body: JSON.stringify({ email: email }),
      });
      const publicKey = opcionesDelNavegador(datos.publicKey || {});
      if (!publicKey.allowCredentials || !publicKey.allowCredentials.length) {
        throw new Error(
          "Esta cuenta no tiene una huella registrada en este dispositivo. " +
            "Ingresá con tu contraseña y registrala desde el menú lateral."
        );
      }
      const credencial = await navigator.credentials.get({ publicKey: publicKey });
      const sesion = await peticion("/auth/webauthn/login/verificar", {
        method: "POST",
        body: JSON.stringify({
          challenge_token: datos.challenge_token,
          credential: serializarCredencial(credencial),
        }),
      });
      const panel = window.SesionApp;
      if (panel && panel.aplicarSesion) {
        panel.aplicarSesion(sesion.usuario, "Huella reconocida · sesión iniciada");
      } else {
        window.location.reload();
      }
    } catch (error) {
      mostrarErrorLogin(mensajeError(error));
    } finally {
      bloquear(boton, false);
    }
  }

  /* ---------------------- Registro del dispositivo ---------------------- */
  function nombreSugerido() {
    const agente = navigator.userAgent || "";
    return /android|iphone|ipad|mobile/i.test(agente) ? "Celular" : "Notebook";
  }

  function renderLista() {
    const contenedor = $("bio-lista");
    if (!contenedor) return;
    if (!state.credenciales.length) {
      contenedor.innerHTML =
        '<p class="bio-vacio">Todavía no registraste ningún dispositivo.</p>';
      return;
    }
    contenedor.innerHTML = state.credenciales
      .map(function (credencial) {
        return (
          '<div class="bio-item">' +
          '<div class="min-w-0">' +
          '<p class="bio-item-nombre">' +
          esc(credencial.nombre_dispositivo) +
          "</p>" +
          '<p class="bio-item-fecha">Registrado el ' +
          esc(fechaLegible(credencial.fecha_registro)) +
          "</p></div>" +
          '<button type="button" class="btn btn-ghost btn-sm" data-bio-eliminar="' +
          esc(credencial.id) +
          '">Quitar</button>' +
          "</div>"
        );
      })
      .join("");
  }

  async function cargarCredenciales() {
    try {
      const lista = await peticion("/auth/webauthn/credenciales");
      state.credenciales = Array.isArray(lista) ? lista : [];
      errorEnCaja("bio-error", "");
    } catch (error) {
      state.credenciales = [];
      errorEnCaja("bio-error", mensajeError(error));
    }
    renderLista();
  }

  async function abrirModal() {
    const usuario =
      window.OdontoApp && window.OdontoApp.state
        ? window.OdontoApp.state.usuario
        : null;
    if (!usuario) {
      mostrarErrorLogin("Iniciá sesión para registrar tu huella.");
      return;
    }
    if (!soportado()) {
      toast(
        "info",
        "Biometría no disponible",
        "Este navegador o dispositivo no soporta huellas/Face ID. Podés seguir usando tu contraseña."
      );
      return;
    }
    const modal = $("modal-biometria");
    if (modal) modal.classList.remove("hide");
    errorEnCaja("bio-error", "");
    const campo = $("bio-nombre");
    if (campo && !campo.value) campo.value = nombreSugerido();
    await cargarCredenciales();
  }

  function cerrarModal() {
    const modal = $("modal-biometria");
    if (modal) modal.classList.add("hide");
    errorEnCaja("bio-error", "");
  }

  async function registrarDispositivo() {
    const boton = $("bio-registrar");
    const nombre = (($("bio-nombre") || {}).value || "").trim() || "Mi dispositivo";
    errorEnCaja("bio-error", "");
    bloquear(boton, true);
    try {
      const datos = await peticion("/auth/webauthn/registro/opciones", {
        method: "POST",
      });
      const publicKey = opcionesDelNavegador(datos.publicKey || {});
      const credencial = await navigator.credentials.create({
        publicKey: publicKey,
      });
      const resultado = await peticion("/auth/webauthn/registro/verificar", {
        method: "POST",
        body: JSON.stringify({
          challenge_token: datos.challenge_token,
          credential: serializarCredencial(credencial),
          nombre_dispositivo: nombre,
        }),
      });
      toast(
        "exito",
        "Huella registrada",
        (resultado && resultado.detail) ||
          "Este dispositivo ya puede entrar sin contraseña."
      );
      const campo = $("bio-nombre");
      if (campo) campo.value = "";
      await cargarCredenciales();
    } catch (error) {
      errorEnCaja("bio-error", mensajeError(error));
    } finally {
      bloquear(boton, false);
    }
  }

  async function eliminarDispositivo(id) {
    if (
      !window.confirm(
        "¿Quitar este dispositivo? Podés volver a registrarlo cuando quieras."
      )
    ) {
      return;
    }
    try {
      await peticion("/auth/webauthn/credenciales/" + encodeURIComponent(id), {
        method: "DELETE",
      });
      toast("info", "Dispositivo eliminado", "Tu contraseña sigue funcionando.");
      await cargarCredenciales();
    } catch (error) {
      errorEnCaja("bio-error", mensajeError(error));
    }
  }

  /* --------------------------- Visibilidad ------------------------------ */
  /* El atajo de login aparece sólo si hay un autenticador de plataforma
     (huella/Face ID del propio equipo). Si no, se explica y se usa la clave. */
  async function actualizarBotonLogin() {
    const boton = $("login-biometria");
    const nota = $("login-biometria-nota");
    const divisor = $("login-divisor-biometria");
    if (!boton) return;
    const disponible = await biometriaDisponible();
    boton.classList.toggle("hide", !disponible);
    if (divisor) divisor.classList.toggle("hide", !disponible);
    if (nota) {
      nota.classList.toggle("hide", disponible);
      nota.textContent = disponible
        ? ""
        : soportado()
          ? "Este dispositivo no tiene huella/Face ID configurado. Ingresá con tu contraseña."
          : "Este navegador no soporta biometría: ingresá con tu contraseña.";
    }
  }

  function actualizarBotonSesion() {
    const boton = $("btn-biometria");
    if (!boton) return;
    const usuario =
      window.OdontoApp && window.OdontoApp.state
        ? window.OdontoApp.state.usuario
        : null;
    boton.classList.toggle("hide", !usuario || !soportado());
  }

  /* ------------------------------ Eventos -------------------------------- */
  function registrarEventos() {
    if (state.eventos) return;
    state.eventos = true;

    const btnLogin = $("login-biometria");
    if (btnLogin) btnLogin.addEventListener("click", iniciarLoginConHuella);

    const btnSesion = $("btn-biometria");
    if (btnSesion) btnSesion.addEventListener("click", abrirModal);

    const btnRegistrar = $("bio-registrar");
    if (btnRegistrar) btnRegistrar.addEventListener("click", registrarDispositivo);

    ["modal-biometria-close", "modal-biometria-cerrar"].forEach(function (id) {
      const boton = $(id);
      if (boton) boton.addEventListener("click", cerrarModal);
    });

    // La lista se redibuja: los botones «Quitar» se resuelven por delegación.
    const lista = $("bio-lista");
    if (lista) {
      lista.addEventListener("click", function (evento) {
        const quitar = evento.target.closest("[data-bio-eliminar]");
        if (quitar) eliminarDispositivo(quitar.dataset.bioEliminar);
      });
    }
  }

  function iniciar() {
    registrarEventos();
    actualizarBotonSesion();
    actualizarBotonLogin();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", iniciar);
  } else {
    iniciar();
  }

  /* --------------------------- API del módulo ---------------------------- */
  window.BiometriaApp = {
    soportado: soportado,
    biometriaDisponible: biometriaDisponible,
    iniciarLoginConHuella: iniciarLoginConHuella,
    abrirModal: abrirModal,
    cargarCredenciales: cargarCredenciales,
    // SesionApp avisa cuando cambia el usuario para refrescar los botones.
    alCambiarSesion: function () {
      actualizarBotonSesion();
      actualizarBotonLogin();
    },
    // Utilidades expuestas para depuración/consola.
    aBase64url: aBase64url,
    deBase64url: aBuffer,
  };
})();
