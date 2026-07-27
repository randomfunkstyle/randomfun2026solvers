// Where does a program actually spend its time?
//
// A sampling profiler for .man grids. Steps the engine and periodically records
// every runner's cell; the resulting per-cell counts are a direct picture of
// where the men are standing, which for a machine whose cost is *walking* is the
// same thing as where the ticks go.
//
// Sampling rather than tracing on purpose: a snapshot carries every pipe's
// contents and (on a display problem) 768 pixels, so parsing one per tick is far
// slower than the run itself. A profile does not need every tick — it needs an
// unbiased sample, and stepping in fixed strides gives that. `--stride 1` is
// available when exactness matters more than speed.
//
// Blocked men are the whole point: a man stuck on `r` waiting for a pipe is
// sampled in the same cell every time, so a blocking hot spot shows up as a tall
// bar rather than disappearing the way an instruction counter would hide it.
//
// **Round-gated machines need `--expected`.** A test case's rounds are separated
// by `/`, and the judge withholds round N+1's input until round N's output has
// all been received (`GRADING.md` §Rounds). The engine only applies that gate
// when it is given the expected output, exactly as `lm.mjs judge` does — so
// profiling a gated machine without it hands the program every round at once and
// measures a jam, not the program. `sort-numbers`' 14x14 ring reads 97-99%
// stalled that way against 5% under the gate: the profiler was reporting the
// missing flag as the machine's bottleneck.
//
// With `--expected` the sampling window also ends where the score does, at the
// settle tick, instead of running to `--cap`. A profile whose window is the cap
// is mostly the idle tail after the last value — the same distortion, spread
// evenly instead of concentrated.
//
// Usage:
//   node littleman/tools/heatmap.mjs prog.man --input "..." [--expected "..."]
//        [--frames JSON] [--cap N] [--stride N] [--json out.json] [--top N]
import fs from "node:fs"; import vm from "node:vm"; import path from "node:path";
import { fileURLToPath } from "node:url";
const LM = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
vm.runInThisContext(fs.readFileSync(path.join(LM, "wasm_exec.js"), "utf8"));
const go = new globalThis.Go();
const mod = await WebAssembly.instantiate(fs.readFileSync(path.join(LM, "littleman.wasm")), go.importObject);
go.run(mod.instance); while (!globalThis.littlemanWasm) await new Promise(s => setTimeout(s, 30));
const api = globalThis.littlemanWasm;

const args = process.argv.slice(2);
const file = args[0];
const opt = (name, dflt) => { const i = args.indexOf(name); return i < 0 ? dflt : args[i + 1]; };
const input = opt("--input", "");
const expected = opt("--expected", "");
const frames = opt("--frames", "");
const CAP = +opt("--cap", 3_000_000);
const STRIDE = +opt("--stride", 25);
const TOP = +opt("--top", 25);
const outJson = opt("--json", null);
// Gating is what the judge does, so it is also what a profile has to do. Only
// stop at settle in that mode: a plain run reports `outputSettled` as soon as
// the pipe is momentarily empty, which would cut every existing profile short.
const gated = expected !== "";

const rows = fs.readFileSync(file, "utf8").replace(/\n$/, "").split("\n");
const W = Math.max(...rows.map(r => r.length)), H = rows.length;

const id = api.newSession();
let s = JSON.parse(api.load(id, rows, input, expected, frames));
if (s.type === "error") { console.error(`load: ${s.message}`); process.exit(1); }

// Per *runner*, not just per cell. A servant blocked on its input `r` — the
// adapter waiting for a request, the tape waiting for the adapter — is merely
// idle, and pooling it with the CPU's own time reads as a bottleneck when it is
// the opposite. Only the critical-path man's profile says where the ticks go.
const counts = new Map();            // "x,y" -> samples (all runners)
const perRunner = new Map();         // id -> Map("x,y" -> samples)
const blocked = new Map();           // id -> samples spent on the same cell as last time
const stalledAt = new Map();         // id -> Map("x,y" -> samples he was stuck there)
const lastPos = new Map();
let samples = 0, t = 0, halted = false, fatal = null;
const bump = (snap) => {
  for (const r of snap.entities?.runners ?? []) {
    if (r.halted) continue;
    const k = `${r.pos[0]},${r.pos[1]}`;
    counts.set(k, (counts.get(k) ?? 0) + 1);
    if (!perRunner.has(r.id)) perRunner.set(r.id, new Map());
    const m = perRunner.get(r.id);
    m.set(k, (m.get(k) ?? 0) + 1);
    // A total is not a diagnosis. "5% stalled" is a number you cannot act on;
    // "5% stalled, all of it on the ring's `r` and none on the output `s`" tells
    // you which pipe to lengthen. Only sound at `--stride 1`, where standing in
    // the same cell twice running really is a block rather than a wide sample.
    if (lastPos.get(r.id) === k) {
      blocked.set(r.id, (blocked.get(r.id) ?? 0) + 1);
      if (!stalledAt.has(r.id)) stalledAt.set(r.id, new Map());
      const sm = stalledAt.get(r.id);
      sm.set(k, (sm.get(k) ?? 0) + 1);
    }
    lastPos.set(r.id, k);
  }
  samples++;
};

bump(s);
let settled = false;
while (t < CAP) {
  const n = Math.min(STRIDE, CAP - t);
  s = JSON.parse(api.stepN(id, n, false));
  t += n;
  if (s.type === "error") { fatal = "engine: " + s.message; break; }
  if (s.fatal) { fatal = `${s.fatal.reason} at ${JSON.stringify(s.fatal.pos)}`; break; }
  bump(s);
  if (s.halted) { halted = true; break; }
  if (gated && s.outputSettled) { settled = true; break; }
}
api.closeSession(id);

const cells = [...counts.entries()]
  .map(([k, n]) => { const [x, y] = k.split(",").map(Number); return { x, y, n, ch: rows[y]?.[x] ?? " " }; })
  .sort((a, b) => b.n - a.n);
const total = cells.reduce((a, c) => a + c.n, 0);

const window = settled ? " (settled)" : halted ? " (halted)" : gated ? "" : " (cap — no --expected, so ungated)";
console.log(`${file}: ${W}x${H}  ticks ${t}${window}${fatal ? "  FATAL " + fatal : ""}`);
console.log(`samples ${samples} x stride ${STRIDE}, ${cells.length} distinct cells occupied`);
if (!gated && /\//.test(input)) {
  console.log(
    "\n  ! the input has `/` round separators but no --expected, so the engine\n" +
    "    released every round at once. Pass --expected to gate; without it a\n" +
    "    round-based machine profiles as a jam rather than as itself."
  );
}
console.log(`\ntop ${TOP} cells by occupancy:`);
console.log(`  ${"cell".padEnd(11)} ${"glyph".padEnd(6)} ${"share".padStart(7)}  ticks(est)`);
for (const c of cells.slice(0, TOP)) {
  const share = c.n / total;
  console.log(
    `  ${`(${c.x},${c.y})`.padEnd(11)} ${JSON.stringify(c.ch).padEnd(6)} ` +
    `${(share * 100).toFixed(2).padStart(6)}%  ${Math.round(share * t).toString().padStart(9)}`
  );
}

// ── per-runner, which is what actually tells you the bottleneck ─────────────
const runners = [...perRunner.entries()].map(([id, m]) => {
  const own = [...m.entries()]
    .map(([k, n]) => { const [x, y] = k.split(",").map(Number); return { x, y, n, ch: rows[y]?.[x] ?? " " }; })
    .sort((a, b) => b.n - a.n);
  const stalls = [...(stalledAt.get(id) ?? new Map()).entries()]
    .map(([k, n]) => { const [x, y] = k.split(",").map(Number); return { x, y, n, ch: rows[y]?.[x] ?? " " }; })
    .sort((a, b) => b.n - a.n);
  return { id, samples: own.reduce((a, c) => a + c.n, 0), stalled: blocked.get(id) ?? 0, cells: own, stalls };
});
console.log(`\nper runner (stalled = sampled twice running in the same cell):`);
for (const r of runners) {
  const pct = (r.stalled / Math.max(1, r.samples)) * 100;
  const hot = r.cells.slice(0, 4).map(c => `(${c.x},${c.y})${JSON.stringify(c.ch)} ${((c.n / r.samples) * 100).toFixed(0)}%`).join("  ");
  console.log(`  runner ${r.id}: ${r.samples} samples, ${pct.toFixed(0)}% stalled | ${hot}`);
  if (r.stalls.length) {
    const where = r.stalls.slice(0, 4)
      .map(c => `(${c.x},${c.y})${JSON.stringify(c.ch)} ${c.n} (${((c.n / r.samples) * 100).toFixed(1)}%)`).join("  ");
    console.log(`      stalled on: ${where}`);
  }
}

if (outJson) {
  fs.writeFileSync(outJson, JSON.stringify({
    file, w: W, h: H, ticks: t, samples, stride: STRIDE, halted, fatal,
    gated, settled, total, cells, runners,
  }));
  console.log(`\nwrote ${outJson}`);
}
