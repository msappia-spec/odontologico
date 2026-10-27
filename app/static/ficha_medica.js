/* ==========================================================================
   Ficha médica · catálogo compartido (Módulo 4)
   --------------------------------------------------------------------------
   Reproduce la «Historia Clínica General» del consultorio
   (``Historia-Clinica-GeneralNUEVA.pdf``) y lo usan las tres pantallas:

   * ``anamnesis.js``  → formulario público (sólo ``publico: true``).
   * ``app.js``        → modal de la secretaría / Panel Principal.
   * ``medico.js``     → tarjeta de sólo lectura de la vista móvil.

   Cada campo tiene:

   * ``k``: nombre de la columna (el mismo que viaja a la API).
   * ``t``: ``bool`` | ``texto`` | ``area`` | ``opcion`` | ``numero`` | ``fecha``.
   * ``l``: etiqueta que ve el usuario.
   * ``h``: ayuda corta (opcional).
   * ``op``: opciones de un ``select`` (``[{v, l}]``).
   * ``min`` / ``max``: rango de los campos numéricos.
   * ``ancho``: ocupa la fila completa del formulario (domicilio, etc.).

   Las secciones marcadas ``publico: true`` son las que completa el paciente;
   las demás (examen bucal y diagnóstico) las carga el profesional. El
   odontograma no está acá: es un gráfico aparte (``odontograma.js``).

   Los **datos filiatorios** son la excepción: se muestran en la ficha pero el
   backend los guarda en el registro del paciente (``pacientes``), así que la
   secretaría no los vuelve a tipear. La fecha de nacimiento muestra la edad
   calculada al lado.
   ========================================================================== */

(function () {
  "use strict";

  var MAX_TEXTO = 1000;

  var OPCIONES = {
    hepatitis: [
      { v: "A", l: "Tipo A" },
      { v: "B", l: "Tipo B" },
      { v: "C", l: "Tipo C" }
    ],
    intensidad: [
      { v: "SUAVE", l: "Suave" },
      { v: "MODERADO", l: "Moderado" },
      { v: "INTENSO", l: "Intenso" }
    ],
    duracion: [
      { v: "TEMPORARIO", l: "Temporario" },
      { v: "INTERMITENTE", l: "Intermitente" },
      { v: "CONTINUO", l: "Continuo" }
    ],
    desencadenante: [
      { v: "ESPONTANEO", l: "Espontáneo" },
      { v: "PROVOCADO", l: "Provocado" }
    ],
    estimulo: [
      { v: "FRIO", l: "Al frío" },
      { v: "CALOR", l: "Al calor" }
    ],
    localizacion: [
      { v: "LOCALIZADO", l: "Localizado" },
      { v: "IRRADIADO", l: "Irradiado" }
    ],
    higiene: [
      { v: "MUY_BUENO", l: "Muy bueno" },
      { v: "BUENO", l: "Bueno" },
      { v: "DEFICIENTE", l: "Deficiente" },
      { v: "MALO", l: "Malo" }
    ]
  };

  var SECCIONES = [
    {
      id: "filiatorios",
      titulo: "Datos filiatorios",
      nota: "Encabezado de la historia clínica. Se guardan en la ficha del paciente.",
      publico: true,
      campos: [
        {
          k: "fecha_nacimiento",
          t: "fecha",
          l: "Fecha de nacimiento",
          h: "La edad se calcula sola."
        },
        { k: "estado_civil", t: "texto", l: "Estado civil" },
        { k: "nacionalidad", t: "texto", l: "Nacionalidad" },
        {
          k: "domicilio",
          t: "texto",
          l: "Domicilio (calle, número, barrio, localidad)",
          ancho: true
        },
        { k: "profesion", t: "texto", l: "Profesión / actividad" },
        { k: "titular", t: "texto", l: "Titular de la obra social" },
        { k: "lugar_trabajo", t: "texto", l: "Lugar de trabajo" },
        { k: "jerarquia", t: "texto", l: "Jerarquía" }
      ]
    },
    {
      id: "familiares",
      titulo: "Antecedentes familiares",
      nota: "Padre, madre y hermanos.",
      publico: true,
      campos: [
        { k: "padre_vive", t: "bool", l: "¿Padre con vida?" },
        {
          k: "padre_patologias",
          t: "area",
          l: "Enfermedad que padece o padeció el padre"
        },
        { k: "madre_vive", t: "bool", l: "¿Madre con vida?" },
        {
          k: "madre_patologias",
          t: "area",
          l: "Enfermedad que padece o padeció la madre"
        },
        { k: "hermanos", t: "bool", l: "¿Tiene hermanos?" },
        { k: "hermanos_sanos", t: "bool", l: "¿Los hermanos están sanos?" },
        { k: "hermanos_detalle", t: "texto", l: "Aclaración sobre los hermanos" }
      ]
    },
    {
      id: "personales",
      titulo: "Antecedentes personales",
      nota: "Enfermedades, medicación, alergias y hábitos.",
      publico: true,
      campos: [
        { k: "enfermedad_actual", t: "bool", l: "¿Sufre alguna enfermedad?" },
        { k: "enfermedad_actual_detalle", t: "texto", l: "¿De qué?" },
        { k: "tratamiento_medico", t: "bool", l: "¿Hace algún tratamiento médico?" },
        { k: "tratamiento_medico_detalle", t: "texto", l: "¿Cuál?" },
        {
          k: "medicacion_actual",
          t: "area",
          l: "Medicamentos que consume habitualmente",
          h: "Incluya anticoagulantes, aspirina o suplementos."
        },
        {
          k: "medicamentos_ultimos_5_anios",
          t: "area",
          l: "Medicamentos de los últimos 5 años"
        },
        { k: "deporte", t: "bool", l: "¿Realiza algún deporte?" },
        { k: "deporte_malestar", t: "bool", l: "¿Nota algún malestar al realizarlo?" },
        {
          k: "alergias_medicamentos",
          t: "area",
          l: "Alergias a medicamentos",
          h: "Anestesia, penicilina, ibuprofeno u otras drogas."
        },
        { k: "alergia_anestesia", t: "bool", l: "¿Es alérgico a la anestesia?" },
        { k: "alergia_penicilina", t: "bool", l: "¿Es alérgico a la penicilina?" },
        { k: "alergia_otros", t: "texto", l: "Otras alergias" },
        { k: "cicatriza_bien", t: "bool", l: "¿Cicatriza bien cuando se lastima?" },
        { k: "sangra_mucho", t: "bool", l: "¿Sangra mucho?" },
        { k: "colageno", t: "bool", l: "¿Tiene problema de colágeno (hiperlaxitud)?" },
        { k: "fiebre_reumatica", t: "bool", l: "¿Antecedentes de fiebre reumática?" },
        {
          k: "fiebre_reumatica_proteccion",
          t: "texto",
          l: "¿Se protege con alguna medicación?"
        },
        { k: "diabetes", t: "bool", l: "¿Es diabético?" },
        { k: "diabetes_controlada", t: "bool", l: "¿Está controlada?" },
        { k: "diabetes_tratamiento", t: "texto", l: "¿Con qué la controla?" },
        { k: "problemas_cardiacos", t: "bool", l: "¿Tiene algún problema cardíaco?" },
        { k: "problemas_cardiacos_detalle", t: "texto", l: "¿Cuál?" },
        {
          k: "anticoagulantes",
          t: "bool",
          l: "¿Toma aspirina o anticoagulantes con frecuencia?"
        },
        { k: "anticoagulantes_frecuencia", t: "texto", l: "¿Con qué frecuencia?" },
        { k: "presion_alta", t: "bool", l: "¿Tiene presión alta?" },
        { k: "chagas", t: "bool", l: "¿Tiene Chagas?" },
        { k: "chagas_tratamiento", t: "texto", l: "¿Está en tratamiento?" },
        { k: "problemas_renales", t: "bool", l: "¿Tiene problemas renales?" },
        { k: "ulcera_gastrica", t: "bool", l: "¿Tiene úlcera gástrica?" },
        { k: "hepatitis", t: "bool", l: "¿Tuvo hepatitis?" },
        {
          k: "hepatitis_tipo",
          t: "opcion",
          l: "Tipo de hepatitis",
          op: OPCIONES.hepatitis
        },
        { k: "problemas_hepaticos", t: "bool", l: "¿Tiene algún problema hepático?" },
        { k: "problemas_hepaticos_detalle", t: "texto", l: "¿Cuál?" },
        { k: "convulsiones", t: "bool", l: "¿Tuvo convulsiones?" },
        { k: "epilepsia", t: "bool", l: "¿Es epiléptico?" },
        { k: "epilepsia_medicacion", t: "texto", l: "Medicación que toma" },
        { k: "sifilis_gonorrea", t: "bool", l: "¿Ha tenido sífilis o gonorrea?" },
        {
          k: "enfermedad_infectocontagiosa",
          t: "bool",
          l: "¿Tuvo otra enfermedad infecto-contagiosa?"
        },
        {
          k: "enfermedad_infectocontagiosa_detalle",
          t: "texto",
          l: "¿Cuál?"
        },
        { k: "transfusiones", t: "bool", l: "¿Tuvo transfusiones?" },
        { k: "operaciones", t: "bool", l: "¿Fue operado alguna vez?" },
        { k: "operaciones_detalle", t: "texto", l: "¿De qué lo operaron?" },
        { k: "operaciones_cuando", t: "texto", l: "¿Cuándo?" },
        {
          k: "problemas_respiratorios",
          t: "bool",
          l: "¿Tiene algún problema respiratorio?"
        },
        { k: "problemas_respiratorios_detalle", t: "texto", l: "¿Cuál?" },
        { k: "fuma", t: "bool", l: "¿Fuma?" },
        { k: "embarazo", t: "bool", l: "¿Está embarazada?" },
        {
          k: "embarazo_meses",
          t: "numero",
          l: "¿De cuántos meses?",
          min: 0,
          max: 12
        },
        {
          k: "otra_enfermedad",
          t: "bool",
          l: "¿Otra enfermedad o recomendación médica que quiera dejar constancia?"
        },
        { k: "otra_enfermedad_detalle", t: "area", l: "¿Cuál?" },
        {
          k: "tratamientos_alternativos",
          t: "area",
          l: "Homeopatía, acupuntura u otros tratamientos"
        },
        { k: "medico_clinico", t: "texto", l: "Médico clínico" },
        {
          k: "centro_derivacion",
          t: "texto",
          l: "Clínica u hospital para una derivación"
        }
      ]
    },
    {
      id: "odontologicos",
      titulo: "Historia clínica odontológica",
      nota: "Qué lo trae a la consulta y qué dolor tiene.",
      publico: true,
      campos: [
        {
          k: "consulta_previa",
          t: "bool",
          l: "¿Consultó antes con otro profesional por este problema?"
        },
        { k: "medicacion_previa", t: "bool", l: "¿Tomó algún medicamento?" },
        { k: "medicacion_previa_detalle", t: "texto", l: "Nombre de los medicamentos" },
        { k: "medicacion_previa_desde", t: "texto", l: "¿Desde cuándo?" },
        { k: "medicacion_previa_resultados", t: "bool", l: "¿Obtuvo resultados?" },
        { k: "dolor", t: "bool", l: "¿Ha tenido dolor?" },
        {
          k: "dolor_intensidad",
          t: "opcion",
          l: "Intensidad del dolor",
          op: OPCIONES.intensidad
        },
        {
          k: "dolor_duracion",
          t: "opcion",
          l: "Duración del dolor",
          op: OPCIONES.duracion
        },
        {
          k: "dolor_desencadenante",
          t: "opcion",
          l: "¿Cómo aparece el dolor?",
          op: OPCIONES.desencadenante
        },
        {
          k: "dolor_estimulo",
          t: "opcion",
          l: "¿Qué lo desencadena?",
          op: OPCIONES.estimulo
        },
        {
          k: "dolor_localizacion",
          t: "opcion",
          l: "Localización del dolor",
          op: OPCIONES.localizacion
        },
        {
          k: "dolor_zona",
          t: "texto",
          l: "¿Dónde duele? ¿Hacia dónde se irradia?"
        },
        { k: "dolor_calmante", t: "texto", l: "¿Puede calmarlo con algo?" },
        { k: "traumatismo_dental", t: "bool", l: "¿Sufrió algún golpe en los dientes?" },
        { k: "traumatismo_cuando", t: "texto", l: "¿Cuándo fue el golpe?" },
        { k: "traumatismo_como", t: "texto", l: "¿Cómo se produjo?" },
        { k: "fractura_dental", t: "bool", l: "¿Se le fracturó algún diente?" },
        { k: "fractura_cual", t: "texto", l: "¿Cuál?" },
        { k: "fractura_tratamiento", t: "texto", l: "¿Recibió algún tratamiento?" },
        { k: "dificultad_hablar", t: "bool", l: "¿Tiene dificultad para hablar?" },
        { k: "dificultad_masticar", t: "bool", l: "¿Dificultad para masticar?" },
        { k: "dificultad_abrir_boca", t: "bool", l: "¿Dificultad para abrir la boca?" },
        {
          k: "dificultad_tragar",
          t: "bool",
          l: "¿Dificultad para tragar los alimentos?"
        }
      ]
    },
    {
      id: "examen",
      titulo: "Examen bucal",
      nota: "Lo completa el profesional durante la revisión.",
      publico: false,
      campos: [
        { k: "labios_anormal", t: "bool", l: "¿Observó algo anormal en los labios?" },
        { k: "lengua_anormal", t: "bool", l: "¿En la lengua?" },
        { k: "paladar_anormal", t: "bool", l: "¿En el paladar?" },
        { k: "piso_boca_anormal", t: "bool", l: "¿En el piso de boca?" },
        { k: "carrillos_anormal", t: "bool", l: "¿En los carrillos?" },
        { k: "rebordes_anormal", t: "bool", l: "¿En los rebordes?" },
        { k: "trigono_anormal", t: "bool", l: "¿En el trígono retromolar?" },
        { k: "retromolar_anormal", t: "bool", l: "¿En la zona retromolar?" },
        { k: "examen_bucal_detalle", t: "area", l: "Detalle del examen bucal" },
        { k: "lesiones_manchas", t: "bool", l: "¿Manchas?" },
        { k: "lesiones_abultamiento", t: "bool", l: "¿Abultamiento de los tejidos?" },
        { k: "lesiones_ulceraciones", t: "bool", l: "¿Ulceraciones?" },
        { k: "lesiones_ampollas", t: "bool", l: "¿Ampollas?" },
        { k: "lesiones_otros", t: "texto", l: "Otras lesiones" },
        { k: "sangrado_encias", t: "bool", l: "¿Le sangran las encías?" },
        { k: "sangrado_encias_cuando", t: "texto", l: "¿Cuándo?" },
        { k: "pus_boca", t: "bool", l: "¿Sale pus de algún lugar de la boca?" },
        { k: "pus_boca_donde", t: "texto", l: "¿De dónde?" },
        { k: "movilidad_dental", t: "bool", l: "¿Tiene movilidad en sus dientes?" },
        { k: "dientes_altos", t: "bool", l: "¿Al morder siente altos los dientes?" },
        { k: "cara_hinchada", t: "bool", l: "¿Ha tenido la cara hinchada?" },
        { k: "cara_hinchada_hielo", t: "bool", l: "¿Se puso hielo?" },
        { k: "cara_hinchada_calor", t: "bool", l: "¿Se puso calor?" },
        { k: "cara_hinchada_otros", t: "texto", l: "¿Otros tratamientos caseros?" },
        {
          k: "momentos_azucar",
          t: "numero",
          l: "Momentos de azúcar diarios",
          min: 0,
          max: 50
        },
        { k: "indice_placa", t: "texto", l: "Índice de placa" },
        {
          k: "higiene_bucal",
          t: "opcion",
          l: "Estado de la higiene bucal",
          op: OPCIONES.higiene
        },
        { k: "sarro", t: "bool", l: "¿Presencia de sarro?" },
        { k: "enfermedad_periodontal", t: "bool", l: "¿Enfermedad periodontal?" }
      ]
    },
    {
      id: "diagnostico",
      titulo: "Diagnóstico y plan",
      nota: "Diagnóstico presuntivo y plan de tratamiento del profesional.",
      publico: false,
      campos: [
        { k: "diagnostico_presuntivo", t: "area", l: "Diagnóstico presuntivo" },
        { k: "plan_tratamiento", t: "area", l: "Plan de tratamiento" },
        { k: "fecha_plan", t: "fecha", l: "Fecha del plan" },
        { k: "observaciones", t: "area", l: "Observaciones" }
      ]
    }
  ];

  /* --------------------------- Fechas y edad ----------------------------- */

  /* Edad en años cumplidos a partir de un valor ``AAAA-MM-DD``. Devuelve
     ``null`` si el campo está vacío o la fecha no es válida, así se puede
     dejar en blanco sin mostrar «NaN». */
  function calcularEdad(texto) {
    var partes = String(texto || "").split("-");
    if (partes.length !== 3) return null;
    var anio = parseInt(partes[0], 10);
    var mes = parseInt(partes[1], 10);
    var dia = parseInt(partes[2], 10);
    if (!anio || !mes || !dia) return null;
    var nacimiento = new Date(anio, mes - 1, dia);
    if (
      nacimiento.getFullYear() !== anio ||
      nacimiento.getMonth() !== mes - 1 ||
      nacimiento.getDate() !== dia
    ) {
      return null;
    }
    var hoy = new Date();
    var edad = hoy.getFullYear() - anio;
    if (
      hoy.getMonth() < mes - 1 ||
      (hoy.getMonth() === mes - 1 && hoy.getDate() < dia)
    ) {
      edad -= 1;
    }
    return edad >= 0 && edad <= 130 ? edad : null;
  }

  function textoEdad(edad) {
    return edad === null ? "" : "Edad: " + edad + " años";
  }

  /* ``AAAA-MM-DD`` → ``DD/MM/AAAA`` para el resumen. No se usa ``new Date``:
     interpretaría la fecha como UTC y podría mostrar el día anterior. */
  function fechaLegible(texto) {
    var partes = String(texto || "").split("-");
    if (partes.length !== 3) return String(texto || "");
    return partes[2] + "/" + partes[1] + "/" + partes[0];
  }

  function textoFecha(campo, valor) {
    var texto = fechaLegible(valor);
    if (campo.k !== "fecha_nacimiento") return texto;
    var edad = calcularEdad(valor);
    return edad === null ? texto : texto + " · " + edad + " años";
  }

  /* --------------------------- Formulario -------------------------------- */

  function crearCampo(campo) {
    var id = "fm-" + campo.k;

    if (campo.t === "bool") {
      var fila = document.createElement("label");
      fila.className = "fm-campo fm-campo-bool";
      fila.setAttribute("for", id);
      var texto = document.createElement("span");
      texto.className = "fm-campo-texto";
      var fuerte = document.createElement("strong");
      fuerte.textContent = campo.l;
      texto.appendChild(fuerte);
      if (campo.h) {
        var chico = document.createElement("small");
        chico.textContent = campo.h;
        texto.appendChild(chico);
      }
      var check = document.createElement("input");
      check.type = "checkbox";
      check.id = id;
      check.className = "fm-check";
      check.setAttribute("data-campo", campo.k);
      fila.appendChild(texto);
      fila.appendChild(check);
      return fila;
    }

    var caja = document.createElement("div");
    caja.className =
      "fm-campo" + (campo.t === "area" || campo.ancho ? " fm-campo-ancho" : "");

    var etiqueta = document.createElement("label");
    etiqueta.className = "fm-etiqueta";
    etiqueta.setAttribute("for", id);
    etiqueta.textContent = campo.l;
    caja.appendChild(etiqueta);

    var control;
    if (campo.t === "area") {
      control = document.createElement("textarea");
      control.rows = 2;
      control.maxLength = MAX_TEXTO;
    } else if (campo.t === "opcion") {
      control = document.createElement("select");
      var vacio = document.createElement("option");
      vacio.value = "";
      vacio.textContent = "Sin dato";
      control.appendChild(vacio);
      (campo.op || []).forEach(function (opcion) {
        var item = document.createElement("option");
        item.value = opcion.v;
        item.textContent = opcion.l;
        control.appendChild(item);
      });
    } else {
      control = document.createElement("input");
      if (campo.t === "numero") {
        control.type = "number";
        control.inputMode = "numeric";
        if (campo.min !== undefined) control.min = String(campo.min);
        if (campo.max !== undefined) control.max = String(campo.max);
      } else if (campo.t === "fecha") {
        control.type = "date";
      } else {
        control.type = "text";
        control.maxLength = MAX_TEXTO;
      }
    }
    control.id = id;
    control.className = "fm-control";
    control.setAttribute("data-campo", campo.k);
    caja.appendChild(control);

    if (campo.h) {
      var ayuda = document.createElement("p");
      ayuda.className = "fm-ayuda";
      ayuda.textContent = campo.h;
      caja.appendChild(ayuda);
    }

    /* La fecha de nacimiento muestra la edad calculada mientras se escribe:
       evita que la secretaría tenga que hacer la cuenta y deja ver al paciente
       que la fecha quedó bien. */
    if (campo.k === "fecha_nacimiento") {
      var edad = document.createElement("p");
      edad.className = "fm-ayuda fm-edad";
      caja.appendChild(edad);
      var actualizar = function () {
        edad.textContent = textoEdad(calcularEdad(control.value));
      };
      control.addEventListener("input", actualizar);
      control.addEventListener("change", actualizar);
      control._fmActualizarEdad = actualizar;
      actualizar();
    }
    return caja;
  }

  function renderFormulario(contenedor, datos, opciones) {
    if (!contenedor) return;
    var ajustes = opciones || {};
    contenedor.replaceChildren();

    SECCIONES.forEach(function (seccion) {
      if (ajustes.soloPaciente && !seccion.publico) return;

      var bloque = document.createElement("section");
      bloque.className = "fm-seccion";
      bloque.setAttribute("data-seccion", seccion.id);

      var titulo = document.createElement("h3");
      titulo.className = "fm-titulo";
      titulo.textContent = seccion.titulo;
      bloque.appendChild(titulo);

      if (seccion.nota) {
        var nota = document.createElement("p");
        nota.className = "fm-nota";
        nota.textContent = seccion.nota;
        bloque.appendChild(nota);
      }

      var grid = document.createElement("div");
      grid.className = "fm-grid";
      seccion.campos.forEach(function (campo) {
        grid.appendChild(crearCampo(campo));
      });
      bloque.appendChild(grid);
      contenedor.appendChild(bloque);
    });

    escribirFormulario(contenedor, datos);
  }

  function escribirFormulario(contenedor, datos) {
    if (!contenedor) return;
    var valores = datos || {};
    var campos = contenedor.querySelectorAll("[data-campo]");
    Array.prototype.forEach.call(campos, function (control) {
      var clave = control.getAttribute("data-campo");
      var valor = valores[clave];
      if (control.type === "checkbox") {
        control.checked = Boolean(valor);
        return;
      }
      control.value = valor === null || valor === undefined ? "" : String(valor);
      // La ayuda de la edad no se actualiza sola: el valor se asignó por código
      // y no dispara ``input``.
      if (control._fmActualizarEdad) control._fmActualizarEdad();
    });
  }

  function leerFormulario(contenedor) {
    var datos = {};
    if (!contenedor) return datos;
    var campos = contenedor.querySelectorAll("[data-campo]");
    Array.prototype.forEach.call(campos, function (control) {
      var clave = control.getAttribute("data-campo");
      if (control.type === "checkbox") {
        datos[clave] = control.checked;
        return;
      }
      var valor = String(control.value || "").trim();
      if (!valor) {
        datos[clave] = null;
        return;
      }
      if (control.type === "number") {
        var numero = parseInt(valor, 10);
        datos[clave] = isNaN(numero) ? null : numero;
        return;
      }
      datos[clave] = valor;
    });
    return datos;
  }

  /* ----------------------------- Resumen --------------------------------- */

  function etiquetaOpcion(campo, valor) {
    var lista = campo.op || [];
    for (var i = 0; i < lista.length; i += 1) {
      if (lista[i].v === valor) return lista[i].l;
    }
    return String(valor);
  }

  function tieneDato(campo, valor) {
    if (campo.t === "bool") return valor === true;
    return valor !== null && valor !== undefined && String(valor).trim() !== "";
  }

  function renderResumen(contenedor, datos, opciones) {
    if (!contenedor) return;
    var valores = datos || {};
    var ajustes = opciones || {};
    contenedor.replaceChildren();

    SECCIONES.forEach(function (seccion) {
      if (ajustes.soloPaciente && !seccion.publico) return;

      var informados = 0;
      seccion.campos.forEach(function (campo) {
        if (tieneDato(campo, valores[campo.k])) informados += 1;
      });

      var bloque = document.createElement("details");
      bloque.className =
        "fm-resumen-seccion" + (informados ? "" : " fm-resumen-vacia");
      bloque.open = Boolean(informados) || Boolean(ajustes.abrirVacias);

      var titulo = document.createElement("summary");
      titulo.className = "fm-resumen-titulo";
      titulo.textContent =
        seccion.titulo + (informados ? " · " + informados + " dato/s" : " · sin datos");
      bloque.appendChild(titulo);

      var lista = document.createElement("dl");
      lista.className = "fm-resumen-lista";

      seccion.campos.forEach(function (campo) {
        var valor = valores[campo.k];
        var fila = document.createElement("div");
        fila.className = "fm-resumen-fila";

        var termino = document.createElement("dt");
        termino.textContent = campo.l;

        var detalle = document.createElement("dd");
        if (campo.t === "bool") {
          detalle.textContent = valor ? "Sí" : "No";
          detalle.className = valor ? "fm-si" : "fm-no";
        } else if (campo.t === "opcion" && tieneDato(campo, valor)) {
          detalle.textContent = etiquetaOpcion(campo, valor);
        } else if (campo.t === "fecha" && tieneDato(campo, valor)) {
          detalle.textContent = textoFecha(campo, valor);
        } else if (tieneDato(campo, valor)) {
          detalle.textContent = String(valor);
        } else {
          detalle.textContent = "Sin dato";
          detalle.className = "fm-vacio";
        }

        fila.appendChild(termino);
        fila.appendChild(detalle);
        lista.appendChild(fila);
      });

      bloque.appendChild(lista);
      contenedor.appendChild(bloque);
    });
  }

  window.FICHA_MEDICA = {
    MAX_TEXTO: MAX_TEXTO,
    SECCIONES: SECCIONES,
    renderFormulario: renderFormulario,
    escribirFormulario: escribirFormulario,
    leerFormulario: leerFormulario,
    renderResumen: renderResumen,
    etiquetaOpcion: etiquetaOpcion,
    calcularEdad: calcularEdad,
    fechaLegible: fechaLegible
  };
})();
