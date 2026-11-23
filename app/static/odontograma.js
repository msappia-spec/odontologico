/* ==========================================================================
   Ficha médica del paciente (Módulo 4)
   --------------------------------------------------------------------------
   Reemplaza el modal del odontograma por la hoja completa de la Historia
   Clínica General que el consultorio usa en papel:

   * DATOS DEL PACIENTE: los filiatorios que ya viven en el registro del
     paciente (nombre, obra social, afiliado, nacimiento, domicilio, teléfono,
     lugar de trabajo, jerarquía y titular) más los datos propios de la hoja
     (grupo familiar, parentesco, antecedentes de enfermedad y vencimiento).
   * ODONTOGRAMA: dentición permanente (11-48) y temporaria (51-85) con los
     colores del papel: ROJO = prestación existente, AZUL = prestación
     requerida, X = ausente o a extraer. A la derecha, la leyenda con prótesis,
     coronas, cantidad de dientes y observaciones.
   * ESTADO BUCAL GENERAL: presencia de sarro SI/NO y enfermedad periodontal
     SI/NO.
   * TRABAJO REALIZADO: libro con Fecha, Código (elegido del catálogo de
     prestaciones), Pieza dentaria, Trabajo realizado, Debe, Haber y Saldo
     (acumulado, se calcula solo).

   Se usa desde el Panel Principal y desde la vista móvil: el modal se inyecta
   en el ``body`` la primera vez que se abre. Las páginas sólo necesitan
   incluir este archivo y un botón con ``data-ficha-medica="<paciente_id>"``
   (se sigue aceptando ``data-odontograma`` por compatibilidad).

   El botón «Editar datos» de la cabecera delega en
   ``window.OdontoApp.editarPaciente``: cierra la hoja y abre el formulario de
   «Pacientes» con los filiatorios precargados. En la vista móvil, donde no
   existe ese puente, el botón queda oculto.

   El guardado valida la hoja completa antes de escribir y después hace dos
   llamadas: el odontograma (``PUT .../odontograma``) y la ficha médica
   (``PUT .../ficha-medica``). Si la segunda falla —por un error del servidor
   que la validación local no pudo anticipar— se avisa que los dientes ya
   quedaron guardados.
   ========================================================================== */

(function () {
  "use strict";

  var API = "/api/v1/pacientes/";
  var API_PRESTACIONES = "/api/v1/prestaciones?activas=true";

  /* Topes que también valida la API: se comprueban antes de enviar para poder
     explicar el error en castellano y sin guardar medio formulario. */
  var MAX_CODIGO = 30;
  var MAX_PIEZA = 30;
  var MAX_TRABAJO = 500;
  var MAX_RENGLONES = 100;
  var MAX_IMPORTE = 99999999;

  var ETIQUETAS_TRABAJO = {
    fecha: "Fecha",
    codigo: "Código",
    pieza: "Pieza dentaria",
    trabajo: "Trabajo realizado",
    debe: "Debe",
    haber: "Haber"
  };

  var ETIQUETAS_CABECERA = {
    vencimiento: "Vencimiento",
    grupo_familiar: "Grupo familiar",
    parentesco: "Parentesco",
    antecedentes_enfermedad: "Antecedentes de enfermedad",
    presencia_sarro: "Presencia de sarro",
    enfermedad_periodontal: "Enfermedad periodontal",
    protesis_fija: "Prótesis fija",
    protesis_removible: "Prótesis removible",
    coronas: "Coronas",
    dientes_existentes: "Cantidad de dientes existentes",
    observaciones: "Observaciones",
    trabajos: "Trabajo realizado"
  };

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
    { id: "inf-izq", dientes: [31, 32, 33, 34, 35, 36, 37, 38] },
    { id: "temp-sup-der", dientes: [55, 54, 53, 52, 51] },
    { id: "temp-sup-izq", dientes: [61, 62, 63, 64, 65] },
    { id: "temp-inf-der", dientes: [85, 84, 83, 82, 81] },
    { id: "temp-inf-izq", dientes: [71, 72, 73, 74, 75] }
  ];

  /* Renglones en blanco que se muestran al abrir la hoja (el papel tiene 16;
     con 6 el libro ya se puede escribir y el resto se agrega con el botón). */
  var MIN_FILAS_TRABAJO = 6;

  var state = {
    pacienteId: null,
    nombre: "",
    dientes: {},
    seleccion: null,
    dienteSeleccionado: null,
    guardando: false,
    inyectado: false,
    // Sólo se puede guardar cuando las dos lecturas llegaron: así un error de
    // red no reemplaza el odontograma o el libro existente con datos vacíos.
    fichaLista: false,
    odontogramaListo: false,
    // Catálogo de prestaciones activas (GET /prestaciones?activas=true). El
    // código de cada renglón se elige de acá; el mapa permite resolverlo sin
    // recorrer la lista en cada tecla.
    prestaciones: [],
    prestacionesPorCodigo: {},
    prestacionesCargando: null,
    prestacionesListas: false
  };

  function $(id) {
    return document.getElementById(id);
  }

  function valor(id) {
    var campo = $(id);
    return campo ? String(campo.value || "").trim() : "";
  }

  function textoONull(id) {
    var texto = valor(id);
    return texto || null;
  }

  function fechaLegible(iso) {
    var texto = String(iso || "").slice(0, 10);
    if (texto.length < 10) return "";
    var partes = texto.split("-");
    return partes[2] + "/" + partes[1] + "/" + partes[0];
  }

  function fechaHora(iso) {
    var texto = String(iso || "");
    if (texto.length < 10) return "";
    var hora = texto.slice(11, 16);
    return fechaLegible(texto) + (hora ? " " + hora + " h" : "");
  }

  function dinero(numero) {
    var valor = Number(numero || 0);
    if (!isFinite(valor)) valor = 0;
    return valor.toLocaleString("es-AR", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  }

  /* ------------------------- Armado del modal ----------------------------- */

  function inyectar() {
    if (state.inyectado) return;

    var contenedor = document.createElement("div");
    contenedor.innerHTML = [
      '<div id="modal-ficha-medica" class="odo-backdrop hide" role="dialog"',
      '     aria-modal="true" aria-labelledby="fm-titulo">',
      '  <div class="odo-card odo-card-ficha">',
      '    <header class="odo-cabecera">',
      '      <div>',
      '        <p class="odo-eyebrow">Historia clínica</p>',
      '        <h3 id="fm-titulo" class="odo-titulo">Ficha médica</h3>',
      '        <p id="fm-sub" class="odo-sub">Cargando…</p>',
      '      </div>',
      '      <button type="button" id="fm-cerrar" class="odo-cerrar"',
      '              aria-label="Cerrar">✕</button>',
      '    </header>',
      '    <section class="odo-datos" aria-label="Datos del paciente">',
      '      <div class="odo-datos-cabecera">',
      '        <h4 class="odo-datos-titulo">Datos del paciente</h4>',
      '        <div class="odo-datos-acciones">',
      '          <p class="odo-datos-nota">Los filiatorios se editan en el registro del paciente.</p>',
      '          <button type="button" id="fm-editar-paciente"',
      '                  class="odo-btn odo-btn-mini hide"',
      '                  title="Abrir el formulario del paciente para corregir sus datos">',
      '            Editar datos</button>',
      '        </div>',
      '      </div>',
      '      <div class="odo-datos-grid">',
      '        <p class="odo-dato"><span class="odo-dato-etiqueta">Apellido y nombres</span>',
      '          <span id="fm-nombre" class="odo-dato-valor">—</span></p>',
      '        <p class="odo-dato"><span class="odo-dato-etiqueta">Obra social</span>',
      '          <span id="fm-obra-social" class="odo-dato-valor">—</span></p>',
      '        <p class="odo-dato"><span class="odo-dato-etiqueta">N° de afiliado</span>',
      '          <span id="fm-afiliado" class="odo-dato-valor">—</span></p>',
      '        <p class="odo-dato"><span class="odo-dato-etiqueta">Fecha de nacimiento</span>',
      '          <span id="fm-nacimiento" class="odo-dato-valor">—</span></p>',
      '        <p class="odo-dato odo-dato-ancho">',
      '          <span class="odo-dato-etiqueta">Domicilio (calle, núm., barrio, localidad)</span>',
      '          <span id="fm-domicilio" class="odo-dato-valor">—</span></p>',
      '        <p class="odo-dato"><span class="odo-dato-etiqueta">Teléfono</span>',
      '          <span id="fm-telefono" class="odo-dato-valor">—</span></p>',
      '        <p class="odo-dato"><span class="odo-dato-etiqueta">Lugar de trabajo del titular</span>',
      '          <span id="fm-trabajo" class="odo-dato-valor">—</span></p>',
      '        <p class="odo-dato"><span class="odo-dato-etiqueta">Jerarquía</span>',
      '          <span id="fm-jerarquia" class="odo-dato-valor">—</span></p>',
      '        <p class="odo-dato"><span class="odo-dato-etiqueta">Titular</span>',
      '          <span id="fm-titular" class="odo-dato-valor">—</span></p>',
      '        <label class="odo-dato"><span class="odo-dato-etiqueta">Grupo familiar</span>',
      '          <input id="fm-grupo" class="odo-dato-control" maxlength="120" /></label>',
      '        <label class="odo-dato"><span class="odo-dato-etiqueta">Parentesco</span>',
      '          <input id="fm-parentesco" class="odo-dato-control" maxlength="60" /></label>',
      '        <label class="odo-dato"><span class="odo-dato-etiqueta">Vto</span>',
      '          <input id="fm-vencimiento" type="date" class="odo-dato-control" /></label>',
      '        <label class="odo-dato odo-dato-ancho">',
      '          <span class="odo-dato-etiqueta">Antecedentes de enfermedad</span>',
      '          <textarea id="fm-antecedentes" class="odo-dato-control" rows="2"',
      '                    maxlength="1000"></textarea></label>',
      '      </div>',
      '    </section>',
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
      '    <div class="odo-ficha-cuerpo">',
      '      <div class="odo-arcadas">',
      '        <div class="odo-arcada">',
      '          <p class="odo-arcada-titulo">Dentición permanente</p>',
      '          <div class="odo-fila">',
      '            <div class="odo-grupo" data-fila="sup-der"></div>',
      '            <div class="odo-separador-vertical" aria-hidden="true"></div>',
      '            <div class="odo-grupo" data-fila="sup-izq"></div>',
      '          </div>',
      '          <div class="odo-separador" aria-hidden="true"></div>',
      '          <div class="odo-fila">',
      '            <div class="odo-grupo" data-fila="inf-der"></div>',
      '            <div class="odo-separador-vertical" aria-hidden="true"></div>',
      '            <div class="odo-grupo" data-fila="inf-izq"></div>',
      '          </div>',
      '          <p class="odo-arcada-titulo odo-arcada-titulo-temp">Dentición temporaria</p>',
      '          <div class="odo-fila odo-fila-temp">',
      '            <span class="odo-lado">Derecha</span>',
      '            <div class="odo-grupo" data-fila="temp-sup-der"></div>',
      '            <div class="odo-separador-vertical" aria-hidden="true"></div>',
      '            <div class="odo-grupo" data-fila="temp-sup-izq"></div>',
      '            <span class="odo-lado">Izquierda</span>',
      '          </div>',
      '          <div class="odo-separador" aria-hidden="true"></div>',
      '          <div class="odo-fila odo-fila-temp">',
      '            <span class="odo-lado">Derecha</span>',
      '            <div class="odo-grupo" data-fila="temp-inf-der"></div>',
      '            <div class="odo-separador-vertical" aria-hidden="true"></div>',
      '            <div class="odo-grupo" data-fila="temp-inf-izq"></div>',
      '            <span class="odo-lado">Izquierda</span>',
      '          </div>',
      '        </div>',
      '      </div>',
      '      <aside class="odo-leyenda" aria-label="Prótesis y observaciones">',
      '        <p class="odo-leyenda-titulo">Prótesis y observaciones</p>',
      '        <label class="odo-campo">Prótesis fija',
      '          <input id="fm-protesis-fija" class="odo-control" maxlength="1000" /></label>',
      '        <label class="odo-campo">Prótesis removible',
      '          <input id="fm-protesis-removible" class="odo-control" maxlength="1000" /></label>',
      '        <label class="odo-campo">Coronas',
      '          <input id="fm-coronas" class="odo-control" maxlength="1000" /></label>',
      '        <label class="odo-campo">Cantidad de dientes existentes',
      '          <input id="fm-dientes-existentes" type="number" min="0" max="32"',
      '                 class="odo-control" /></label>',
      '        <div class="odo-referencias">',
      '          <span><span class="odo-punto odo-existente"></span> Existente</span>',
      '          <span><span class="odo-punto odo-requerido"></span> Requerida</span>',
      '          <span><span class="odo-punto odo-punto-x">✕</span> Ausente / a extraer</span>',
      '        </div>',
      '        <label class="odo-campo">Observaciones',
      '          <textarea id="fm-observaciones" class="odo-control" rows="2"',
      '                    maxlength="1000"></textarea></label>',
      '      </aside>',
      '    </div>',
      '    <section class="odo-estado" aria-label="Estado bucal general">',
      '      <p class="odo-estado-titulo">Estado bucal general</p>',
      '      <div class="odo-si-no"><span>Presencia de sarro</span>',
      '        <label><input type="radio" name="fm-sarro" value="SI" /> SI</label>',
      '        <label><input type="radio" name="fm-sarro" value="NO" /> NO</label>',
      '      </div>',
      '      <div class="odo-si-no"><span>Enfermedad periodontal</span>',
      '        <label><input type="radio" name="fm-periodontal" value="SI" /> SI</label>',
      '        <label><input type="radio" name="fm-periodontal" value="NO" /> NO</label>',
      '      </div>',
      '    </section>',
      '    <section class="odo-trabajos" aria-label="Trabajo realizado">',
      '      <div class="odo-trabajos-cabecera">',
      '        <p class="odo-leyenda-titulo">Trabajo realizado</p>',
      '        <button type="button" id="fm-agregar-trabajo"',
      '                class="odo-btn odo-btn-mini">+ Agregar renglón</button>',
      '      </div>',
      '      <p id="fm-prestaciones-estado" class="odo-trabajos-nota">',
      '        Código del catálogo de prestaciones: elegí uno de la lista para',
      '        completar el trabajo y el importe.</p>',
      '      <div class="odo-tabla-scroll">',
      '        <table class="odo-tabla">',
      '          <thead><tr>',
      '            <th>Fecha</th>',
      '            <th title="Código del catálogo de prestaciones">Código</th>',
      '            <th>Pieza dentaria</th>',
      '            <th>Trabajo realizado</th>',
      '            <th class="odo-num">Debe</th><th class="odo-num">Haber</th>',
      '            <th class="odo-num">Saldo</th><th aria-label="Quitar"></th>',
      '          </tr></thead>',
      '          <tbody id="fm-trabajos"></tbody>',
      '        </table>',
      '      </div>',
      '      <div class="odo-totales-libro">',
      '        <span>Debe: $<strong id="fm-total-debe">0,00</strong></span>',
      '        <span>Haber: $<strong id="fm-total-haber">0,00</strong></span>',
      '        <span>Saldo: $<strong id="fm-total-saldo">0,00</strong></span>',
      '      </div>',
      '      <datalist id="fm-prestaciones"></datalist>',
      '    </section>',
      '    <p id="fm-totales" class="odo-totales"></p>',
      '    <p id="fm-error" class="odo-error hide" role="alert"></p>',
      '    <footer class="odo-acciones">',
      '      <button type="button" id="fm-imprimir" class="odo-btn">Imprimir</button>',
      '      <button type="button" id="fm-cancelar" class="odo-btn">Cerrar</button>',
      '      <button type="button" id="fm-guardar" class="odo-btn odo-btn-primario">',
      '        Guardar ficha</button>',
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

  /* --------------------------- Conexión ---------------------------------- */

  /* El panel principal expone `editarPaciente` en `window.OdontoApp`; en la
     vista móvil no existe, así que el botón se oculta en lugar de quedar sin
     efecto. */
  function puedeEditarPaciente() {
    return Boolean(
      window.OdontoApp &&
        typeof window.OdontoApp.editarPaciente === "function"
    );
  }

  function actualizarBotonEditarPaciente() {
    var boton = $("fm-editar-paciente");
    if (boton) boton.classList.toggle("hide", !puedeEditarPaciente());
  }

  function editarDatosDelPaciente() {
    if (!state.pacienteId || !puedeEditarPaciente()) return;
    var id = state.pacienteId;
    cerrar();
    window.OdontoApp.editarPaciente(id);
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
        return;
      }
      var quitar = evento.target.closest("[data-quitar-trabajo]");
      if (quitar) {
        var fila = quitar.closest("tr");
        if (fila) fila.parentNode.removeChild(fila);
        asegurarFilasTrabajo();
        recalcularSaldos();
      }
    });

    $("fm-cerrar").addEventListener("click", cerrar);
    $("fm-cancelar").addEventListener("click", cerrar);
    $("fm-guardar").addEventListener("click", guardar);
    $("fm-editar-paciente").addEventListener("click", editarDatosDelPaciente);
    $("fm-imprimir").addEventListener("click", function () {
      if (typeof window.print === "function") window.print();
    });
    $("fm-agregar-trabajo").addEventListener("click", function () {
      agregarFilaTrabajo({});
      recalcularSaldos();
    });
    $("fm-trabajos").addEventListener("input", recalcularSaldos);
    // El código se resuelve contra el catálogo al tipearlo o al elegir una
    // sugerencia del datalist. Si la prestación existe, se completan solos el
    // detalle y el importe cuando todavía están vacíos.
    $("fm-trabajos").addEventListener("input", autocompletarPrestacion);
    $("fm-trabajos").addEventListener("change", autocompletarPrestacion);
    // Al corregir un campo se quita la marca roja del error anterior.
    modal.addEventListener("input", function (evento) {
      if (evento.target && evento.target.removeAttribute) {
        evento.target.removeAttribute("aria-invalid");
      }
    });

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
    var caja = $("fm-totales");
    if (!caja) return;
    caja.textContent =
      "Existentes: " + contar("EXISTENTE") +
      " · Requeridos: " + contar("REQUERIDO") +
      " · Ausentes: " + contar("AUSENTE");
  }

  /* -------------------- Libro «Trabajo realizado» ------------------------- */

  /* El nomenclador puede escribir el mismo código con puntos o sin ellos
     (``10.01`` / ``1001``): para buscar se ignoran puntos, guiones y espacios.
     El valor que se guarda es siempre el código exacto del catálogo. */
  function normalizarCodigo(codigo) {
    return String(codigo || "")
      .trim()
      .toUpperCase()
      .replace(/[.\s-]/g, "");
  }

  function buscarPrestacion(codigo) {
    var clave = normalizarCodigo(codigo);
    return clave ? state.prestacionesPorCodigo[clave] || null : null;
  }

  function cargarPrestaciones() {
    if (state.prestacionesListas) return Promise.resolve(state.prestaciones);
    if (state.prestacionesCargando) return state.prestacionesCargando;

    var nota = $("fm-prestaciones-estado");
    if (nota) nota.textContent = "Cargando el catálogo de prestaciones…";

    state.prestacionesCargando = pedir(
      API_PRESTACIONES,
      "GET",
      undefined,
      "el catálogo de prestaciones"
    )
      .then(function (lista) {
        state.prestaciones = Array.isArray(lista) ? lista : [];
        state.prestacionesPorCodigo = {};
        state.prestaciones.forEach(function (prestacion) {
          var clave = normalizarCodigo(prestacion.codigo_nomenclador);
          if (clave) state.prestacionesPorCodigo[clave] = prestacion;
        });
        state.prestacionesListas = true;
        actualizarListaPrestaciones();
        if (nota) {
          nota.textContent = state.prestaciones.length
            ? "Código del catálogo de prestaciones (" +
              state.prestaciones.length +
              " activa" +
              (state.prestaciones.length === 1 ? "" : "s") +
              "): elegí uno de la lista para completar el trabajo y el importe."
            : "El catálogo no tiene prestaciones activas: escribí el código " +
              "a mano.";
        }
        return state.prestaciones;
      })
      .catch(function () {
        // La ficha se puede seguir usando a mano: el catálogo es una ayuda,
        // no un requisito para guardar un renglón antiguo o un pago a cuenta.
        state.prestaciones = [];
        state.prestacionesPorCodigo = {};
        state.prestacionesListas = false;
        actualizarListaPrestaciones();
        if (nota) {
          nota.textContent =
            "No se pudo cargar el catálogo de prestaciones: podés escribir " +
            "el código a mano, pero no se validará contra el nomenclador.";
        }
        return [];
      })
      .then(function (lista) {
        state.prestacionesCargando = null;
        return lista;
      });

    return state.prestacionesCargando;
  }

  function actualizarListaPrestaciones() {
    var lista = $("fm-prestaciones");
    if (!lista) return;
    var opciones = document.createDocumentFragment();
    state.prestaciones.forEach(function (prestacion) {
      var codigo = String(prestacion.codigo_nomenclador || "").trim();
      if (!codigo) return;
      var etiqueta =
        codigo + (prestacion.descripcion ? " · " + prestacion.descripcion : "");
      var opcion = document.createElement("option");
      opcion.value = codigo;
      opcion.label = etiqueta;
      opcion.textContent = etiqueta;
      opciones.appendChild(opcion);
    });
    lista.innerHTML = "";
    lista.appendChild(opciones);
  }

  function autocompletarPrestacion(evento) {
    var campo = evento.target && evento.target.closest
      ? evento.target.closest(".fm-trabajo-codigo")
      : null;
    if (!campo) return;
    var fila = campo.closest("tr");
    if (fila) aplicarPrestacionEnFila(fila, campo);
  }

  /* Completa detalle/importe sólo cuando siguen vacíos o conservan el valor
     que puso el catálogo: nunca pisa lo que la secretaría escribió a mano. */
  function aplicarPrestacionEnFila(fila, campoCodigo) {
    var prestacion = buscarPrestacion(campoCodigo.value);
    var original = String(fila.getAttribute("data-codigo-original") || "").trim();
    var desconocido =
      Boolean(String(campoCodigo.value || "").trim()) &&
      state.prestacionesListas &&
      state.prestaciones.length > 0 &&
      !prestacion &&
      normalizarCodigo(campoCodigo.value) !== normalizarCodigo(original);
    campoCodigo.classList.toggle("odo-codigo-desconocido", desconocido);
    campoCodigo.title = desconocido
      ? "Código no encontrado en el catálogo de prestaciones"
      : "Código del catálogo de prestaciones";
    if (!prestacion) return;

    campoCodigo.value = String(prestacion.codigo_nomenclador || "").trim();

    var detalle = fila.querySelector(".fm-trabajo-detalle");
    if (detalle) {
      var detalleAuto = fila.getAttribute("data-detalle-auto");
      var detalleActual = String(detalle.value || "").trim();
      if (!detalleActual || (detalleAuto !== null && detalleActual === detalleAuto)) {
        detalle.value = prestacion.descripcion || "";
        fila.setAttribute("data-detalle-auto", detalle.value);
      }
    }

    var debe = fila.querySelector(".fm-trabajo-debe");
    if (debe) {
      var debeAuto = fila.getAttribute("data-debe-auto");
      var debeActual = String(debe.value || "").trim();
      var base =
        prestacion.valor_base === null || prestacion.valor_base === undefined
          ? ""
          : String(prestacion.valor_base);
      if (!debeActual || (debeAuto !== null && debeActual === debeAuto)) {
        if (base && Number(base) > 0) {
          debe.value = base;
          fila.setAttribute("data-debe-auto", base);
        } else if (debeAuto !== null) {
          debe.value = "";
          fila.removeAttribute("data-debe-auto");
        }
      }
    }

    recalcularSaldos();
  }

  function agregarFilaTrabajo(datos) {
    var cuerpo = $("fm-trabajos");
    if (!cuerpo) return;

    var fila = document.createElement("tr");
    fila.setAttribute("data-trabajo", "");
    // Código con el que vino el renglón: permite seguir guardando una ficha
    // vieja cuyo código ya no esté en el catálogo sin obligar a cambiarlo.
    fila.setAttribute("data-codigo-original", String(datos.codigo || ""));

    fila.appendChild(celdaEntrada("date", "fm-trabajo-fecha", datos.fecha || ""));
    var celdaCodigo = celdaEntrada(
      "text",
      "fm-trabajo-codigo",
      datos.codigo || "",
      MAX_CODIGO
    );
    var campoCodigo = celdaCodigo.firstElementChild;
    campoCodigo.setAttribute("list", "fm-prestaciones");
    campoCodigo.setAttribute("autocomplete", "off");
    campoCodigo.setAttribute("placeholder", "Código");
    campoCodigo.title = "Código del catálogo de prestaciones";
    fila.appendChild(celdaCodigo);
    fila.appendChild(
      celdaEntrada("text", "fm-trabajo-pieza", datos.pieza || "", MAX_PIEZA)
    );
    fila.appendChild(
      celdaEntrada("text", "fm-trabajo-detalle", datos.trabajo || "", MAX_TRABAJO)
    );
    fila.appendChild(celdaNumero("fm-trabajo-debe", datos.debe));
    fila.appendChild(celdaNumero("fm-trabajo-haber", datos.haber));

    var saldo = document.createElement("td");
    saldo.className = "odo-num odo-saldo";
    saldo.textContent = "$ 0,00";
    fila.appendChild(saldo);

    var acciones = document.createElement("td");
    var quitar = document.createElement("button");
    quitar.type = "button";
    quitar.className = "odo-quitar";
    quitar.setAttribute("data-quitar-trabajo", "");
    quitar.setAttribute("aria-label", "Quitar renglón");
    quitar.title = "Quitar renglón";
    quitar.textContent = "✕";
    acciones.appendChild(quitar);
    fila.appendChild(acciones);

    cuerpo.appendChild(fila);
  }

  function celdaEntrada(tipo, clase, valorInicial, largo) {
    var celda = document.createElement("td");
    var campo = document.createElement("input");
    campo.type = tipo;
    campo.className = "odo-celda " + clase;
    campo.value = valorInicial == null ? "" : String(valorInicial);
    if (largo) campo.maxLength = largo;
    celda.appendChild(campo);
    return celda;
  }

  function celdaNumero(clase, valorInicial) {
    var celda = document.createElement("td");
    celda.className = "odo-num";
    var campo = document.createElement("input");
    campo.type = "number";
    campo.className = "odo-celda " + clase;
    campo.min = "0";
    campo.max = String(MAX_IMPORTE);
    campo.step = "0.01";
    campo.inputMode = "decimal";
    campo.value =
      valorInicial === null || valorInicial === undefined
        ? ""
        : String(valorInicial);
    celda.appendChild(campo);
    return celda;
  }

  function filasTrabajo() {
    var cuerpo = $("fm-trabajos");
    return cuerpo ? Array.prototype.slice.call(cuerpo.children) : [];
  }

  function asegurarFilasTrabajo() {
    var faltan = MIN_FILAS_TRABAJO - filasTrabajo().length;
    for (var i = 0; i < faltan; i += 1) agregarFilaTrabajo({});
  }

  function numeroDe(campo) {
    var texto = String(campo.value || "").replace(",", ".").trim();
    if (!texto) return null;
    var numero = Number(texto);
    return isFinite(numero) ? numero : null;
  }

  function recalcularSaldos() {
    var saldo = 0;
    var totalDebe = 0;
    var totalHaber = 0;

    filasTrabajo().forEach(function (fila) {
      var debe = numeroDe(fila.querySelector(".fm-trabajo-debe"));
      var haber = numeroDe(fila.querySelector(".fm-trabajo-haber"));
      debe = debe === null ? 0 : debe;
      haber = haber === null ? 0 : haber;
      saldo = Math.round((saldo + debe - haber) * 100) / 100;
      totalDebe = Math.round((totalDebe + debe) * 100) / 100;
      totalHaber = Math.round((totalHaber + haber) * 100) / 100;
      var celda = fila.querySelector(".odo-saldo");
      if (celda) celda.textContent = "$ " + dinero(saldo);
    });

    var cajaDebe = $("fm-total-debe");
    var cajaHaber = $("fm-total-haber");
    var cajaSaldo = $("fm-total-saldo");
    if (cajaDebe) cajaDebe.textContent = dinero(totalDebe);
    if (cajaHaber) cajaHaber.textContent = dinero(totalHaber);
    if (cajaSaldo) cajaSaldo.textContent = dinero(saldo);
  }

  function aplicarTrabajos(lista) {
    var cuerpo = $("fm-trabajos");
    if (!cuerpo) return;
    cuerpo.innerHTML = "";
    (lista || []).forEach(function (fila) {
      agregarFilaTrabajo(fila);
    });
    asegurarFilasTrabajo();
    recalcularSaldos();
  }

  function leerTrabajos() {
    var renglones = [];
    filasTrabajo().forEach(function (fila) {
      var fecha = fila.querySelector(".fm-trabajo-fecha").value.trim();
      var codigo = fila.querySelector(".fm-trabajo-codigo").value.trim();
      var pieza = fila.querySelector(".fm-trabajo-pieza").value.trim();
      var trabajo = fila.querySelector(".fm-trabajo-detalle").value.trim();
      var debe = numeroDe(fila.querySelector(".fm-trabajo-debe"));
      var haber = numeroDe(fila.querySelector(".fm-trabajo-haber"));
      if (
        !fecha && !codigo && !pieza && !trabajo &&
        debe === null && haber === null
      ) {
        return;
      }
      renglones.push({
        fecha: fecha || null,
        codigo: codigo || null,
        pieza: pieza || null,
        trabajo: trabajo || null,
        debe: debe,
        haber: haber
      });
    });
    return renglones;
  }

  /* --------------------------- Validación -------------------------------- */

  function campoConFechaInvalida(campo) {
    if (!campo || !campo.validity) return false;
    return Boolean(campo.validity.badInput) ||
      (Boolean(String(campo.value || "").trim()) && !campo.validity.valid);
  }

  function fallarValidacion(campo, mensaje) {
    mostrarError(mensaje);
    if (campo && typeof campo.focus === "function") {
      campo.focus();
      if (typeof campo.scrollIntoView === "function") {
        campo.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
      campo.setAttribute("aria-invalid", "true");
    }
    return false;
  }

  function validarFicha() {
    var vencimiento = $("fm-vencimiento");
    if (campoConFechaInvalida(vencimiento)) {
      return fallarValidacion(
        vencimiento,
        "El vencimiento no es una fecha válida. Usá el calendario o el formato AAAA-MM-DD."
      );
    }

    var existentesCampo = $("fm-dientes-existentes");
    var existentesTexto = existentesCampo
      ? String(existentesCampo.value || "").trim()
      : "";
    if (existentesCampo && existentesCampo.validity && existentesCampo.validity.badInput) {
      return fallarValidacion(
        existentesCampo,
        "La cantidad de dientes existentes debe ser un número entero entre 0 y 32."
      );
    }
    if (existentesTexto) {
      var existentes = Number(existentesTexto);
      if (
        !isFinite(existentes) ||
        Math.floor(existentes) !== existentes ||
        existentes < 0 ||
        existentes > 32
      ) {
        return fallarValidacion(
          existentesCampo,
          "La cantidad de dientes existentes debe ser un número entero entre 0 y 32."
        );
      }
    }

    var conDatos = 0;
    var filas = filasTrabajo();
    for (var i = 0; i < filas.length; i += 1) {
      var fila = filas[i];
      var campos = {
        fecha: fila.querySelector(".fm-trabajo-fecha"),
        codigo: fila.querySelector(".fm-trabajo-codigo"),
        pieza: fila.querySelector(".fm-trabajo-pieza"),
        trabajo: fila.querySelector(".fm-trabajo-detalle"),
        debe: fila.querySelector(".fm-trabajo-debe"),
        haber: fila.querySelector(".fm-trabajo-haber")
      };
      var fechaInvalida = campoConFechaInvalida(campos.fecha);
      var fecha = String(campos.fecha.value || "").trim();
      var codigo = String(campos.codigo.value || "").trim();
      var pieza = String(campos.pieza.value || "").trim();
      var trabajo = String(campos.trabajo.value || "").trim();
      var debeTexto = String(campos.debe.value || "").trim();
      var haberTexto = String(campos.haber.value || "").trim();
      var debeMalEscrito = Boolean(
        campos.debe.validity && campos.debe.validity.badInput
      );
      var haberMalEscrito = Boolean(
        campos.haber.validity && campos.haber.validity.badInput
      );
      if (
        !fecha && !fechaInvalida && !codigo && !pieza && !trabajo &&
        !debeTexto && !haberTexto && !debeMalEscrito && !haberMalEscrito
      ) {
        continue;
      }
      conDatos += 1;
      var renglon = "Renglón " + (i + 1) + ": ";

      if (fechaInvalida) {
        return fallarValidacion(
          campos.fecha,
          renglon + "la fecha no es válida. Usá el calendario o el formato AAAA-MM-DD."
        );
      }
      if (codigo.length > MAX_CODIGO) {
        return fallarValidacion(
          campos.codigo,
          renglon + "el código no puede superar los " + MAX_CODIGO + " caracteres."
        );
      }
      if (
        codigo &&
        state.prestacionesListas &&
        state.prestaciones.length > 0 &&
        !buscarPrestacion(codigo) &&
        normalizarCodigo(codigo) !==
          normalizarCodigo(fila.getAttribute("data-codigo-original"))
      ) {
        return fallarValidacion(
          campos.codigo,
          renglon +
            "el código «" +
            codigo +
            "» no está en el catálogo de prestaciones. Elegí uno de la lista."
        );
      }
      if (pieza.length > MAX_PIEZA) {
        return fallarValidacion(
          campos.pieza,
          renglon +
            "la pieza dentaria no puede superar los " +
            MAX_PIEZA +
            " caracteres."
        );
      }
      if (trabajo.length > MAX_TRABAJO) {
        return fallarValidacion(
          campos.trabajo,
          renglon +
            "el detalle no puede superar los " +
            MAX_TRABAJO +
            " caracteres."
        );
      }

      var importes = [
        {
          campo: campos.debe,
          texto: debeTexto,
          nombre: "Debe",
          sugerencia: "Si es un pago, cargalo en Haber."
        },
        {
          campo: campos.haber,
          texto: haberTexto,
          nombre: "Haber",
          sugerencia: "Si es un cargo, cargalo en Debe."
        }
      ];
      for (var j = 0; j < importes.length; j += 1) {
        var importe = importes[j];
        var numero = numeroDe(importe.campo);
        var malEscrito = Boolean(
          importe.campo.validity && importe.campo.validity.badInput
        );
        if (malEscrito) numero = null;
        if (malEscrito || (importe.texto && numero === null)) {
          return fallarValidacion(
            importe.campo,
            renglon +
              importe.nombre +
              " no es un importe válido. Escribí sólo números, con punto decimal."
          );
        }
        if (numero !== null && numero < 0) {
          return fallarValidacion(
            importe.campo,
            renglon +
              importe.nombre +
              " no puede ser negativo. " +
              importe.sugerencia
          );
        }
        if (numero !== null && numero > MAX_IMPORTE) {
          return fallarValidacion(
            importe.campo,
            renglon +
              importe.nombre +
              " no puede superar los $ " +
              dinero(MAX_IMPORTE) +
              "."
          );
        }
      }
    }

    if (conDatos > MAX_RENGLONES) {
      return fallarValidacion(
        null,
        "El libro de trabajos admite hasta " + MAX_RENGLONES + " renglones."
      );
    }
    return true;
  }

  /* ------------------------------ Datos ---------------------------------- */

  function escribirDato(id, valor) {
    var caja = $(id);
    if (caja) caja.textContent = valor ? String(valor) : "—";
  }

  function marcarRadio(nombre, valor) {
    var botones = document.querySelectorAll('input[name="' + nombre + '"]');
    Array.prototype.forEach.call(botones, function (boton) {
      boton.checked =
        valor === true ? boton.value === "SI"
        : valor === false ? boton.value === "NO"
        : false;
    });
  }

  function leerRadio(nombre) {
    var elegido = document.querySelector(
      'input[name="' + nombre + '"]:checked'
    );
    if (!elegido || !elegido.value) return null;
    return elegido.value === "SI";
  }

  function aplicarFiliatorios(paciente) {
    paciente = paciente || {};
    if (paciente.nombre) {
      state.nombre = paciente.nombre;
      var titulo = $("fm-titulo");
      if (titulo) titulo.textContent = "Ficha médica · " + state.nombre;
    }
    escribirDato("fm-nombre", paciente.nombre || state.nombre);
    escribirDato("fm-obra-social", paciente.obra_social);
    escribirDato("fm-afiliado", paciente.numero_afiliado);
    var nacimiento = fechaLegible(paciente.fecha_nacimiento);
    escribirDato(
      "fm-nacimiento",
      nacimiento
        ? nacimiento + (paciente.edad == null ? "" : " · " + paciente.edad + " años")
        : ""
    );
    escribirDato("fm-domicilio", paciente.domicilio);
    escribirDato("fm-telefono", paciente.telefono);
    escribirDato("fm-trabajo", paciente.lugar_trabajo);
    escribirDato("fm-jerarquia", paciente.jerarquia);
    escribirDato("fm-titular", paciente.titular);
  }

  function aplicarFicha(datos) {
    var paciente = (datos && datos.paciente) || {};
    state.nombre = paciente.nombre || state.nombre;
    state.fichaLista = true;

    aplicarFiliatorios(paciente);

    $("fm-grupo").value = datos.grupo_familiar || "";
    $("fm-parentesco").value = datos.parentesco || "";
    $("fm-vencimiento").value = datos.vencimiento || "";
    $("fm-antecedentes").value = datos.antecedentes_enfermedad || "";
    marcarRadio("fm-sarro", datos.presencia_sarro);
    marcarRadio("fm-periodontal", datos.enfermedad_periodontal);

    var ficha = datos.numero_ficha ? "Ficha " + datos.numero_ficha : "Ficha nueva";
    $("fm-sub").textContent =
      ficha +
      (datos.actualizado_en
        ? " · Actualizada el " + fechaHora(datos.actualizado_en)
        : " · Hoja sin guardar todavía");

    aplicarTrabajos(datos.trabajos || []);
  }

  function aplicarOdontograma(datos) {
    state.dientes = {};
    state.odontogramaListo = true;
    (datos.dientes || []).forEach(function (item) {
      if (ESTADOS[item.estado]) state.dientes[item.diente] = item.estado;
    });

    $("fm-protesis-fija").value = datos.protesis_fija || "";
    $("fm-protesis-removible").value = datos.protesis_removible || "";
    $("fm-coronas").value = datos.coronas || "";
    $("fm-dientes-existentes").value =
      datos.dientes_existentes === null || datos.dientes_existentes === undefined
        ? ""
        : String(datos.dientes_existentes);
    $("fm-observaciones").value = datos.observaciones || "";

    repintar();
    actualizarTotales();
    seleccionarDiente(null);
  }

  /* ------------------------- Abrir y cerrar ------------------------------ */

  function limpiarFormulario() {
    ["fm-nombre", "fm-obra-social", "fm-afiliado", "fm-nacimiento",
     "fm-domicilio", "fm-telefono", "fm-trabajo", "fm-jerarquia",
     "fm-titular"].forEach(function (id) {
      escribirDato(id, "");
    });
    ["fm-grupo", "fm-parentesco", "fm-vencimiento", "fm-antecedentes",
     "fm-protesis-fija", "fm-protesis-removible", "fm-coronas",
     "fm-dientes-existentes", "fm-observaciones"].forEach(function (id) {
      var campo = $(id);
      if (campo) campo.value = "";
    });
    marcarRadio("fm-sarro", null);
    marcarRadio("fm-periodontal", null);
    aplicarTrabajos([]);
  }

  function abrir(pacienteId, nombre) {
    inyectar();
    // El catálogo se carga en paralelo con la ficha: el datalist queda listo
    // antes de que la secretaría termine de completar el primer renglón.
    cargarPrestaciones();
    state.pacienteId = Number(pacienteId);
    state.nombre = nombre || "Paciente #" + pacienteId;
    state.dientes = {};
    state.fichaLista = false;
    state.odontogramaListo = false;

    actualizarBotonEditarPaciente();
    ocultarError();
    limpiarFormulario();
    repintar();
    actualizarTotales();
    seleccionarDiente(null);
    seleccionar(null);

    $("fm-titulo").textContent = "Ficha médica · " + state.nombre;
    $("fm-sub").textContent = "Cargando…";
    // El nombre del paciente llega desde el panel: se muestra de inmediato,
    // antes de que respondan las lecturas de la ficha y del odontograma.
    escribirDato("fm-nombre", state.nombre);
    mostrar(true);

    return Promise.all([
      // Los filiatorios se leen también del paciente: si la ficha médica
      // todavía no existe (o el servidor es anterior a esta ruta), el nombre,
      // la obra social, el teléfono y el domicilio ya cargados se ven igual.
      pedirPaciente()
        .then(aplicarFiliatorios)
        .catch(function () {
          /* La ficha trae los mismos datos; su error se muestra abajo. */
        }),
      pedirFicha("GET")
        .then(aplicarFicha)
        .catch(function (error) {
          state.fichaLista = false;
          $("fm-sub").textContent = "No se pudo cargar la ficha médica.";
          mostrarError(error.message);
        }),
      pedirOdontograma("GET")
        .then(aplicarOdontograma)
        .catch(function (error) {
          mostrarError(error.message);
        })
    ]);
  }

  function mostrar(abierto) {
    var modal = $("modal-ficha-medica");
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
    var boton = $("fm-guardar");
    if (!boton) return;
    boton.disabled = activo;
    boton.textContent = activo ? "Guardando…" : "Guardar ficha";
  }

  function mostrarError(mensaje) {
    var caja = $("fm-error");
    if (!caja) return;
    caja.textContent = mensaje;
    caja.classList.remove("hide");
  }

  function ocultarError() {
    var caja = $("fm-error");
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

  function etiquetaDeCampo(loc) {
    var partes = (loc || []).filter(function (parte) {
      return parte !== "body" && parte !== "query" && parte !== "path";
    });
    if (!partes.length) return "";
    if (partes[0] === "trabajos") {
      if (partes.length < 3) return ETIQUETAS_CABECERA.trabajos;
      var indice = Number(partes[1]);
      var campo = ETIQUETAS_TRABAJO[partes[2]] || String(partes[2] || "");
      return (
        "Renglón " +
        (isFinite(indice) ? indice + 1 : "?") +
        (campo ? " · " + campo : "")
      );
    }
    var clave = String(partes[0] || "");
    return ETIQUETAS_CABECERA[clave] || clave;
  }

  /* Traduce los errores de Pydantic (422) a un texto útil para la secretaría:
     antes se mostraba sólo ``Input should be greater than or equal to 0``. */
  function mensajeDeErrorDeValidacion(item) {
    var tipo = String(item.type || "");
    var ctx = item.ctx || {};
    var mensaje = String(item.msg || "").replace(/^(Value error|Assertion failed),\s*/i, "");
    switch (tipo) {
      case "string_too_long":
        mensaje = "no puede superar los " + ctx.max_length + " caracteres.";
        break;
      case "string_too_short":
        mensaje = "debe tener al menos " + ctx.min_length + " caracteres.";
        break;
      case "greater_than_equal":
        mensaje = "debe ser mayor o igual a " + ctx.ge + ".";
        break;
      case "less_than_equal":
        mensaje = "debe ser menor o igual a " + ctx.le + ".";
        break;
      case "greater_than":
        mensaje = "debe ser mayor que " + ctx.gt + ".";
        break;
      case "less_than":
        mensaje = "debe ser menor que " + ctx.lt + ".";
        break;
      case "date_parsing":
      case "date_from_datetime_parsing":
      case "datetime_parsing":
        mensaje = "no es una fecha válida (AAAA-MM-DD).";
        break;
      case "int_parsing":
      case "int_type":
        mensaje = "debe ser un número entero.";
        break;
      case "float_parsing":
      case "float_type":
        mensaje = "debe ser un número.";
        break;
      case "bool_parsing":
      case "bool_type":
        mensaje = "debe ser SI o NO.";
        break;
      case "missing":
        mensaje = "es obligatorio.";
        break;
      case "list_type":
        mensaje = "debe ser una lista.";
        break;
      case "string_type":
        mensaje = "debe ser texto.";
        break;
      default:
        break;
    }
    var etiqueta = etiquetaDeCampo(item.loc);
    return etiqueta ? etiqueta + ": " + mensaje : mensaje;
  }

  function mensajeDeDetalle(detalle) {
    if (typeof detalle === "string") return detalle;
    if (!Array.isArray(detalle)) return "";
    var vistos = {};
    return detalle
      .map(function (item) {
        if (!item || typeof item !== "object") return String(item || "");
        return mensajeDeErrorDeValidacion(item);
      })
      .filter(function (texto) {
        if (!texto || vistos[texto]) return false;
        vistos[texto] = true;
        return true;
      })
      .join(" ");
  }

  function pedir(url, metodo, cuerpo, queEs) {
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

    return fetch(url, opciones).then(
      function (respuesta) {
        return respuesta
          .json()
          .catch(function () {
            return null;
          })
          .then(function (datos) {
            if (respuesta.ok) return datos;
            var detalle = mensajeDeDetalle(datos && datos.detail);
            var mensaje =
              detalle && detalle !== "Not Found"
                ? detalle
                : respuesta.status === 404
                  ? detalle === "Not Found"
                    ? "Esta función todavía no está disponible en el servidor. " +
                      "Reiniciá la aplicación y volvé a intentar."
                    : "No encontramos al paciente. Actualizá el panel e intentá de nuevo."
                  : "No pudimos completar la operación con " + queEs + ". Intentá de nuevo.";
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

  function pedirFicha(metodo, cuerpo) {
    var url =
      API + encodeURIComponent(state.pacienteId) + "/ficha-medica";
    return pedir(url, metodo, cuerpo, "la ficha médica");
  }

  function pedirPaciente() {
    return pedir(
      API + encodeURIComponent(state.pacienteId),
      "GET",
      undefined,
      "los datos del paciente"
    );
  }

  function pedirOdontograma(metodo, cuerpo) {
    var url =
      API + encodeURIComponent(state.pacienteId) + "/odontograma";
    return pedir(url, metodo, cuerpo, "el odontograma");
  }

  function guardar() {
    if (state.guardando) return Promise.resolve();
    if (!state.fichaLista || !state.odontogramaListo) {
      mostrarError(
        "No se pudieron cargar todos los datos de la ficha. Cerrá el modal, " +
          "actualizá el panel e intentá de nuevo."
      );
      return Promise.resolve();
    }

    var dientes = Object.keys(state.dientes)
      .map(function (clave) {
        return { diente: Number(clave), estado: state.dientes[clave] };
      })
      .sort(function (a, b) {
        return a.diente - b.diente;
      });

    var existentes = valor("fm-dientes-existentes");
    var odontograma = {
      protesis_fija: textoONull("fm-protesis-fija"),
      protesis_removible: textoONull("fm-protesis-removible"),
      coronas: textoONull("fm-coronas"),
      dientes_existentes: existentes ? parseInt(existentes, 10) : null,
      observaciones: textoONull("fm-observaciones"),
      dientes: dientes
    };

    var ficha = {
      vencimiento: textoONull("fm-vencimiento"),
      grupo_familiar: textoONull("fm-grupo"),
      parentesco: textoONull("fm-parentesco"),
      antecedentes_enfermedad: textoONull("fm-antecedentes"),
      presencia_sarro: leerRadio("fm-sarro"),
      enfermedad_periodontal: leerRadio("fm-periodontal"),
      trabajos: leerTrabajos()
    };

    ocultarError();
    // Se valida todo antes de la primera escritura: si un importe es negativo
    // o un código no pertenece al nomenclador, no se guarda el odontograma a
    // medias ni se pierde el libro que la secretaría acaba de cargar.
    if (!validarFicha()) return Promise.resolve();

    state.guardando = true;
    bloquear(true);

    var dientesGuardados = false;

    return pedirOdontograma("PUT", odontograma)
      .then(function () {
        dientesGuardados = true;
        return pedirFicha("PUT", ficha);
      })
      .then(function (datos) {
        aplicarFicha(datos);
        document.dispatchEvent(
          new CustomEvent("ficha-medica:guardada", { detail: datos })
        );
        cerrar();
        avisar(
          "Ficha médica guardada",
          dientes.length + " diente/s marcados para " + state.nombre + "."
        );
      })
      .catch(function (error) {
        mostrarError(
          (dientesGuardados ? "Se guardaron los dientes, pero la ficha no: " : "") +
            error.message
        );
      })
      .then(function () {
        state.guardando = false;
        bloquear(false);
      });
  }

  /* --------------------------- Integración ------------------------------- */

  document.addEventListener("click", function (evento) {
    var boton = evento.target.closest(
      "[data-ficha-medica], [data-odontograma]"
    );
    if (!boton) return;
    evento.preventDefault();
    abrir(
      boton.getAttribute("data-ficha-medica") ||
        boton.getAttribute("data-odontograma"),
      boton.getAttribute("data-ficha-medica-nombre") ||
        boton.getAttribute("data-odontograma-nombre") ||
        ""
    );
  });

  window.FichaMedica = {
    abrir: abrir,
    cerrar: cerrar
  };
  // Compatibilidad con llamadas anteriores al módulo del odontograma.
  window.Odontograma = window.FichaMedica;
})();
