// Focused tracer for generated programs with a .debug.json sidecar.
//
// Usage:
//   node littleman/tools/debug-trace.mjs file.man debug.json "input" from to [name...]
//   node littleman/tools/debug-trace.mjs file.man debug.json --scenario name
//
// Prints only ticks in [from,to], annotating each runner with named regions and
// lanes. Named pipe lanes print only changes to their contents (not every
// queue shift). If names are supplied, prints only matching runner/pipe
// activity.
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const LM = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
vm.runInThisContext(fs.readFileSync(path.join(LM, "wasm_exec.js"), "utf8"));
const go = new globalThis.Go();
const mod = await WebAssembly.instantiate(fs.readFileSync(path.join(LM, "littleman.wasm")), go.importObject);
go.run(mod.instance);
while (!globalThis.littlemanWasm) await new Promise((s) => setTimeout(s, 30));
const api = globalThis.littlemanWasm;

const D = { "1,0": ">", "-1,0": "<", "0,1": "v", "0,-1": "^" };

const [file, debugFile, ...args] = process.argv.slice(2);
if (!file || !debugFile) {
  console.error("usage: node littleman/tools/debug-trace.mjs file.man debug.json \"input\" from to [name...] | --scenario name");
  process.exit(2);
}

const rows = fs.readFileSync(file, "utf8").replace(/\n$/, "").split("\n");
const debug = JSON.parse(fs.readFileSync(debugFile, "utf8"));
let input = "";
let from = 0;
let to = 100;
let watchNames = [];
if (args[0] === "--scenario") {
  const name = args[1];
  const scenario = (debug.scenarios || []).find((s) => s.name === name);
  if (!scenario) {
    console.error(`unknown scenario ${JSON.stringify(name)}; available: ${(debug.scenarios || []).map((s) => s.name).join(", ") || "(none)"}`);
    process.exit(2);
  }
  input = scenario.input || "";
  from = scenario.from_tick ?? 0;
  to = scenario.to_tick ?? 100;
  watchNames = scenario.watch || [];
  console.log(`scenario ${scenario.name}: ${scenario.note || "(no note)"}`);
} else {
  input = args[0] ?? "";
  from = +(args[1] ?? "0");
  to = +(args[2] ?? "100");
  watchNames = args.slice(3);
}
const watch = new Set(watchNames);
const analysis = JSON.parse(api.analyze(rows));
const regions = debug.regions || [];
const circles = debug.circles || [];
const lanes = debug.lanes || [];
const laneCells = lanes.map((l) => ({
  ...l,
  cellSet: new Set((l.cells || []).map(([x, y]) => `${x},${y}`)),
}));
const namedPipes = lanes
  .filter((l) => l.kind === "pipe")
  .map((l) => ({
    ...l,
    cellSet: new Set((l.cells || []).map(([x, y]) => `${x},${y}`)),
  }));

function namesAt(x, y) {
  const hits = [];
  for (const r of regions) {
    if (x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h) {
      hits.push(`region:${r.name}`);
    }
  }
  for (const c of circles) {
    if ((x - c.cx) ** 2 + (y - c.cy) ** 2 <= c.r ** 2) {
      hits.push(`region:${c.name}`);
    }
  }
  for (const l of laneCells) {
    if (l.cellSet.has(`${x},${y}`)) hits.push(`lane:${l.name}`);
  }
  return hits;
}

function watched(hits) {
  if (watch.size === 0) return true;
  return hits.some((h) => {
    const bare = h.split(":").slice(1).join(":");
    return watch.has(h) || watch.has(bare);
  });
}

function pipeNamesFor(anPipe) {
  const cells = (anPipe.path || []).map((p) => `${p.pos[0]},${p.pos[1]}`);
  const names = [];
  for (const lane of namedPipes) {
    let overlap = 0;
    for (const c of cells) if (lane.cellSet.has(c)) overlap++;
    if (overlap >= Math.min(cells.length, lane.cellSet.size) * 0.5) names.push(lane.name);
  }
  return names;
}

const pipeNames = (analysis.pipes || []).map(pipeNamesFor);
const lastPipeValues = new Map();

function summarizeValues(values) {
  const counts = new Map();
  for (const value of values) counts.set(value, (counts.get(value) || 0) + 1);
  return [...counts.entries()]
    .sort(([a], [b]) => a - b)
    .map(([value, count]) => count === 1 ? String(value) : `${value}x${count}`)
    .join(", ");
}

function cell(r) {
  return rows[r.pos[1]]?.[r.pos[0]] ?? "?";
}

const id = api.newSession();
let s = JSON.parse(api.load(id, rows, input, "", ""));
if (s.type === "error") {
  console.error("LOAD ERROR: " + s.message);
  process.exit(1);
}

for (let t = 0; t <= to; t++) {
  if (t >= from) {
    const runners = s.entities?.runners || [];
    const lines = [];
    for (const r of runners) {
      const [x, y] = r.pos;
      const hits = namesAt(x, y);
      if (!watched(hits)) continue;
      lines.push(
        `(${x},${y}) '${cell(r)}'${D[r.dir.join(",")] || "?"} A=${r.a} B=${r.b} BP=${r.backpack}` +
          (r.halted ? " HALT" : "") +
          (hits.length ? ` [${hits.join(", ")}]` : "")
      );
    }
    const pipes = s.entities?.pipes || [];
    for (let i = 0; i < pipes.length; i++) {
      const names = pipeNames[i] || [];
      if (watch.size !== 0 && !names.some((n) => watch.has(n) || watch.has(`lane:${n}`))) continue;
      if (watch.size === 0 && names.length === 0) continue;
      const vals = (pipes[i].values || []).map((v) => v.value ?? v);
      // Pipe positions and occupancy move every tick. For a memory trace the
      // durable signal is the set of values present: it changes when a write
      // commits, while the zero-value conveyor remains quiet.
      const signature = JSON.stringify([...new Set(vals)].sort((a, b) => a - b));
      const changed = lastPipeValues.get(i) !== signature;
      lastPipeValues.set(i, signature);
      if (!changed) continue;
      if (vals.length === 0 && watch.size !== 0) continue;
      lines.push(`pipe${i}${names.length ? ` [${names.map((n) => `lane:${n}`).join(", ")}]` : ""} values=[${summarizeValues(vals)}]`);
    }
    if (lines.length || watch.size === 0) {
      console.log(`t${String(t).padStart(5)} out=${JSON.stringify(s.output || [])}`);
      for (const line of lines) console.log("  " + line);
    }
  }
  if (s.halted) break;
  s = JSON.parse(api.stepN(id, 1, false));
  if (s.type === "error") {
    console.error("ERR " + s.message);
    break;
  }
}
api.closeSession(id);
