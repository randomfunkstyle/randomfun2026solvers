#!/usr/bin/env node
// Headless runner for littleman `.man` programs.
//
// Drives the exact interpreter the online editor uses: `littleman.wasm`
// (Go 1.25.7, GOOS=js GOARCH=wasm) via Go's standard `wasm_exec.js` runtime.
// No reimplementation — this is the reference engine, run 1:1.
//
// Usage:
//   lm.mjs run  <file.man> [--input "1 2 3"] [--json] [--max-ticks N]
//   lm.mjs tick <file.man> [n] [--input "1 2 3"] [--json]
//
// `run`  executes to completion and prints the program output (space-joined ints).
// `tick` advances n ticks (default 1) and prints the rendered ASCII map + a
//        hands/backpack/output summary (or the raw snapshot with --json).

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WASM_EXEC = path.join(HERE, "wasm_exec.js");
const WASM = path.join(HERE, "littleman.wasm");

const DEFAULT_MAX_TICKS = 1_000_000;
const STEP_CHUNK = 1000; // ticks per stepN call in the run loop

// ── wasm boot ────────────────────────────────────────────────────────────
let _api = null; // globalThis.littlemanWasm, cached

async function boot() {
  if (_api) return _api;

  // Define globalThis.Go by evaluating Go's wasm_exec.js (an IIFE). It only
  // sets globalThis.Go — no auto-run block — so this is side-effect-safe.
  if (typeof globalThis.Go === "undefined") {
    const src = fs.readFileSync(WASM_EXEC, "utf8");
    vm.runInThisContext(src, { filename: WASM_EXEC });
    if (typeof globalThis.Go === "undefined") {
      throw new Error("wasm_exec.js did not define globalThis.Go");
    }
  }

  const go = new globalThis.Go();
  const bytes = fs.readFileSync(WASM);
  const { instance } = await WebAssembly.instantiate(bytes, go.importObject);

  // Do NOT await: Go main() registers globalThis.littlemanWasm then blocks in
  // select{} forever to keep the wasm exports alive.
  go.run(instance);

  // Poll for the API global, exactly as the editor does.
  const deadline = Date.now() + 10_000;
  while (!globalThis.littlemanWasm) {
    if (Date.now() > deadline) {
      throw new Error("wasm: littlemanWasm global never appeared (10s timeout)");
    }
    await new Promise((r) => setTimeout(r, 10));
  }
  _api = globalThis.littlemanWasm;
  return _api;
}

// Parse a wasm JSON result and surface engine errors as thrown JS errors.
function unwrap(jsonStr) {
  const v = JSON.parse(jsonStr);
  if (v && v.type === "error") {
    const e = new Error(v.message || "wasm error");
    e.pos = v.pos ?? null;
    throw e;
  }
  return v;
}

// A loaded program session.
class Session {
  constructor(api, id) {
    this.api = api;
    this.id = id;
  }
  step() {
    return unwrap(this.api.step(this.id));
  }
  stepN(count, stopOnFrame = false) {
    return unwrap(this.api.stepN(this.id, count, !!stopOnFrame));
  }
  back() {
    return unwrap(this.api.back(this.id));
  }
  close() {
    try {
      this.api.closeSession(this.id);
    } catch {
      /* ignore */
    }
  }
}

async function loadProgram(rows, input = "", expected = "", frames = "") {
  const api = await boot();
  const id = api.newSession();
  const framesJSON = frames && frames.length ? JSON.stringify(frames) : "";
  const snap = unwrap(api.load(id, rows, input, expected, framesJSON));
  return { session: new Session(api, id), snap };
}

// ── rendering ────────────────────────────────────────────────────────────
// Runner `dir` is a [dx,dy] vector in the snapshot.
function dirGlyph(dir) {
  if (Array.isArray(dir)) {
    const [dx, dy] = dir;
    if (dx > 0) return ">";
    if (dx < 0) return "<";
    if (dy > 0) return "v";
    if (dy < 0) return "^";
    return "?";
  }
  return String(dir);
}

function renderAscii(rows, snap) {
  // Overlay live runners onto a copy of the source grid.
  const grid = rows.map((r) => r.split(""));
  const runners = snap?.entities?.runners ?? [];
  for (const r of runners) {
    if (r.halted) continue;
    const [x, y] = r.pos ?? [];
    if (y == null || x == null) continue;
    if (y < 0 || y >= grid.length) continue;
    const row = grid[y];
    while (row.length <= x) row.push(" ");
    row[x] = "@";
  }
  return grid.map((r) => r.join("")).join("\n");
}

function summarize(snap) {
  const lines = [];
  const halted = snap.halted ? "true" : "false";
  lines.push(`tick ${snap.step}  halted:${halted}${snap.reason ? `  (${snap.reason})` : ""}`);
  if (snap.fatal) {
    const f = snap.fatal;
    lines.push(`FATAL ${f.reason}${f.pos ? ` at [${f.pos.join(",")}]` : ""}${f.cell != null ? ` cell='${f.cell}'` : ""}`);
  }
  const runners = snap?.entities?.runners ?? [];
  for (const r of runners) {
    const dir = dirGlyph(r.dir);
    const pos = r.pos ? `(${r.pos.join(",")})` : "?";
    lines.push(
      `runner${r.id}  A=${r.a} B=${r.b} BP=${r.backpack} dir=${dir} pos=${pos}${r.halted ? " HALTED" : ""}`
    );
  }
  const out = Array.isArray(snap.output) ? snap.output.join(" ") : "";
  lines.push(`output: ${out}`);
  return lines.join("\n");
}

// ── input resolution ───────────────────────────────────────────────────────
function readStdinSync() {
  try {
    // fd 0; returns "" if it's a TTY with no piped data.
    if (process.stdin.isTTY) return "";
    return fs.readFileSync(0, "utf8");
  } catch {
    return "";
  }
}

// Mirror the editor's input/expected normalization (`qo` in embed.js): keep only
// digits, whitespace, `-`, and `/`; space out `/` (the frame separator).
function normalizeInput(s) {
  return s.replace(/[^\d\s/-]+/g, " ").replace(/\//g, " / ");
}

function resolveInput(flagInput) {
  const raw = flagInput != null ? flagInput : readStdinSync();
  return raw.trim().length ? normalizeInput(raw) : "";
}

// ── arg parsing ────────────────────────────────────────────────────────────
function parseArgs(argv) {
  const out = {
    positional: [],
    json: false,
    input: null,
    expected: null,
    frames: null,
    maxTicks: DEFAULT_MAX_TICKS,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--json") out.json = true;
    else if (a === "--input") out.input = argv[++i] ?? "";
    else if (a.startsWith("--input=")) out.input = a.slice("--input=".length);
    else if (a === "--expected") out.expected = argv[++i] ?? "";
    else if (a.startsWith("--expected=")) out.expected = a.slice("--expected=".length);
    else if (a === "--frames") out.frames = argv[++i] ?? "";
    else if (a.startsWith("--frames=")) out.frames = a.slice("--frames=".length);
    else if (a === "--max-ticks") out.maxTicks = parseInt(argv[++i], 10);
    else if (a.startsWith("--max-ticks=")) out.maxTicks = parseInt(a.slice("--max-ticks=".length), 10);
    else out.positional.push(a);
  }
  return out;
}

function readManFile(file) {
  if (!file) throw new Error("no .man file given");
  const text = fs.readFileSync(file, "utf8");
  // Drop a single trailing newline so we don't add a phantom empty row.
  const trimmed = text.endsWith("\n") ? text.slice(0, -1) : text;
  return trimmed.split("\n");
}

// ── subcommands ────────────────────────────────────────────────────────────
async function cmdRun(opts) {
  const file = opts.positional[0];
  const rows = readManFile(file);
  const input = resolveInput(opts.input);
  const { session, snap: loadSnap } = await loadProgram(rows, input);

  // Run to completion, matching the editor's grading loop: stop when the program
  // halts, crashes, all output has settled, or a batch makes no progress (every
  // runner blocked/halted). Guard against runaways with --max-ticks.
  let snap = loadSnap;
  let ticks = snap.step ?? 0;
  while (!snap.halted && !snap.fatal && !snap.outputSettled) {
    if (ticks >= opts.maxTicks) {
      session.close();
      throw new Error(`exceeded max ticks (${opts.maxTicks}) without halting; raise with --max-ticks`);
    }
    const prev = snap.step;
    snap = session.stepN(STEP_CHUNK, false);
    if (snap.step === prev) break; // no progress → all runners blocked/halted
    ticks = snap.step ?? ticks + STEP_CHUNK;
  }
  session.close();

  if (opts.json) {
    process.stdout.write(JSON.stringify(snap, null, 2) + "\n");
  } else {
    const out = Array.isArray(snap.output) ? snap.output.join(" ") : "";
    if (out.length) process.stdout.write(out + "\n");
    // Tick count + end reason to stderr, so stdout stays pure program output.
    const why = snap.fatal ? `fatal:${snap.fatal.reason}` : snap.reason ?? (snap.outputSettled ? "output-settled" : "stopped");
    process.stderr.write(`# halted after ${snap.step} tick(s) (${why})\n`);
  }
  if (snap.fatal) {
    const f = snap.fatal;
    process.stderr.write(`fatal: ${f.reason}${f.pos ? ` at [${f.pos.join(",")}]` : ""}\n`);
    process.exitCode = 1;
  }
}

async function cmdTick(opts) {
  const file = opts.positional[0];
  const n = opts.positional[1] != null ? parseInt(opts.positional[1], 10) : 1;
  const rows = readManFile(file);
  const input = resolveInput(opts.input);
  const { session, snap: loadSnap } = await loadProgram(rows, input);

  let snap = loadSnap;
  if (n > 0 && !snap.halted && !snap.fatal) {
    snap = session.stepN(n, false);
  }
  session.close();

  if (opts.json) {
    process.stdout.write(JSON.stringify(snap, null, 2) + "\n");
  } else {
    process.stdout.write(renderAscii(rows, snap) + "\n\n" + summarize(snap) + "\n");
  }
}

// ── static analysis + judging (structural bridge for tooling) ───────────────
// These surface wasm exports the run/tick loop doesn't need but an optimizer
// does: analyze() (rooms/pipes/displays), route() (which pipe a send/recv cell
// binds to — the nearest-pipe oracle), and judge() (engine-side round-gating +
// precise settle tick, by passing `expected`/`frames` into load()).

async function cmdAnalyze(opts) {
  const rows = readManFile(opts.positional[0]);
  const api = await boot();
  const info = unwrap(api.analyze(rows));
  process.stdout.write(JSON.stringify(info, null, opts.json ? 2 : 0) + "\n");
}

async function cmdRoute(opts) {
  const rows = readManFile(opts.positional[0]);
  const x = parseInt(opts.positional[1], 10);
  const y = parseInt(opts.positional[2], 10);
  if (Number.isNaN(x) || Number.isNaN(y)) throw new Error("route needs <x> <y>");
  const api = await boot();
  const res = unwrap(api.route(rows, x, y));
  process.stdout.write(JSON.stringify(res, null, opts.json ? 2 : 0) + "\n");
}

async function cmdJudge(opts) {
  const rows = readManFile(opts.positional[0]);
  const input = resolveInput(opts.input);
  const expected = opts.expected != null ? normalizeInput(opts.expected) : "";
  const frames = opts.frames ? JSON.parse(opts.frames) : "";
  const { session, snap: loadSnap } = await loadProgram(rows, input, expected, frames);

  // Step to settle, matching cmdRun's stop condition (halt / fatal / output
  // settled / no progress), so `step` is the precise final-output tick.
  let snap = loadSnap;
  let ticks = snap.step ?? 0;
  while (!snap.halted && !snap.fatal && !snap.outputSettled) {
    if (ticks >= opts.maxTicks) break;
    const prev = snap.step;
    snap = session.stepN(STEP_CHUNK, false);
    if (snap.step === prev) break;
    ticks = snap.step ?? ticks + STEP_CHUNK;
  }
  session.close();
  process.stdout.write(JSON.stringify(snap, null, opts.json ? 2 : 0) + "\n");
}

const USAGE = `littleman runner

  lm.mjs run     <file.man> [--input "1 2 3"] [--json] [--max-ticks N]
  lm.mjs tick    <file.man> [n] [--input "1 2 3"] [--json]
  lm.mjs analyze <file.man> [--json]
  lm.mjs route   <file.man> <x> <y> [--json]
  lm.mjs judge   <file.man> [--input "…"] [--expected "…"] [--frames JSON] [--json]

run     execute to completion; print program output (space-joined integers).
tick    advance n ticks (default 1); print ASCII map + hands/backpack/output.
analyze print the structural analysis JSON (rooms, pipes, displays).
route   print the pipe cells a send/recv instruction at (x,y) binds to.
judge   run with engine-side round-gating (needs --expected); print the settle snapshot.

Flags:
  --input "…"    whitespace-separated integers for the program's input room
                 (also read from piped stdin if --input is omitted).
  --expected "…" expected output; enables engine round-gating (judge).
  --frames JSON  expected display frames as JSON (judge, display problems).
  --json         emit raw / pretty JSON.
  --max-ticks N  safety cap for run/judge (default ${DEFAULT_MAX_TICKS}).
`;

async function main() {
  const [cmd, ...rest] = process.argv.slice(2);
  const opts = parseArgs(rest);
  try {
    if (cmd === "run") await cmdRun(opts);
    else if (cmd === "tick") await cmdTick(opts);
    else if (cmd === "analyze") await cmdAnalyze(opts);
    else if (cmd === "route") await cmdRoute(opts);
    else if (cmd === "judge") await cmdJudge(opts);
    else {
      process.stdout.write(USAGE);
      process.exitCode = cmd ? 1 : 0;
    }
  } catch (e) {
    process.stderr.write(`error: ${e.message}${e.pos ? ` (pos ${JSON.stringify(e.pos)})` : ""}\n`);
    process.exitCode = 1;
  }
  // Go's runtime keeps timers alive (select{} + scheduled events); force exit —
  // but not before stdout has actually drained. `analyze` on a large grid writes
  // well past a pipe's 64 KB buffer, and exiting mid-write truncates the JSON
  // (the write is async; the callback below fires once it has reached the OS).
  await new Promise((resolve) => process.stdout.write("", resolve));
  process.exit(process.exitCode ?? 0);
}

main();
