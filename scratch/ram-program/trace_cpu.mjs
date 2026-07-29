// Trace the CPU man's fetches: log (opcode=BP, operand-cell A) at the fetch row.
// usage: node trace_cpu.mjs file.man "input" maxTicks fx fy
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LM = path.resolve(HERE, "../../littleman");
vm.runInThisContext(fs.readFileSync(path.join(LM, "wasm_exec.js"), "utf8"));
const go = new globalThis.Go();
const mod = await WebAssembly.instantiate(
  fs.readFileSync(path.join(LM, "littleman.wasm")),
  go.importObject,
);
go.run(mod.instance);
while (!globalThis.littlemanWasm) await new Promise((s) => setTimeout(s, 30));
const api = globalThis.littlemanWasm;

const [file, input, maxTicks, fxS, fyS] = process.argv.slice(2);
const fx = Number(fxS), fy = Number(fyS);
const rows = fs.readFileSync(file, "utf8").replace(/\n$/, "").split("\n");
const id = api.newSession();
let s = JSON.parse(api.load(id, rows, input || "", "", ""));
if (s.type === "error") { console.error("LOAD:", s.message); process.exit(1); }

let lastOut = 0;
for (let t = 0; t < Number(maxTicks); t++) {
  s = JSON.parse(api.stepN(id, 1, false));
  if (s.type === "error") { console.log("ERR", s.message, "at", t); break; }
  for (const run of s.entities?.runners || []) {
    const [x, y] = run.pos;
    if (y === fy && x === fx + 3) {
      console.log(`t=${t} op=${run.backpack} a=${run.a} b=${run.b}`);
    }
  }
  const out = s.output || [];
  if (out.length > lastOut) {
    console.log("OUTPUT:", out.join(" "), "at", t);
    lastOut = out.length;
    if (out.length >= 3) break;
  }
}
api.closeSession(id);
