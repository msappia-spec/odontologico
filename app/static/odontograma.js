/* ==========================================================================
   Odontograma gráfico (Módulo 4)
   --------------------------------------------------------------------------
   El profesional lo completa **después** de revisar al paciente, en un modal
   aparte de la ficha médica. Reproduce el cuadro de la Historia Clínica
   General:

   * ROJO  → prestación existente (lo que el paciente ya tiene hecho).
   * AZUL  → prestación requerida (lo que hay que hacer).
   * X     → diente ausente o a extraer.

   Se elige un color en la paleta y después se tocan los dientes (volver a
   tocarlos con el mismo color borra la marca). El guardado reemplaza el
   conjunto completo, igual que el ``PUT`` de la API.

   Se usa desde el Panel Principal y desde la vista móvil: el modal se inyecta
   en el ``body`` la primera vez que se abre, así las páginas sólo necesitan
   incluir este archivo y un botón con ``data-odontograma="<paciente_id>"``.
   ========================================================================== */

(function () {
  "use strict";

  var API = "/api/v1/pacientes/";

  var ESTADOS = {
    EXISTENTE: {
      etiqueta: "Prestación existente",
      clase: "odo-existente",
      ayuda: "Rojo"
    },
    REQUERIDO: {
      etiqueta: "Prestación requerida",
      clase: "odo-requerido",
      ayuda: "Azul"
    },
    AUSENTE: {
      etiqueta: "Diente ausente o a extraer",
      clase: "odo-ausente",
      ayuda: "X"
    }
  };

  var ARCADAS = [
    { id: "sup-der", dientes: [18, 17, 16, 15, 14, 13, 12, 11] },
    { id: "sup-izq", dientes: [21, 22, 23, 24, 25, 26, 27, 28] },
    { id: "inf-der", dientes: [48, 47, 46, 45, 44, 43, 42, 41] },
    { id: "inf-izq", dientes: [31, 32, 33, 34, 35, 36, 37, 38] }
  ];

  var state = {
    pacienteId: null,
    nombre: "",
    dientes: {},
    seleccion: null,
    dienteSeleccionado: null,
    guardando: false,
    inyectado: false
  };

  function $(id) {
    return document.getElementById(id);
  }

  function esc(texto) {
    return String(texto == null ? "" : texto)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* ------------------------- Armado del modal ----------------------------- */

  function inyectar() {
    if (state.inyectado) return;

    var contenedor = document.createElement("div");
    contenedor.innerHTML = [
      '<div id="modal-odontograma" class="odo-backdrop hide" role="dialog"',
      '     aria-modal="true" aria-labelledby="odo-titulo">',
      '  <div class="odo-card">',
      '    <header class="odo-cabecera">',
      '      <div>',
      '        <p class="odo-eyebrow">Odontograma</p>',
      '        <h3 id="odo-titulo" class="odo-titulo">Paciente</h3>',
      '        <p id="odo-sub" class="odo-sub">Cargando…</p>',
      '      </div>',
      '      <button type="button" id="odo-cerrar" class="odo-cerrar"',
      '              aria-label="Cerrar">✕</button>',
      '    </header>',
      '    <div class="odo-paleta" role="group" aria-label="Color para pintar">',
      '      <button type="button" class="odo-color" data-color="EXISTENTE">',
      '        <span class="odo-punto odo-existente"></span>Prestación existente</button>',
      '      <button type="button" class="odo-color" data-color="REQUERIDO">',
      '        <span class="odo-punto odo-requerido"></span>Prestación requerida</button>',
      '      <button type="button" class="odo-color" data-color="AUSENTE">',
      '        <span class="odo-punto odo-punto-x">✕</span>Ausente / a extraer</button>',
      '      <button type="button" class="odo-color" data-color="">',
      '        <span class="odo-punto odo-punto-blanco"></span>Borrar marca</button>',
      '    </div>',
      '    <p class="odo-ayuda">Tocá un diente y después elegí un color para',
      '       pintarlo. También podés elegir el color y tocar varios dientes.',
      '       «Borrar marca» limpia el diente seleccionado.</p>',
      '    <div class="odo-arcadas">',
      '      <div class="odo-arcada">',
      '        <p class="odo-arcada-titulo">Arcada superior</p>',
      '        <div class="odo-fila" data-fila="sup-der"></div>',
      '        <div class="odo-separador" aria-hidden="true"></div>',
      '        <div class="odo-fila" data-fila="sup-izq"></div>',
      '      </div>',
      '      <div class="odo-arcada">',
      '        <p class="odo-arcada-titulo">Arcada inferior</p>',
      '        <div class="odo-fila" data-fila="inf-der"></div>',
      '        <div class="odo-separador" aria-hidden="true"></div>',
      '        <div class="odo-fila" data-fila="inf-izq"></div>',
      '      </div>',
      '    </div>',
      '    <div class="odo-grid">',
      '      <label class="odo-campo">Prótesis fija',
      '        <input id="odo-protesis-fija" class="odo-control" maxlength="1000" /></label>',
      '      <label class="odo-campo">Prótesis removible',
      '        <input id="odo-protesis-removible" class="odo-control" maxlength="1000" /></label>',
      '      <label class="odo-campo">Coronas',
      '        <input id="odo-coronas" class="odo-control" maxlength="1000" /></label>',
      '      <label class="odo-campo">Cantidad de dientes existentes',
      '        <input id="odo-dientes-existentes" type="number" min="0" max="32"',
      '               class="odo-control" /></label>',
      '    </div>',
      '    <label class="odo-campo odo-campo-ancho">Observaciones',
      '      <textarea id="odo-observaciones" class="odo-control" rows="2"',
      '                maxlength="1000"></textarea></label>',
      '    <p id="odo-totales" class="odo-totales"></p>',
      '    <p id="odo-error" class="odo-error hide" role="alert"></p>',
      '    <footer class="odo-acciones">',
      '      <button type="button" id="odo-cancelar" class="odo-btn">Cerrar</button>',
      '      <button type="button" id="odo-guardar" class="odo-btn odo-btn-primario">',
      '        Guardar odontograma</button>',
      '    </footer>',
      '  </div>',
      '</div>'
    ].join("\n");

    var modal = contenedor.firstElementChild;
    document.body.appendChild(modal);

    ARCADAS.forEach(function (arcada) {
      var fila = modal.querySelector('[data-fila="' + arcada.id + '"]');
      arcada.dientes.forEach(function (diente) {
        fila.appendChild(botonDiente(diente));
      });
    });

    state.inyectado = true;
    conectar(modal);
  }

  function botonDiente(diente) {
    var boton = document.createElement("button");
    boton.type = "button";
    boton.className = "odo-diente";
    boton.setAttribute("data-diente", String(diente));
    boton.setAttribute("aria-pressed", "false");
    boton.setAttribute("aria-label", "Diente " + diente);
    boton.title = "Diente " + diente;

    var numero = document.createElement("span");
    numero.className = "odo-numero";
    numero.textContent = String(diente);

    var marca = document.createElement("span");
    marca.className = "odo-marca";
    marca.textContent = "✕";

    boton.appendChild(numero);
    boton.appendChild(marca);
    return boton;
  }

  function conectar(modal) {
    modal.addEventListener("click", function (evento) {
      if (evento.target === modal) {
        cerrar();
        return;
      }
      var color = evento.target.closest("[data-color]");
      if (color) {
        seleccionar(color.getAttribute("data-color"));
        return;
      }
      var diente = evento.target.closest("[data-diente]");
      if (diente) {
        var numero = Number(diente.getAttribute("data-diente"));
        seleccionarDiente(numero);
        if (state.seleccion !== null) pintar(numero, state.seleccion);
      }
    });

    $("odo-cerrar").addEventListener("click", cerrar);
    $("odo-cancelar").addEventListener("click", cerrar);
    $("odo-guardar").addEventListener("click", guardar);

    document.addEventListener("keydown", function (evento) {
      if (evento.key === "Escape" && !modal.classList.contains("hide")) {
        cerrar();
      }
    });
  }

  /* --------------------------- Pintado ----------------------------------- */

  function seleccionar(estado) {
    if (estado && !ESTADOS[estado]) return;
    state.seleccion = estado;
    var botones = document.querySelectorAll("[data-color]");
    Array.prototype.forEach.call(botones, function (boton) {
      var activo =
        estado !== null && boton.getAttribute("data-color") === estado;
      boton.classList.toggle("odo-color-activo", activo);
      boton.setAttribute("aria-pressed", activo ? "true" : "false");
    });
    // Orden del consultorio: primero se toca el diente y después el color.
    if (state.dienteSeleccionado !== null) {
      pintar(state.dienteSeleccionado, estado);
    }
  }

  function seleccionarDiente(diente) {
    state.dienteSeleccionado = diente;
    var botones = document.querySelectorAll("[data-diente]");
    Array.prototype.forEach.call(botones, function (boton) {
      boton.classList.toggle(
        "odo-diente-activo",
        diente !== null &&
          Number(boton.getAttribute("data-diente")) === diente
      );
    });
  }

  function pintar(diente, estado) {
    if (estado) state.dientes[diente] = estado;
    else delete state.dientes[diente];
    pintarDiente(diente);
    actualizarTotales();
  }

  function pintarDiente(diente) {
    var boton = document.querySelector('[data-diente="' + diente + '"]');
    if (!boton) return;
    var estado = state.dientes[diente];
    boton.className =
      "odo-diente" + (estado ? " " + ESTADOS[estado].clase : "");
    boton.setAttribute("aria-pressed", estado ? "true" : "false");
    boton.title = estado
      ? ESTADOS[estado].etiqueta
      : "Diente " + diente + " sin marca";
  }

  function repintar() {
    var botones = document.querySelectorAll("[data-diente]");
    Array.prototype.forEach.call(botones, function (boton) {
      pintarDiente(Number(boton.getAttribute("data-diente")));
    });
  }

  function contar(estado) {
    return Object.keys(state.dientes).filter(function (clave) {
      return state.dientes[clave] === estado;
    }).length;
  }

  function actualizarTotales() {
    var caja = $("odo-totales");
    if (!caja) return;
    caja.textContent =
      "Existentes: " + contar("EXISTENTE") +
      " · Requeridos: " + contar("REQUERIDO") +
      " · Ausentes: " + contar("AUSENTE");
  }

  /* ------------------------------ Datos ---------------------------------- */

  function fechaHora(iso) {
    var texto = String(iso || "");
    if (texto.length < 10) return "";
    var partes = texto.slice(0, 10).split("-");
    var hora = texto.slice(11, 16);
    return partes[2] + "/" + partes[1] + "/" + partes[0] + (hora ? " " + hora + " h" : "");
  }

  function aplicar(datos) {
    state.dientes = {};
    (datos.dientes || []).forEach(function (item) {
      if (ESTADOS[item.estado]) state.dientes[item.diente] = item.estado;
    });

    $("odo-protesis-fija").value = datos.protesis_fija || "";
    $("odo-protesis-removible").value = datos.protesis_removible || "";
    $("odo-coronas").value = datos.coronas || "";
    $("odo-dientes-existentes").value =
      datos.dientes_existentes === null || datos.dientes_existentes === undefined
        ? ""
        : String(datos.dientes_existentes);
    $("odo-observaciones").value = datos.observaciones || "";

    repintar();
    actualizarTotales();
    seleccionarDiente(null);

    var marcados =
      (datos.total_existentes || 0) +
      (datos.total_requeridos || 0) +
      (datos.total_ausentes || 0);
    $("odo-sub").textContent = datos.actualizado_en
      ? "Última actualización: " + fechaHora(datos.actualizado_en) +
        " · " + marcados + " diente/s marcados"
      : "Todavía no se guardó ningún diente.";
  }

  /* ------------------------- Abrir y cerrar ------------------------------ */

  function limpiarFormulario() {
    $("odo-protesis-fija").value = "";
    $("odo-protesis-removible").value = "";
    $("odo-coronas").value = "";
    $("odo-dientes-existentes").value = "";
    $("odo-observaciones").value = "";
  }

  function abrir(pacienteId, nombre) {
    inyectar();
    state.pacienteId = Number(pacienteId);
    state.nombre = nombre || "Paciente #" + pacienteId;
    state.dientes = {};

    ocultarError();
    limpiarFormulario();
    repintar();
    actualizarTotales();
    seleccionarDiente(null);
    seleccionar(null);

    $("odo-titulo").textContent = "Odontograma · " + state.nombre;
    $("odo-sub").textContent = "Cargando…";
    mostrar(true);

    return pedir("GET")
      .then(aplicar)
      .catch(function (error) {
        mostrarError(error.message);
      });
  }

  function mostrar(abierto) {
    var modal = $("modal-odontograma");
    if (!modal) return;
    modal.classList.toggle("hide", !abierto);
    document.body.classList.toggle("odo-con-modal", Boolean(abierto));
    if (abierto) {
      var primero = modal.querySelector("[data-color]");
      if (primero) primero.focus({ preventScroll: true });
    }
  }

  function cerrar() {
    mostrar(false);
  }

  function bloquear(activo) {
    var boton = $("odo-guardar");
    if (!boton) return;
    boton.disabled = activo;
    boton.textContent = activo ? "Guardando…" : "Guardar odontograma";
  }

  function mostrarError(mensaje) {
    var caja = $("odo-error");
    if (!caja) return;
    caja.textContent = mensaje;
    caja.classList.remove("hide");
  }

  function ocultarError() {
    var caja = $("odo-error");
    if (!caja) return;
    caja.textContent = "";
    caja.classList.add("hide");
  }

  function avisar(titulo, texto) {
    var caja = document.createElement("div");
    caja.className = "odo-toast";
    var fuerte = document.createElement("strong");
    fuerte.textContent = titulo;
    var detalle = document.createElement("span");
    detalle.textContent = texto;
    caja.appendChild(fuerte);
    caja.appendChild(detalle);
    document.body.appendChild(caja);
    setTimeout(function () {
      caja.classList.add("odo-toast-salir");
    }, 3600);
    setTimeout(function () {
      if (caja.parentNode) caja.parentNode.removeChild(caja);
    }, 4400);
  }

  /* ------------------------------- API ----------------------------------- */

  function pedir(metodo, cuerpo) {
    var opciones = {
      method: metodo,
      headers: { Accept: "application/json" },
      credentials: "same-origin",
      cache: "no-store"
    };
    if (cuerpo !== undefined) {
      opciones.headers["Content-Type"] = "application/json";
      opciones.body = JSON.stringify(cuerpo);
    }

    var url = API + encodeURIComponent(state.pacienteId) + "/odontograma";
    return fetch(url, opciones).then(
      function (respuesta) {
        return respuesta
          .json()
          .catch(function () {
            return null;
          })
          .then(function (datos) {
            if (respuesta.ok) return datos;
            var detalle = datos && datos.detail;
            if (Array.isArray(detalle) && detalle.length) {
              detalle = detalle[0] && detalle[0].msg;
            }
            var mensaje =
              typeof detalle === "string" && detalle
                ? detalle
                : respuesta.status === 404
                  ? "No encontramos al paciente. Actualizá el panel e intentá de nuevo."
                  : "No pudimos guardar el odontograma. Intentá de nuevo.";
            throw new Error(mensaje);
          });
      },
      function () {
        throw new Error(
          "No pudimos conectarnos con el servidor. Revisá la conexión e intentá de nuevo."
        );
      }
    );
  }

  function guardar() {
    if (state.guardando) return Promise.resolve();

    var dientes = Object.keys(state.dientes)
      .map(function (clave) {
        return { diente: Number(clave), estado: state.dientes[clave] };
      })
      .sort(function (a, b) {
        return a.diente - b.diente;
      });

    var existentes = String($("odo-dientes-existentes").value || "").trim();
    var payload = {
      protesis_fija: String($("odo-protesis-fija").value || "").trim() || null,
      protesis_removible:
        String($("odo-protesis-removible").value || "").trim() || null,
      coronas: String($("odo-coronas").value || "").trim() || null,
      dientes_existentes: existentes ? parseInt(existentes, 10) : null,
      observaciones: String($("odo-observaciones").value || "").trim() || null,
      dientes: dientes
    };

    state.guardando = true;
    bloquear(true);
    ocultarError();

    return pedir("PUT", payload)
      .then(function (datos) {
        aplicar(datos);
        document.dispatchEvent(
          new CustomEvent("odontograma:guardado", { detail: datos })
        );
        cerrar();
        avisar(
          "Odontograma guardado",
          dientes.length + " diente/s marcados para " + state.nombre + "."
        );
      })
      .catch(function (error) {
        mostrarError(error.message);
      })
      .then(function () {
        state.guardando = false;
        bloquear(false);
      });
  }

  /* --------------------------- Integración ------------------------------- */

  document.addEventListener("click", function (evento) {
    var boton = evento.target.closest("[data-odontograma]");
    if (!boton) return;
    evento.preventDefault();
    abrir(
      boton.getAttribute("data-odontograma"),
      boton.getAttribute("data-odontograma-nombre") || ""
    );
  });

  window.Odontograma = {
    abrir: abrir,
    cerrar: cerrar
  };
})();
