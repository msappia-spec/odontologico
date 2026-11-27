/* =========================================================================
   Franciosi Odontología — Sesión, permisos y administración de usuarios
   -------------------------------------------------------------------------
   Este módulo completa el panel con lo que necesita el "Módulo de
   autenticación, usuarios y permisos":

   * la pantalla de login (POST /auth/login) y el cierre de sesión;
   * la verificación de la sesión al abrir el panel (GET /auth/me);
   * el filtrado del menú según `modulos_permitidos` (app.js aplica la regla);
   * la vista «Usuarios y Permisos» (GET/POST /usuarios y sus PATCH).

   Se apoya en la API pública de app.js (`window.OdontoApp`): mismo cliente
   `fetch`, mismos toasts, mismo escape de HTML y mismo estado global.
   ========================================================================= */
(function () {
  "use strict";

  /* Catálogo de respaldo: si GET /auth/modulos todavía no llegó (o falla), el
     modal de usuarios igual dibuja los checkboxes con estos nombres. Los
     códigos son los mismos que valida el backend. */
  const MODULOS_RESPALDO = [
    { codigo: "PANEL_PRINCIPAL", nombre: "Panel Principal", descripcion: "Resumen del consultorio y alertas del día." },
    { codigo: "AGENDA_TURNOS", nombre: "Agenda y turnos", descripcion: "Calendario, disponibilidad y recordatorios." },
    { codigo: "FICHAS_SALUD", nombre: "Fichas de salud", descripcion: "Declaración de salud, historia y odontograma." },
    { codigo: "CAJA_PRESUPUESTOS", nombre: "Caja y presupuestos", descripcion: "Facturación, caja, aranceles y presupuestos." },
    { codigo: "CUENTA_CORRIENTE", nombre: "Cuenta corriente", descripcion: "Saldo deudor, libro mayor y cuotas." },
    { codigo: "PROVEEDORES_EGRESOS", nombre: "Proveedores y egresos", descripcion: "Facturas de proveedores y pagos." },
    { codigo: "BORRAR_MOVIMIENTOS", nombre: "Borrar movimientos", descripcion: "Eliminar cierres de caja y movimientos (sensible)." },
    { codigo: "REGISTROS", nombre: "Registros", descripcion: "Doctores, pacientes y categorías." },
    { codigo: "CONFIGURACION", nombre: "Configuración", descripcion: "Obras sociales y bot de WhatsApp." },
    { codigo: "ADMIN_USUARIOS", nombre: "Usuarios y permisos", descripcion: "Alta de cuentas y módulos habilitados." },
  ];

  let panelIniciado = false;

  function app() {
    return window.OdontoApp || null;
  }

  function estado() {
    const odonto = app();
    return odonto ? odonto.state : { usuario: null, modulos: [], usuarios: [] };
  }

  function $(id) {
    return document.getElementById(id);
  }

  function api(ruta, opciones) {
    const odonto = app();
    if (!odonto) throw new Error("El panel no terminó de cargar (app.js).");
    return odonto.api(ruta, opciones);
  }

  function toast(tipo, titulo, mensaje) {
    if (app()) app().toast(tipo, titulo, mensaje);
  }

  function esc(valor) {
    return app() ? app().esc(valor == null ? "" : String(valor)) : String(valor || "");
  }

  function iniciales(nombre) {
    return app() ? app().iniciales(nombre || "") : "--";
  }

  function puede(codigo) {
    const odonto = app();
    return odonto ? odonto.puede(codigo) : false;
  }

  function esSuperadmin() {
    const usuario = estado().usuario;
    return !!(usuario && usuario.es_superadmin);
  }

  /* Avisa al módulo de huellas cuando cambia la sesión: el atajo del login y
     el botón «Huella / FaceID» del perfil se muestran sólo cuando corresponde.
     Se usa `window.BiometriaApp` con guardas porque biometria.js se carga
     después de este archivo. */
  function avisarBiometria() {
    if (window.BiometriaApp && window.BiometriaApp.alCambiarSesion) {
      try {
        window.BiometriaApp.alCambiarSesion();
      } catch (_err) {
        /* la biometría es opcional: nunca debe romper la sesión */
      }
    }
  }

  function API_AUTH() {
    const base = app() ? app().API : "/api/v1";
    return base + "/auth/login";
  }

  /* ----------------------------- Pantalla de login ----------------------- */
  function mostrarErrorLogin(mensaje) {
    const caja = $("login-error");
    if (!caja) return;
    caja.textContent = mensaje || "";
    caja.classList.toggle("hide", !mensaje);
  }

  function mostrarLogin(mensaje) {
    const overlay = $("pantalla-login");
    if (!overlay) return;
    overlay.classList.remove("hide");
    mostrarErrorLogin(mensaje || "");
    const clave = $("login-password");
    if (clave) clave.value = "";
    const email = $("login-email");
    if (email && !email.value) setTimeout(function () { email.focus(); }, 50);
    avisarBiometria();
  }

  function ocultarLogin() {
    const overlay = $("pantalla-login");
    if (overlay) overlay.classList.add("hide");
    mostrarErrorLogin("");
  }

  function renderSesion() {
    avisarBiometria();
    const usuario = estado().usuario;
    const nombre = $("sesion-nombre");
    const rol = $("sesion-rol");
    const avatar = $("sesion-avatar");
    if (!usuario) {
      if (nombre) nombre.textContent = "Sin sesión";
      if (rol) rol.textContent = "—";
      if (avatar) avatar.textContent = "--";
      return;
    }
    if (nombre) nombre.textContent = usuario.nombre_completo;
    if (rol) {
      rol.textContent = usuario.es_superadmin
        ? "Superadmin · acceso total"
        : (usuario.modulos_permitidos || []).length + " secciones habilitadas";
    }
    if (avatar) avatar.textContent = iniciales(usuario.nombre_completo);
  }

  /* Arranca el panel después de una sesión válida (primera carga o re-login). */
  function arrancarPanel() {
    const odonto = app();
    if (!odonto) return;
    if (panelIniciado) {
      odonto.aplicarPermisos();
      odonto.cambiarVista(estado().vista);
      odonto.cargarTodo();
      return;
    }
    panelIniciado = true;
    odonto.iniciarPanel();
  }

  async function iniciarPanel() {
    registrarEventos();
    let usuario = null;
    try {
      usuario = await api("/auth/me");
    } catch (err) {
      if (err.status === 401) {
        mostrarLogin("");
      } else {
        mostrarLogin("No se pudo verificar la sesión: " + err.message);
      }
      return;
    }
    estado().usuario = usuario;
    estado().sesionVerificada = true;
    ocultarLogin();
    renderSesion();
    arrancarPanel();
    // El catálogo se pide en segundo plano: lo necesita el modal de permisos.
    cargarModulos().catch(function () {});
  }

  /* Aplica una sesión ya validada (login con contraseña o con huella/FaceID).
     Es el punto único para que ambos caminos dejen el panel en el mismo estado. */
  function aplicarSesion(usuario, mensaje) {
    estado().usuario = usuario;
    estado().sesionVerificada = true;
    ocultarLogin();
    renderSesion();
    arrancarPanel();
    toast(
      "exito",
      "Bienvenido/a",
      mensaje ||
        ((usuario && usuario.nombre_completo ? usuario.nombre_completo : "Usuario") +
          " · sesión iniciada")
    );
    // El catálogo de módulos se pide en segundo plano: lo necesita el modal
    // de permisos y no debe demorar la entrada al panel.
    cargarModulos().catch(function () {});
  }

  async function iniciarSesion(evento) {
    if (evento) evento.preventDefault();
    const email = ($("login-email") || {}).value || "";
    const password = ($("login-password") || {}).value || "";
    const boton = $("login-submit");
    if (!email.trim() || !password) {
      mostrarErrorLogin("Completá el email y la contraseña.");
      return;
    }
    mostrarErrorLogin("");
    if (app()) app().bloquear(boton, true);
    try {
      const respuesta = await fetch(API_AUTH(), {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password: password }),
      });
      const texto = await respuesta.text();
      let datos = null;
      if (texto) { try { datos = JSON.parse(texto); } catch (_) { datos = texto; } }
      if (!respuesta.ok) {
        const detalle = datos && datos.detail;
        throw new Error(
          typeof detalle === "string"
            ? detalle
            : "No se pudo iniciar sesión (HTTP " + respuesta.status + ")."
        );
      }
      aplicarSesion(datos.usuario);
    } catch (err) {
      mostrarErrorLogin(err.message);
    } finally {
      if (app()) app().bloquear(boton, false);
    }
  }

  async function cerrarSesion() {
    try {
      await fetch((app() ? app().API : "/api/v1") + "/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
      });
    } catch (_err) {
      /* aunque falle la petición, la cookie local se descarta al re-loguear */
    }
    estado().usuario = null;
    estado().sesionVerificada = false;
    renderSesion();
    mostrarLogin("Cerraste la sesión.");
    toast("info", "Sesión cerrada", "Volvé a ingresar cuando quieras.");
  }

  function sesionExpirada() {
    if (!estado().usuario) return;
    estado().usuario = null;
    renderSesion();
    mostrarLogin("Tu sesión expiró o la cuenta se desactivó. Volvé a ingresar.");
  }

  /* ------------------------- Catálogo y equipo --------------------------- */
  function modulosDisponibles() {
    const modulos = estado().modulos;
    return modulos && modulos.length ? modulos : MODULOS_RESPALDO;
  }

  function nombreModulo(codigo) {
    const encontrado = modulosDisponibles().filter(function (m) {
      return m.codigo === codigo;
    })[0];
    return encontrado ? encontrado.nombre : codigo;
  }

  async function cargarModulos() {
    if (estado().modulos && estado().modulos.length) return estado().modulos;
    const modulos = await api("/auth/modulos");
    estado().modulos = modulos || [];
    return estado().modulos;
  }

  async function cargarUsuarios() {
    if (!puede("ADMIN_USUARIOS")) return;
    try {
      estado().usuarios = (await api("/usuarios")) || [];
      estado().usuariosError = null;
    } catch (err) {
      estado().usuariosError = err.message;
      toast("error", "No se pudo cargar el equipo", err.message);
    }
    renderUsuarios();
  }

  function alEntrarUsuarios() {
    if (!puede("ADMIN_USUARIOS")) return;
    cargarModulos().catch(function () {});
    cargarUsuarios();
  }

  /* Fecha del último acceso: la API devuelve UTC naive; se interpreta como UTC
     para mostrarla en la hora local del navegador. */
  function fechaLegible(valor) {
    if (!valor) return "Nunca ingresó";
    const texto = /Z|[+-]\d\d:?\d\d$/.test(valor) ? valor : valor + "Z";
    const fecha = new Date(texto);
    if (isNaN(fecha.getTime())) return valor;
    return fecha.toLocaleString("es-AR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function chipsModulos(usuario) {
    if (usuario.es_superadmin) {
      return '<span class="usuario-chip super">Acceso total</span>';
    }
    const codigos = usuario.modulos_permitidos || [];
    if (!codigos.length) {
      return '<span class="text-[11px] text-slate-400">Sin secciones</span>';
    }
    return (
      '<div class="usuario-chips">' +
      codigos
        .map(function (codigo) {
          return '<span class="usuario-chip">' + esc(nombreModulo(codigo)) + "</span>";
        })
        .join("") +
      "</div>"
    );
  }

  function filaUsuario(usuario) {
    const actual = estado().usuario;
    const propio = actual && actual.id === usuario.id;
    const rol = usuario.es_superadmin
      ? '<span class="badge badge-brand">Superadmin</span>'
      : usuario.activo
        ? '<span class="badge badge-green">Activo</span>'
        : '<span class="badge badge-red">Desactivado</span>';
    const vinculo = usuario.doctor_nombre
      ? '<p class="text-[10px] text-slate-400 truncate">Odontólogo: ' + esc(usuario.doctor_nombre) + "</p>"
      : "";
    return (
      '<tr class="' + (usuario.activo ? "" : "opacity-60") + '">' +
      '<td><div class="flex items-center gap-2.5">' +
      '<span class="w-8 h-8 rounded-full bg-slate-100 text-slate-600 text-[11px] font-bold flex items-center justify-center flex-none">' +
      esc(iniciales(usuario.nombre_completo)) +
      "</span>" +
      '<div class="min-w-0"><p class="font-semibold text-slate-800 truncate">' +
      esc(usuario.nombre_completo) +
      (propio ? ' <span class="text-[10px] font-bold text-brand-700">(vos)</span>' : "") +
      '</p><p class="text-[11px] text-slate-400 truncate">' +
      esc(usuario.email) +
      "</p>" + vinculo + "</div></div></td>" +
      "<td>" + rol + "</td>" +
      "<td>" + chipsModulos(usuario) + "</td>" +
      '<td class="text-xs text-slate-500 whitespace-nowrap">' + esc(fechaLegible(usuario.ultimo_acceso)) + "</td>" +
      '<td class="text-right"><div class="flex items-center justify-end gap-1.5 flex-wrap">' +
      '<button type="button" class="btn btn-ghost btn-sm" data-editar-usuario="' + usuario.id + '" title="Editar las secciones habilitadas">Permisos</button>' +
      '<button type="button" class="btn btn-ghost btn-sm" data-reset-usuario="' + usuario.id + '" title="Restablecer la contraseña">Clave</button>' +
      '<button type="button" class="btn btn-ghost btn-sm" data-toggle-usuario="' + usuario.id + '" title="' +
      (usuario.activo ? "Desactivar la cuenta" : "Volver a habilitar la cuenta") + '">' +
      (usuario.activo ? "Desactivar" : "Activar") +
      "</button></div></td></tr>"
    );
  }

  function renderUsuarios() {
    const tbody = $("lista-usuarios");
    if (!tbody) return;
    const usuarios = estado().usuarios || [];
    const contador = $("count-usuarios");
    if (contador) contador.textContent = usuarios.length;
    if (!usuarios.length) {
      tbody.innerHTML =
        '<tr><td colspan="5" class="py-8 text-center text-sm text-slate-400">' +
        esc(
          estado().usuariosError ||
            "Todavía no hay usuarios. Creá el primero con «Nuevo usuario»."
        ) +
        "</td></tr>";
      return;
    }
    tbody.innerHTML = usuarios.map(filaUsuario).join("");
  }

  /* ------------------------- Alta y edición ------------------------------ */
  function mostrarModal(id) {
    const modal = $(id);
    if (modal) modal.classList.remove("hide");
  }

  function ocultarModal(id) {
    const modal = $(id);
    if (modal) modal.classList.add("hide");
  }

  function errorEn(id, mensaje) {
    const caja = $(id);
    if (!caja) return;
    caja.textContent = mensaje || "";
    caja.classList.toggle("hide", !mensaje);
  }

  function usuarioPorId(id) {
    return (
      (estado().usuarios || []).filter(function (u) {
        return String(u.id) === String(id);
      })[0] || null
    );
  }

  function passwordFuerte(valor) {
    if (!valor || valor.length < 8) return false;
    return (
      /[A-ZÁÉÍÓÚÑ]/.test(valor) &&
      /[a-záéíóúñ]/.test(valor) &&
      /\d/.test(valor)
    );
  }

  function renderModulosUsuario(usuario) {
    const contenedor = $("usuario-modulos");
    if (!contenedor) return;
    const marcados = usuario ? usuario.modulos_permitidos || [] : [];
    const superadmin = !!(usuario && usuario.es_superadmin);
    contenedor.innerHTML = modulosDisponibles()
      .map(function (modulo) {
        const activo = superadmin || marcados.indexOf(modulo.codigo) !== -1;
        return (
          '<label class="permiso-opcion">' +
          '<input type="checkbox" value="' +
          esc(modulo.codigo) +
          '"' +
          (activo ? " checked" : "") +
          (superadmin ? " disabled" : "") +
          " />" +
          "<span><span class=\"permiso-opcion-titulo\">" +
          esc(modulo.nombre) +
          '</span><span class="permiso-opcion-detalle">' +
          esc(modulo.descripcion) +
          "</span></span></label>"
        );
      })
      .join("");
  }

  function renderDoctorUsuario(usuario) {
    const select = $("usuario-doctor");
    if (!select) return;
    const doctores = estado().doctores || [];
    const elegido = usuario && usuario.doctor_id ? String(usuario.doctor_id) : "";
    select.innerHTML =
      '<option value="">Sin vínculo</option>' +
      doctores
        .map(function (doctor) {
          return (
            '<option value="' + doctor.id + '"' +
            (String(doctor.id) === elegido ? " selected" : "") +
            ">" + esc(doctor.nombre) + "</option>"
          );
        })
        .join("");
  }

  function leerModulosMarcados() {
    const contenedor = $("usuario-modulos");
    if (!contenedor) return [];
    return Array.prototype.slice
      .call(contenedor.querySelectorAll('input[type="checkbox"]'))
      .filter(function (input) {
        return input.checked && !input.disabled;
      })
      .map(function (input) {
        return input.value;
      });
  }

  function abrirModalUsuario(id) {
    const usuario = id ? usuarioPorId(id) : null;
    estado().usuarioEditId = usuario ? usuario.id : null;
    const formulario = $("form-usuario");
    if (formulario) formulario.reset();
    errorEn("usuario-error", "");
    if ($("usuario-id")) $("usuario-id").value = usuario ? usuario.id : "";
    if ($("usuario-nombre")) $("usuario-nombre").value = usuario ? usuario.nombre_completo : "";
    if ($("usuario-email")) {
      $("usuario-email").value = usuario ? usuario.email : "";
      $("usuario-email").readOnly = !!usuario;
    }
    if ($("usuario-password")) {
      $("usuario-password").value = "";
      $("usuario-password").required = !usuario;
    }
    if ($("usuario-password-nota")) {
      $("usuario-password-nota").textContent = usuario
        ? "Dejá vacío para conservar la clave actual (o usá el botón «Clave»)."
        : "Debe tener al menos 8 caracteres, una mayúscula, una minúscula y un número.";
    }
    if ($("usuario-superadmin")) {
      $("usuario-superadmin").checked = !!(usuario && usuario.es_superadmin);
      $("usuario-superadmin").disabled = !esSuperadmin();
    }
    if ($("usuario-modal-titulo")) {
      $("usuario-modal-titulo").textContent = usuario ? "Editar permisos" : "Nuevo usuario";
    }
    if ($("modal-usuario-save")) {
      $("modal-usuario-save").textContent = usuario ? "Guardar cambios" : "Crear usuario";
    }
    renderModulosUsuario(usuario);
    renderDoctorUsuario(usuario);
    mostrarModal("modal-usuario");
    if (!usuario && $("usuario-nombre")) {
      setTimeout(function () { $("usuario-nombre").focus(); }, 60);
    }
  }

  function cerrarModalUsuario() {
    ocultarModal("modal-usuario");
    estado().usuarioEditId = null;
  }

  async function guardarUsuario(evento) {
    if (evento) evento.preventDefault();
    const id = estado().usuarioEditId;
    const nombre = (($("usuario-nombre") || {}).value || "").trim();
    const email = (($("usuario-email") || {}).value || "").trim();
    const password = ($("usuario-password") || {}).value || "";
    const doctorValor = ($("usuario-doctor") || {}).value || "";
    const superadmin = !!($("usuario-superadmin") || {}).checked;
    // Un superadmin entra a todo: se guarda la lista completa para que, si más
    // adelante se lo degrada, conserve las secciones que tenía marcadas.
    const modulos = superadmin
      ? modulosDisponibles().map(function (modulo) {
          return modulo.codigo;
        })
      : leerModulosMarcados();

    if (nombre.length < 2) {
      errorEn("usuario-error", "Escribí el nombre completo.");
      return;
    }
    if (!email) {
      errorEn("usuario-error", "El email es obligatorio: es el usuario del login.");
      return;
    }
    if (!id && !password) {
      errorEn("usuario-error", "La contraseña es obligatoria para crear la cuenta.");
      return;
    }
    if (password && !passwordFuerte(password)) {
      errorEn(
        "usuario-error",
        "La contraseña debe tener al menos 8 caracteres, una mayúscula, una minúscula y un número."
      );
      return;
    }
    if (!modulos.length && !superadmin) {
      errorEn(
        "usuario-error",
        "Marcá al menos una sección (o la casilla de superadmin)."
      );
      return;
    }

    errorEn("usuario-error", "");
    const boton = $("modal-usuario-save");
    if (app()) app().bloquear(boton, true);
    try {
      if (!id) {
        const cuerpo = {
          email: email,
          nombre_completo: nombre,
          password: password,
          es_superadmin: superadmin,
          modulos_permitidos: modulos,
          doctor_id: doctorValor ? Number(doctorValor) : null,
        };
        await api("/usuarios", { method: "POST", body: JSON.stringify(cuerpo) });
        toast("exito", "Usuario creado", nombre + " ya puede iniciar sesión.");
      } else {
        const cuerpo = { modulos_permitidos: modulos };
        if (esSuperadmin()) cuerpo.es_superadmin = superadmin;
        if (doctorValor) cuerpo.doctor_id = Number(doctorValor);
        await api("/usuarios/" + id + "/permisos", {
          method: "PATCH",
          body: JSON.stringify(cuerpo),
        });
        if (password) {
          await api("/usuarios/" + id + "/reset-password", {
            method: "PATCH",
            body: JSON.stringify({ password: password }),
          });
        }
        toast("exito", "Permisos actualizados", nombre);
      }
      cerrarModalUsuario();
      await cargarUsuarios();
    } catch (err) {
      errorEn("usuario-error", err.message);
    } finally {
      if (app()) app().bloquear(boton, false);
    }
  }

  /* ---------------------- Restablecer contraseña ------------------------- */
  function abrirModalReset(id) {
    const usuario = usuarioPorId(id);
    if (!usuario) return;
    estado().usuarioResetId = usuario.id;
    if ($("usuario-reset-id")) $("usuario-reset-id").value = usuario.id;
    if ($("usuario-reset-nombre")) {
      $("usuario-reset-nombre").textContent = usuario.nombre_completo + " · " + usuario.email;
    }
    if ($("usuario-reset-password")) $("usuario-reset-password").value = "";
    errorEn("usuario-reset-error", "");
    mostrarModal("modal-reset-password");
    if ($("usuario-reset-password")) {
      setTimeout(function () { $("usuario-reset-password").focus(); }, 60);
    }
  }

  function cerrarModalReset() {
    ocultarModal("modal-reset-password");
    estado().usuarioResetId = null;
  }

  async function guardarResetPassword(evento) {
    if (evento) evento.preventDefault();
    const id = estado().usuarioResetId;
    const password = ($("usuario-reset-password") || {}).value || "";
    if (!id) return;
    if (!passwordFuerte(password)) {
      errorEn(
        "usuario-reset-error",
        "La contraseña debe tener al menos 8 caracteres, una mayúscula, una minúscula y un número."
      );
      return;
    }
    errorEn("usuario-reset-error", "");
    const boton = $("modal-reset-save");
    if (app()) app().bloquear(boton, true);
    try {
      const respuesta = await api("/usuarios/" + id + "/reset-password", {
        method: "PATCH",
        body: JSON.stringify({ password: password }),
      });
      toast("exito", "Contraseña actualizada", (respuesta && respuesta.detail) || "");
      cerrarModalReset();
      await cargarUsuarios();
    } catch (err) {
      errorEn("usuario-reset-error", err.message);
    } finally {
      if (app()) app().bloquear(boton, false);
    }
  }

  /* --------------------------- Activar / desactivar ---------------------- */
  /* --------------------------- Activar / desactivar ---------------------- */
  async function alternarCuenta(id) {
    const usuario = usuarioPorId(id);
    if (!usuario) return;
    const accion = usuario.activo ? "desactivar" : "activar";
    if (
      !window.confirm(
        "¿Seguro que querés " + accion + " la cuenta de " + usuario.nombre_completo + "?"
      )
    ) {
      return;
    }
    try {
      await api("/usuarios/" + usuario.id + "/permisos", {
        method: "PATCH",
        body: JSON.stringify({ activo: !usuario.activo }),
      });
      toast(
        "exito",
        usuario.activo ? "Cuenta desactivada" : "Cuenta activada",
        usuario.nombre_completo
      );
      await cargarUsuarios();
    } catch (err) {
      toast("error", "No se pudo cambiar el estado", err.message);
    }
  }

  /* ------------------------------ Eventos -------------------------------- */
  let eventosRegistrados = false;

  function registrarEventos() {
    if (eventosRegistrados) return;
    eventosRegistrados = true;

    const formLogin = $("form-login");
    if (formLogin) formLogin.addEventListener("submit", iniciarSesion);
    const btnLogout = $("btn-logout");
    if (btnLogout) btnLogout.addEventListener("click", cerrarSesion);

    const btnNuevo = $("btn-nuevo-usuario");
    if (btnNuevo) {
      btnNuevo.addEventListener("click", function () {
        abrirModalUsuario(null);
      });
    }
    const btnRefrescar = $("btn-refrescar-usuarios");
    if (btnRefrescar) btnRefrescar.addEventListener("click", cargarUsuarios);

    const formUsuario = $("form-usuario");
    if (formUsuario) formUsuario.addEventListener("submit", guardarUsuario);
    const formReset = $("form-reset-password");
    if (formReset) formReset.addEventListener("submit", guardarResetPassword);

    [
      ["modal-usuario-close", cerrarModalUsuario],
      ["modal-usuario-cancel", cerrarModalUsuario],
      ["modal-reset-close", cerrarModalReset],
      ["modal-reset-cancel", cerrarModalReset],
    ].forEach(function (par) {
      const boton = $(par[0]);
      if (boton) boton.addEventListener("click", par[1]);
    });

    const marcarTodas = $("usuario-marcar-todos");
    if (marcarTodas) {
      marcarTodas.addEventListener("click", function () {
        const contenedor = $("usuario-modulos");
        if (!contenedor) return;
        contenedor
          .querySelectorAll('input[type="checkbox"]:not(:disabled)')
          .forEach(function (input) {
            input.checked = true;
          });
      });
    }

    // Un superadmin accede a todo: al marcarlo, los checkboxes se completan y
    // se bloquean; al desmarcarlo vuelven a elegirse a mano.
    const superadmin = $("usuario-superadmin");
    if (superadmin) {
      superadmin.addEventListener("change", function () {
        const usuario = estado().usuarioEditId
          ? usuarioPorId(estado().usuarioEditId)
          : null;
        renderModulosUsuario(
          superadmin.checked
            ? Object.assign({}, usuario || {}, { es_superadmin: true })
            : Object.assign({}, usuario || {}, { es_superadmin: false })
        );
      });
    }

    // Las filas se redibujan: los botones se resuelven por delegación.
    const lista = $("lista-usuarios");
    if (lista) {
      lista.addEventListener("click", function (evento) {
        const editar = evento.target.closest("[data-editar-usuario]");
        if (editar) {
          abrirModalUsuario(editar.dataset.editarUsuario);
          return;
        }
        const reset = evento.target.closest("[data-reset-usuario]");
        if (reset) {
          abrirModalReset(reset.dataset.resetUsuario);
          return;
        }
        const alternar = evento.target.closest("[data-toggle-usuario]");
        if (alternar) alternarCuenta(alternar.dataset.toggleUsuario);
      });
    }
  }

  /* --------------------------- API del módulo ---------------------------- */
  window.SesionApp = {
    iniciarPanel,
    iniciarSesion,
    aplicarSesion,
    cerrarSesion,
    sesionExpirada,
    alEntrarUsuarios,
    cargarUsuarios,
    cargarModulos,
    mostrarLogin,
    renderSesion,
    // Se exponen para depuración y para reutilizarlos desde la consola.
    abrirModalUsuario,
    abrirModalReset,
  };
})();
