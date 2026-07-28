// Coarse probe: batch-step and report the CPU-region runner + output.
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

const [file, input, upTo, batch, x0S, x1S, y0S, y1S] = process.argv.slice(2);
const [x0, x1, y0, y1] = [x0S, x1S, y0S, y1S].map(Number);
const rows = fs.readFileSync(file, "utf8").replace(/\n$/, "").split("\n");
const id = api.newSession();
let s = JSON.parse(api.load(id, rows, input || "", "", ""));
if (s.type === "error") { console.error("LOAD:", s.message); process.exit(1); }
let t = 0;
while (t < Number(upTo)) {
  s = JSON.parse(api.stepN(id, Number(batch), false));
  t += Number(batch);
  if (s.type === "error") { console.log("ERR", s.message, t); break; }
  const hits = (s.entities?.runners || []).filter(
    (r) => r.pos[0] >= x0 && r.pos[0] <= x1 && r.pos[1] >= y0 && r.pos[1] <= y1,
  );
  console.log(
    `t=${t} out=[${(s.output || []).join(",")}] cpuMen=` +
      hits.map((r) => `(${r.pos[0]},${r.pos[1]}) a=${r.a} b=${r.b} bp=${r.backpack}`).join(" "),
  );
}
api.closeSession(id);
