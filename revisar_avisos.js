/* Comprobación temporal de la lógica de avisos de app.js (no se publica). */
const fs = require("fs");
const vm = require("vm");

const fuente = fs.readFileSync("app/static/app.js", "utf8");
const desde = fuente.indexOf("  /* Marca de la respuesta de un turno");
const hasta = fuente.indexOf("  /* --------------------------- Render de apoyo");
if (desde < 0 || hasta < 0) throw new Error("No se encontró el bloque de avisos");
const bloque = fuente.slice(desde, hasta);

const avisados = [];
const contexto = {
  state: { avisosTurnos: {} },
  console,
};
vm.createContext(contexto);
vm.runInContext(bloque, contexto);
// `avisarRespuestasDeWhatsApp` busca `notificarRespuestaTurno` al llamarla, así
// que este reemplazo (posterior a la evaluación del bloque) captura los avisos.
contexto.notificarRespuestaTurno = (t) => avisados.push(`${t.id}:${t.estado_turno}`);

const t = (id, estado) => ({
  id,
  estado_whatsapp: estado ? "RESPONDIDO" : "ENVIADO",
  estado_turno: estado || "PENDIENTE",
  fecha_hora_inicio: `2026-11-23T0${id}:00:00`,
});
const fallos = [];
const check = (nombre, cond) => {
  if (!cond) fallos.push(nombre);
  console.log((cond ? "OK   " : "FALLA") + " " + nombre);
};

// 1. Carga inicial: los ya respondidos no generan aviso.
contexto.avisarRespuestasDeWhatsApp({}, [t(1, "CONFIRMADO")]);
check("carga inicial sin avisos", avisados.length === 0);

// 2. Un turno conocido que responde confirma: avisa una vez.
contexto.avisarRespuestasDeWhatsApp({ 1: "" }, [t(1, "CONFIRMADO")]);
check("avisa la confirmación", avisados.join() === "1:CONFIRMADO");

// 3. La misma lectura otra vez no repite el aviso.
contexto.avisarRespuestasDeWhatsApp({ 1: "RESPONDIDO:CONFIRMADO" }, [t(1, "CONFIRMADO")]);
check("no repite el aviso", avisados.length === 1);

// 4. Cambió de opinión: avisa la cancelación.
contexto.avisarRespuestasDeWhatsApp({ 1: "RESPONDIDO:CONFIRMADO" }, [t(1, "CANCELADO")]);
check("avisa el cambio a cancelado", avisados.join() === "1:CONFIRMADO,1:CANCELADO");

// 5. Sin respuesta nueva no hay aviso.
contexto.avisarRespuestasDeWhatsApp({ 2: "" }, [t(2)]);
check("sin respuesta no avisa", avisados.length === 2);

// 6. Muchos juntos: salen como máximo tres, los más próximos.
avisados.length = 0;
contexto.state.avisosTurnos = {};
const antes = { 3: "", 4: "", 5: "", 6: "", 7: "" };
contexto.avisarRespuestasDeWhatsApp(antes, [
  t(7, "CONFIRMADO"), t(6, "CONFIRMADO"), t(5, "CANCELADO"),
  t(4, "CONFIRMADO"), t(3, "CONFIRMADO"),
]);
check("máximo tres avisos", avisados.join() === "3:CONFIRMADO,4:CONFIRMADO,5:CANCELADO");

if (fallos.length) {
  console.error("Fallaron: " + fallos.join(", "));
  process.exit(1);
}
console.log("Lógica de avisos OK");
