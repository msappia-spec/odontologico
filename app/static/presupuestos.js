/* =========================================================================
   Módulo de presupuestos del panel de la secretaría.
   Se apoya en la API pública de app.js (window.OdontoApp) para usar el mismo
   cliente HTTP, los toasts y el escapado de HTML del resto del panel.
   ========================================================================= */
(function () {
  "use strict";

  const API = (window.OdontoApp && window.OdontoApp.API) || "/api/v1";
  const RUTA = "/presupuestos";

  const state = {
    presupuestos: [],
    pacientes: [],
    prestaciones: [],
    editId: null,
    cargando: false,
    itemSeq: 0,
    direccionAuto: "",
  };

  const $ = (id) => document.getElementById(id);

  function esc(valor) {
    if (window.OdontoApp && window.OdontoApp.esc) {
      return window.OdontoApp.esc(valor);
    }
    return String(valor == null ? "" : valor).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  function api(ruta, opciones) {
    if (window.OdontoApp && window.OdontoApp.api) {
      return window.OdontoApp.api(ruta, opciones);
    }
    return fetch(API + ruta, Object.assign({ headers: { "Content-Type": "application/json" } }, opciones || {}))
      .then(async (respuesta) => {
        const texto = await respuesta.text();
        let datos = null;
        if (texto) {
          try { datos = JSON.parse(texto); } catch (_) { datos = texto; }
        }
        if (!respuesta.ok) {
          const detalle = datos && datos.detail ? datos.detail : `Error ${respuesta.status}`;
          throw new Error(typeof detalle === "string" ? detalle : "No se pudo completar la operación.");
        }
        return datos;
      });
  }

  function toast(tipo, titulo, mensaje) {
    if (window.OdontoApp && window.OdontoApp.toast) {
      window.OdontoApp.toast(tipo, titulo, mensaje);
      return;
    }
    console.log(`[${tipo}] ${titulo}: ${mensaje || ""}`);
  }

  function hoyISO() {
    const hoy = new Date();
    const mes = String(hoy.getMonth() + 1).padStart(2, "0");
    const dia = String(hoy.getDate()).padStart(2, "0");
    return `${hoy.getFullYear()}-${mes}-${dia}`;
  }

  function pesos(valor) {
    const numero = Number(valor) || 0;
    if (Math.abs(numero - Math.round(numero)) < 0.005) {
      return "$" + Math.round(numero).toLocaleString("es-AR");
    }
    return "$" + numero.toLocaleString("es-AR", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function normalizar(texto) {
    return String(texto || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();
  }

  function fechaLegible(iso) {
    const partes = String(iso || "").slice(0, 10).split("-").map(Number);
    if (partes.length !== 3 || !partes[0]) return "Sin fecha";
    const fecha = new Date(partes[0], partes[1] - 1, partes[2]);
    const texto = fecha.toLocaleDateString("es-AR", { month: "long", year: "numeric" });
    return texto.charAt(0).toUpperCase() + texto.slice(1);
  }

  function pacienteDe(id) {
    return state.pacientes.find((p) => String(p.id) === String(id)) || null;
  }

  function presupuestoDe(id) {
    return state.presupuestos.find((p) => String(p.id) === String(id)) || null;
  }

  function prestacionDeCodigo(codigo) {
    const buscado = normalizar(codigo);
    if (!buscado) return null;
    return (
      state.prestaciones.find(
        (p) => normalizar(p.codigo_nomenclador) === buscado
      ) || null
    );
  }

  function mostrarError(mensaje) {
    const caja = $("pres-error");
    if (!caja) return;
    caja.textContent = mensaje || "";
    caja.classList.toggle("hide", !mensaje);
  }

  function limpiarError() {
    mostrarError("");
  }

  /* --------------------------- Renglones del detalle -------------------- */
  function renumerarItems() {
    const filas = document.querySelectorAll("#pres-items .pres-item");
    filas.forEach((fila, indice) => {
      const numero = fila.querySelector(".pres-item-num");
      if (numero) numero.textContent = indice + 1;
    });
  }

  function agregarItem(datos) {
    const contenedor = $("pres-items");
    if (!contenedor) return null;

    const item = datos || {};
    const id = ++state.itemSeq;
    const fila = document.createElement("div");
    fila.className = "pres-item";
    fila.dataset.presItem = String(id);
    fila.innerHTML = `
      <div class="pres-item-head">
        <span class="pres-item-num">0</span>
        <span class="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Trabajo</span>
      </div>
      <div class="pres-item-grid">
        <div>
          <label class="field-label" for="pres-codigo-${id}">Código</label>
          <input id="pres-codigo-${id}" class="field pres-codigo" maxlength="30"
                 list="pres-prestaciones-catalogo" autocomplete="off"
                 placeholder="Ej.: 11.02" value="${esc(item.codigo || "")}" />
        </div>
        <div>
          <label class="field-label" for="pres-detalle-${id}">Descripción y otros detalles</label>
          <textarea id="pres-detalle-${id}" class="field pres-detalle" maxlength="1000" rows="4"
                    placeholder="Corona de Circonio ed 36… / Podés agregar otros detalles en varias líneas">${esc(item.detalle || "")}</textarea>
        </div>
        <div>
          <label class="field-label" for="pres-precio-${id}">Precio</label>
          <input id="pres-precio-${id}" class="field pres-precio" type="number" min="0" step="0.01"
                 inputmode="decimal" placeholder="0" value="${item.precio == null ? "" : esc(item.precio)}" />
        </div>
        <div>
          <label class="field-label" for="pres-entrega-${id}">Entrega</label>
          <input id="pres-entrega-${id}" class="field pres-entrega" type="number" min="0" step="0.01"
                 inputmode="decimal" placeholder="Sin entrega" value="${item.entrega == null ? "" : esc(item.entrega)}" />
        </div>
        <button type="button" class="pres-quitar" data-pres-quitar="${id}" title="Quitar este trabajo" aria-label="Quitar este trabajo">
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
        </button>
      </div>`;

    contenedor.appendChild(fila);
    renumerarItems();
    recalcular();
    return fila;
  }

  function limpiarItems() {
    const contenedor = $("pres-items");
    if (contenedor) contenedor.innerHTML = "";
    recalcular();
  }

  function recalcular() {
    let total = 0;
    let entregas = 0;
    document.querySelectorAll("#pres-items .pres-item").forEach((fila) => {
      total += Number(fila.querySelector(".pres-precio")?.value) || 0;
      entregas += Number(fila.querySelector(".pres-entrega")?.value) || 0;
    });
    if ($("pres-total")) $("pres-total").textContent = pesos(total);
    if ($("pres-entregas")) $("pres-entregas").textContent = pesos(entregas);
    if ($("pres-saldo")) $("pres-saldo").textContent = pesos(total - entregas);
  }

  function quitarItem(id) {
    const fila = document.querySelector(`#pres-items [data-pres-item="${id}"]`);
    if (fila) fila.remove();
    if (!document.querySelector("#pres-items .pres-item")) agregarItem();
    renumerarItems();
    recalcular();
  }

  /* ------------------------------ Pacientes ----------------------------- */
  async function cargarPrestaciones() {
    const prestaciones = await api("/prestaciones?activas=true");
    state.prestaciones = Array.isArray(prestaciones) ? prestaciones : [];

    const catalogo = $("pres-prestaciones-catalogo");
    if (!catalogo) return;
    catalogo.innerHTML = state.prestaciones
      .map(
        (prestacion) =>
          `<option value="${esc(prestacion.codigo_nomenclador)}">${esc(
            prestacion.descripcion
          )} · ${pesos(prestacion.valor_base)}</option>`
      )
      .join("");
  }

  /* Completa descripción y precio al elegir un código del catálogo. Sólo pisa
     los campos que siguen vacíos o que fueron completados automáticamente, para
     no perder los detalles que haya escrito la secretaría. */
  function aplicarPrestacion(input) {
    const fila = input.closest(".pres-item");
    if (!fila) return;

    const prestacion = prestacionDeCodigo(input.value);
    if (!prestacion) return;

    input.value = prestacion.codigo_nomenclador;

    const detalle = fila.querySelector(".pres-detalle");
    const detalleAuto = fila.dataset.autoDetalle || "";
    if (detalle && (!detalle.value.trim() || detalle.value === detalleAuto)) {
      detalle.value = prestacion.descripcion;
      fila.dataset.autoDetalle = prestacion.descripcion;
    }

    const precio = fila.querySelector(".pres-precio");
    const precioAuto = fila.dataset.autoPrecio || "";
    if (precio && (!precio.value.trim() || precio.value === precioAuto)) {
      const valor = Number(prestacion.valor_base) || 0;
      precio.value = valor ? String(valor) : "";
      fila.dataset.autoPrecio = precio.value;
    }

    recalcular();
  }

  async function cargarPacientes() {
    const pacientes = await api("/pacientes");
    state.pacientes = Array.isArray(pacientes) ? pacientes : [];

    const select = $("pres-paciente");
    if (!select) return;
    const elegido = select.value;
    select.innerHTML =
      '<option value="">Elegí un paciente…</option>' +
      state.pacientes
        .map((paciente) => {
          const dni = paciente.dni ? ` · DNI ${paciente.dni}` : "";
          return `<option value="${paciente.id}">${esc(paciente.nombre)}${esc(dni)}</option>`;
        })
        .join("");
    if (elegido && state.pacientes.some((p) => String(p.id) === String(elegido))) {
      select.value = elegido;
    }

    const ayuda = $("pres-paciente-ayuda");
    if (ayuda) {
      ayuda.textContent = state.pacientes.length
        ? "El nombre y el domicilio del paciente salen en el PDF."
        : "Todavía no hay pacientes: dalos de alta en la pestaña Pacientes.";
    }
  }

  /* --------------------------- Listado y filtros ------------------------ */
  function renderLista() {
    const contenedor = $("pres-lista");
    if (!contenedor) return;

    const termino = normalizar($("pres-buscar") ? $("pres-buscar").value : "");
    const filtrados = state.presupuestos.filter((presupuesto) => {
      if (!termino) return true;
      const texto = [
        presupuesto.paciente ? presupuesto.paciente.nombre : "",
        presupuesto.paciente ? presupuesto.paciente.dni : "",
        presupuesto.forma_pago,
        ...(presupuesto.items || []).flatMap((item) => [
          item.codigo,
          item.detalle,
        ]),
      ].join(" ");
      return normalizar(texto).includes(termino);
    });

    if ($("pres-count")) $("pres-count").textContent = String(state.presupuestos.length);

    if (!filtrados.length) {
      contenedor.innerHTML = `<div class="pres-empty">${
        state.presupuestos.length
          ? "No hay presupuestos que coincidan con la búsqueda."
          : "Todavía no hay presupuestos cargados."
      }</div>`;
      return;
    }

    contenedor.innerHTML = filtrados
      .map((presupuesto) => {
        const paciente = presupuesto.paciente || {};
        const items = presupuesto.items || [];
        const detalle = items
          .map((item) =>
            [item.codigo ? `Cód. ${item.codigo}` : "", item.detalle]
              .filter(Boolean)
              .join(" · ")
          )
          .join(" · ");
        const cantidad = `${items.length} trabajo${items.length === 1 ? "" : "s"}`;
        return `
          <article class="pres-lista-item" data-pres-id="${presupuesto.id}">
            <div class="pres-lista-top">
              <div class="min-w-0">
                <p class="pres-lista-paciente">${esc(paciente.nombre || "Paciente")}</p>
                <p class="pres-lista-meta">${esc(fechaLegible(presupuesto.fecha))} · ${cantidad} · ${esc(presupuesto.forma_pago)}</p>
              </div>
              <span class="pres-lista-total">${pesos(presupuesto.total)}</span>
            </div>
            <p class="pres-lista-detalle">${esc(detalle)}</p>
            <div class="pres-lista-acciones">
              <a class="btn btn-ghost" href="${API}${RUTA}/${presupuesto.id}/pdf"
                 target="_blank" rel="noopener" title="Abrir el PDF de este presupuesto">
                <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/></svg>
                PDF
              </a>
              <button type="button" class="btn btn-soft" data-pres-editar="${presupuesto.id}">
                Editar
              </button>
              <button type="button" class="btn btn-danger-soft" data-pres-eliminar="${presupuesto.id}">
                Eliminar
              </button>
            </div>
          </article>`;
      })
      .join("");
  }

  async function cargarPresupuestos() {
    const presupuestos = await api(RUTA);
    state.presupuestos = Array.isArray(presupuestos) ? presupuestos : [];
    renderLista();
  }

  async function cargar() {
    if (state.cargando) return;
    state.cargando = true;
    limpiarError();
    try {
      await Promise.all([
        cargarPacientes(),
        cargarPrestaciones(),
        cargarPresupuestos(),
      ]);
    } catch (err) {
      mostrarError(`No se pudieron cargar los presupuestos: ${err.message}`);
    } finally {
      state.cargando = false;
    }
  }

  /* ---------------------------- Alta y edición -------------------------- */
  function iniciarNuevo() {
    state.editId = null;
    if ($("pres-id")) $("pres-id").value = "";
    if ($("pres-paciente")) $("pres-paciente").value = "";
    if ($("pres-fecha")) $("pres-fecha").value = hoyISO();
    if ($("pres-forma-pago")) $("pres-forma-pago").value = "Contado / transferencia";
    if ($("pres-direccion")) $("pres-direccion").value = "";
    state.direccionAuto = "";

    limpiarItems();
    agregarItem();

    if ($("pres-form-titulo")) $("pres-form-titulo").textContent = "Nuevo presupuesto";
    if ($("pres-form-modo")) {
      $("pres-form-modo").textContent = "Nuevo";
      $("pres-form-modo").className = "badge badge-brand";
    }
    if ($("pres-guardar-texto")) $("pres-guardar-texto").textContent = "Guardar presupuesto";
    if ($("pres-cancelar")) $("pres-cancelar").classList.add("hide");
    if ($("pres-pdf-ultimo")) {
      $("pres-pdf-ultimo").classList.add("hide");
      $("pres-pdf-ultimo").href = "#";
    }
    limpiarError();
    recalcular();
  }

  function cargarEnFormulario(id) {
    const presupuesto = presupuestoDe(id);
    if (!presupuesto) {
      mostrarError("No se encontró el presupuesto en la lista.");
      return;
    }

    state.editId = presupuesto.id;
    if ($("pres-id")) $("pres-id").value = presupuesto.id;
    if ($("pres-paciente")) $("pres-paciente").value = presupuesto.paciente_id;
    if ($("pres-fecha")) $("pres-fecha").value = presupuesto.fecha || hoyISO();
    if ($("pres-forma-pago")) $("pres-forma-pago").value = presupuesto.forma_pago || "";
    if ($("pres-direccion")) $("pres-direccion").value = presupuesto.direccion || "";
    state.direccionAuto = presupuesto.paciente ? presupuesto.paciente.domicilio || "" : "";

    limpiarItems();
    (presupuesto.items || []).forEach((item) => agregarItem(item));
    if (!presupuesto.items || !presupuesto.items.length) agregarItem();

    if ($("pres-form-titulo")) $("pres-form-titulo").textContent = "Editar presupuesto";
    if ($("pres-form-modo")) {
      $("pres-form-modo").textContent = "Editando";
      $("pres-form-modo").className = "badge badge-amber";
    }
    if ($("pres-guardar-texto")) $("pres-guardar-texto").textContent = "Guardar cambios";
    if ($("pres-cancelar")) $("pres-cancelar").classList.remove("hide");
    if ($("pres-pdf-ultimo")) {
      $("pres-pdf-ultimo").href = `${API}${RUTA}/${presupuesto.id}/pdf`;
      $("pres-pdf-ultimo").classList.remove("hide");
    }
    limpiarError();
    recalcular();

    const formulario = $("pres-form");
    if (formulario && formulario.scrollIntoView) {
      formulario.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function leerItems() {
    const items = [];
    const filas = Array.from(document.querySelectorAll("#pres-items .pres-item"));

    filas.forEach((fila, indice) => {
      const codigo = (fila.querySelector(".pres-codigo")?.value || "").trim();
      const detalle = (fila.querySelector(".pres-detalle")?.value || "").trim();
      const precioTexto = (fila.querySelector(".pres-precio")?.value || "").trim();
      const entregaTexto = (fila.querySelector(".pres-entrega")?.value || "").trim();

      // Un renglón completamente vacío se ignora (igual que la ficha médica).
      if (!codigo && !detalle && !precioTexto && !entregaTexto) return;

      if (!detalle) {
        throw new Error(`Completá la descripción del trabajo ${indice + 1}.`);
      }
      if (!precioTexto) {
        throw new Error(`Ingresá el precio del trabajo ${indice + 1}.`);
      }

      const precio = Number(precioTexto);
      if (!Number.isFinite(precio) || precio < 0) {
        throw new Error(`El precio del trabajo ${indice + 1} no es válido.`);
      }

      let entrega = null;
      if (entregaTexto) {
        entrega = Number(entregaTexto);
        if (!Number.isFinite(entrega) || entrega < 0) {
          throw new Error(`La entrega del trabajo ${indice + 1} no es válida.`);
        }
      }

      items.push({ codigo: codigo || null, detalle, precio, entrega });
    });

    if (!items.length) {
      throw new Error("Agregá al menos un trabajo al presupuesto.");
    }
    return items;
  }

  async function guardar(evento) {
    evento.preventDefault();
    limpiarError();

    let items;
    try {
      items = leerItems();
    } catch (err) {
      mostrarError(err.message);
      return;
    }

    const pacienteId = Number($("pres-paciente") ? $("pres-paciente").value : 0);
    if (!Number.isInteger(pacienteId) || pacienteId <= 0) {
      mostrarError("Elegí el paciente del presupuesto.");
      if ($("pres-paciente")) $("pres-paciente").focus();
      return;
    }

    const payload = {
      paciente_id: pacienteId,
      fecha: ($("pres-fecha") && $("pres-fecha").value) || hoyISO(),
      forma_pago: (($("pres-forma-pago") && $("pres-forma-pago").value) || "").trim() ||
        "Contado / transferencia",
      direccion: (($("pres-direccion") && $("pres-direccion").value) || "").trim() || null,
      items,
    };

    const boton = $("pres-guardar");
    const textoBoton = $("pres-guardar-texto");
    const editando = state.editId != null;
    if (boton) boton.disabled = true;
    if (textoBoton) textoBoton.textContent = "Guardando…";

    try {
      const guardado = await api(
        editando ? `${RUTA}/${state.editId}` : RUTA,
        {
          method: editando ? "PUT" : "POST",
          body: JSON.stringify(payload),
        }
      );

      const indice = state.presupuestos.findIndex(
        (p) => String(p.id) === String(guardado.id)
      );
      if (indice >= 0) state.presupuestos[indice] = guardado;
      else state.presupuestos.unshift(guardado);
      renderLista();
      cargarEnFormulario(guardado.id);

      toast(
        "exito",
        editando ? "Presupuesto actualizado" : "Presupuesto guardado",
        `${guardado.paciente.nombre} · ${pesos(guardado.total)}`
      );
    } catch (err) {
      mostrarError(err.message);
      toast("error", "No se pudo guardar el presupuesto", err.message);
    } finally {
      if (boton) boton.disabled = false;
      if (textoBoton) {
        textoBoton.textContent = state.editId != null
          ? "Guardar cambios"
          : "Guardar presupuesto";
      }
    }
  }

  async function eliminar(id) {
    const presupuesto = presupuestoDe(id);
    if (!presupuesto) return;
    const nombre = presupuesto.paciente ? presupuesto.paciente.nombre : "este paciente";
    const confirmar = window.confirm(
      `¿Eliminar el presupuesto de ${nombre}? Esta acción no se puede deshacer.`
    );
    if (!confirmar) return;

    try {
      await api(`${RUTA}/${id}`, { method: "DELETE" });
      state.presupuestos = state.presupuestos.filter(
        (p) => String(p.id) !== String(id)
      );
      if (String(state.editId) === String(id)) iniciarNuevo();
      renderLista();
      toast("exito", "Presupuesto eliminado", nombre);
    } catch (err) {
      mostrarError(err.message);
      toast("error", "No se pudo eliminar el presupuesto", err.message);
    }
  }

  /* ------------------------------ Eventos ------------------------------- */
  function actualizarDireccionDelPaciente() {
    const paciente = pacienteDe($("pres-paciente") ? $("pres-paciente").value : "");
    const direccion = $("pres-direccion");
    if (!direccion) return;
    // Sólo se pisa la dirección si está vacía o si era la que se había
    // completado automáticamente con el paciente anterior.
    if (!direccion.value.trim() || direccion.value === state.direccionAuto) {
      direccion.value = paciente && paciente.domicilio ? paciente.domicilio : "";
    }
    state.direccionAuto = paciente && paciente.domicilio ? paciente.domicilio : "";
  }

  function registrarEventos() {
    const agregar = $("pres-agregar-item");
    if (agregar) agregar.addEventListener("click", () => agregarItem());

    const items = $("pres-items");
    if (items) {
      items.addEventListener("input", recalcular);
      items.addEventListener("change", (evento) => {
        const codigo = evento.target.closest(".pres-codigo");
        if (codigo) aplicarPrestacion(codigo);
      });
      items.addEventListener("click", (evento) => {
        const quitar = evento.target.closest("[data-pres-quitar]");
        if (quitar) quitarItem(quitar.dataset.presQuitar);
      });
    }

    const buscar = $("pres-buscar");
    if (buscar) buscar.addEventListener("input", renderLista);

    const lista = $("pres-lista");
    if (lista) {
      lista.addEventListener("click", (evento) => {
        const editar = evento.target.closest("[data-pres-editar]");
        if (editar) {
          cargarEnFormulario(editar.dataset.presEditar);
          return;
        }
        const borrar = evento.target.closest("[data-pres-eliminar]");
        if (borrar) eliminar(borrar.dataset.presEliminar);
      });
    }

    const formulario = $("pres-form");
    if (formulario) formulario.addEventListener("submit", guardar);

    const cancelar = $("pres-cancelar");
    if (cancelar) cancelar.addEventListener("click", iniciarNuevo);

    const paciente = $("pres-paciente");
    if (paciente) paciente.addEventListener("change", actualizarDireccionDelPaciente);
  }

  function iniciar() {
    registrarEventos();
    iniciarNuevo();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", iniciar);
  } else {
    iniciar();
  }

  // app.js llama a cargar() al entrar a la pestaña «Presupuestos».
  window.PresupuestosApp = {
    cargar,
    iniciarNuevo,
    state,
  };
})();
