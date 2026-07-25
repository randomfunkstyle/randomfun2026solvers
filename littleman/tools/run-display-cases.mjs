// Verify a display-judged .man: step it and compare every committed frame, in
// order, against the problem's expected frames.
//
// The engine exposes the panel's *current* buffer at a tick, not a commit history,
// so this records each distinct non-blank front buffer as it appears. A streaming
// compare is what the judge does too (SPEC.md: "every frame committed by a SWAP
// must equal the next expected frame in order").
//
// Usage: node run-display-cases.mjs prog.man problem.json [cap] [step]
import fs from "node:fs"; import vm from "node:vm"; import path from "node:path";
import { fileURLToPath } from "node:url";
const LM = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
vm.runInThisContext(fs.readFileSync(path.join(LM, "wasm_exec.js"), "utf8"));
const go = new globalThis.Go();
const mod = await WebAssembly.instantiate(fs.readFileSync(path.join(LM, "littleman.wasm")), go.importObject);
go.run(mod.instance); while (!globalThis.littlemanWasm) await new Promise(s => setTimeout(s, 30));
const api = globalThis.littlemanWasm;

const file = process.argv[2];
const prob = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const CAP = +(process.argv[4] ?? 2_000_000);
const STEP = +(process.argv[5] ?? 100);
const rows = fs.readFileSync(file, "utf8").replace(/\n$/, "").split("\n");
const w = Math.max(...rows.map(r => r.length)), h = rows.length;
const W = prob.io.display.width, H = prob.io.display.height;
console.log(`${file}: ${w}x${h}  footprint area2=${Math.max(w, h) ** 2}`);

const hex = (front) => {
  const out = [];
  for (let r = 0; r < H; r++) {
    let line = "";
    for (let c = 0; c < W; c++) line += front[r * W + c].toString(16);
    out.push(line);
  }
  return out;
};

let pass = 0, ticks = [];
for (const c of prob.publicTestData) {
  const inp = c.rounds.map(r => (r.in ?? []).join(" ")).join(" / ");
  const want = c.rounds.filter(r => r.frames).map(r => r.frames[0]);
  const id = api.newSession();
  let s = JSON.parse(api.load(id, rows, inp, "", ""));
  if (s.type === "error") { console.log(`  FAIL(load) ${c.name}: ${s.message}`); api.closeSession(id); continue; }
  const got = []; let t = 0, bad = null, prev = null;
  while (t < CAP) {
    const disp = (s.entities?.displays ?? [])[0];
    if (disp) {
      const key = disp.front.join(",");
      if (disp.front.some(v => v !== 0) && key !== prev) { got.push(hex(disp.front)); prev = key; }
    }
    // stop as soon as every expected frame has been seen and matched
    if (got.length >= want.length) break;
    if (s.halted) { bad = `halted (${s.reason}) with ${got.length}/${want.length} frames`; break; }
    s = JSON.parse(api.stepN(id, Math.min(STEP, CAP - t), false)); t += Math.min(STEP, CAP - t);
    if (s.type === "error") { bad = "engine: " + s.message; break; }
    if (s.fatal) { bad = `fatal: ${s.fatal.reason} at ${JSON.stringify(s.fatal.pos)}`; break; }
  }
  api.closeSession(id);
  let mismatch = null;
  for (let i = 0; i < Math.min(got.length, want.length) && !mismatch; i++)
    for (let r = 0; r < H && !mismatch; r++)
      if (got[i][r] !== want[i][r]) mismatch = `frame ${i} row ${r}:\n       got  ${got[i][r]}\n       want ${want[i][r]}`;
  const ok = !bad && !mismatch && got.length === want.length;
  if (ok) { pass++; ticks.push(t); console.log(`  ok    ${c.name}  ${want.length} frames (<=${t} ticks)`); }
  else console.log(`  FAIL  ${c.name}  frames ${got.length}/${want.length}  ${bad ?? ""}${mismatch ? "\n     " + mismatch : ""}`);
}
const avg = ticks.length ? ticks.reduce((a, b) => a + b, 0) / ticks.length : 0;
const area2 = Math.max(w, h) ** 2;
console.log(`${pass}/${prob.publicTestData.length} passed` + (ticks.length ? `, ticks max ${Math.max(...ticks)} avg ${Math.round(avg)}` : ""));
if (pass === prob.publicTestData.length)
  console.log(`score = area2 ${area2} x avgTicks ${Math.round(avg)} = ${(area2 * avg).toLocaleString("en-US", { maximumFractionDigits: 0 })}`);
process.exit(pass === prob.publicTestData.length ? 0 : 1);
