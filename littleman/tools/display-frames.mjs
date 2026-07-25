// Collect the frames a display-judged .man commits, on the reference engine.
//
// `run-cases.mjs` grades on program *output*, which a display problem never emits
// (SPEC.md: emitting any is an error). Judging there is a streaming compare of
// committed frames instead, so this steps with the wasm's `stopOnFrame` flag and
// snapshots the display's front buffer at every SWAP.
//
// Expected frames are passed into `load` alongside the input, so the engine does
// its own round gating: round N+1's input stays withheld until round N's frame is
// committed, exactly as the judge does it (GRADING.md § Rounds).
//
//   display-frames.mjs <file.man> <cases.json> [maxTicks] [chunk]
//
// `cases.json` is a problem JSON (its `publicTestData` is used) or a bare list of
// cases. Prints one JSON object per invocation:
//
//   {"width":W,"height":H,"cases":[{"name":…,"frames":[["00ff…",…],…],
//                                   "ticks":[…],"output":[…],"fatal":…}]}
//
// Frames are rows of hex digits, one character per pixel — the shape the problem
// JSONs use, so a caller can compare them verbatim.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const LM = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
vm.runInThisContext(fs.readFileSync(path.join(LM, "wasm_exec.js"), "utf8"));
const go = new globalThis.Go();
const mod = await WebAssembly.instantiate(fs.readFileSync(path.join(LM, "littleman.wasm")), go.importObject);
go.run(mod.instance);
while (!globalThis.littlemanWasm) await new Promise((s) => setTimeout(s, 30));
const api = globalThis.littlemanWasm;

const file = process.argv[2];
const problem = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const CAP = +(process.argv[4] ?? 5_000_000);
const CHUNK = +(process.argv[5] ?? 2000);

const text = fs.readFileSync(file, "utf8");
const rows = (text.endsWith("\n") ? text.slice(0, -1) : text).split("\n");
const cases = problem.publicTestData ?? problem;

// Input rounds are `/`-separated, matching the editor. `framesJson` is grouped by
// round instead — the wasm wants [][][]string, i.e. round -> frame -> row — which
// is what lets it gate round N+1's input on round N's commits.
const roundsOf = (c) => c.rounds ?? [c];
const inputOf = (c) => roundsOf(c).map((r) => (r.in ?? []).join(" ")).join(" / ");
const byRound = (c) => roundsOf(c).map((r) => r.frames ?? []);
const wantFrames = (c) => byRound(c).flat();

function frontRows(disp) {
  const out = [];
  for (let y = 0; y < disp.h; y++) {
    let line = "";
    for (let x = 0; x < disp.w; x++) line += (disp.front[y * disp.w + x] ?? 0).toString(16);
    out.push(line);
  }
  return out;
}

const results = [];
for (const c of cases) {
  const want = wantFrames(c);
  const id = api.newSession();
  let s = JSON.parse(api.load(id, rows, inputOf(c), "", want.length ? JSON.stringify(byRound(c)) : ""));
  const rec = { name: c.name ?? "?", frames: [], ticks: [], output: [], fatal: null };
  if (s.type === "error") {
    rec.fatal = `load: ${s.message}`;
    results.push(rec);
    api.closeSession(id);
    continue;
  }
  let t = s.step ?? 0;
  while (t < CAP && rec.frames.length < want.length) {
    const prev = t;
    s = JSON.parse(api.stepN(id, Math.min(CHUNK, CAP - t), true));
    if (s.type === "error") {
      rec.fatal = `engine: ${s.message}`;
      break;
    }
    t = s.step ?? prev;
    if (s.fatal) {
      rec.fatal = `${s.fatal.reason}${s.fatal.pos ? ` at [${s.fatal.pos.join(",")}]` : ""}`;
      break;
    }
    const disp = (s.entities?.displays ?? [])[0];
    if (s.frameCommitted && disp) {
      rec.frames.push(frontRows(disp));
      rec.ticks.push(t);
    } else if (t === prev) {
      rec.fatal = s.halted ? `halted (${s.reason ?? "?"})` : "no progress (every man blocked)";
      break;
    }
  }
  if (!rec.fatal && rec.frames.length < want.length) rec.fatal = `tick cap at ${t}`;
  rec.output = s.output ?? [];
  const disp = (s.entities?.displays ?? [])[0];
  rec.width = disp?.w ?? 0;
  rec.height = disp?.h ?? 0;
  api.closeSession(id);
  results.push(rec);
}

process.stdout.write(JSON.stringify({ cases: results }) + "\n");
process.exit(0);
