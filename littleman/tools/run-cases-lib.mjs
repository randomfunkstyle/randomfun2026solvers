// Shared engine + suite-runner used by run-cases.mjs, run-cases-strict.mjs and
// predict-score.mjs.
//
// A case passes when the program emits exactly the expected values in order.
// The program need not halt, so we stop at the first tick where the output is
// complete (that tick count is what the contest scores) — and then, if an
// extra-tick budget is given, we KEEP STEPPING for that many ticks and fail the
// case if any further value shows up. Without that second phase a loop that
// moves one value too many passes silently: the extra value is emitted after
// the length check. See littleman/programs/blocks/lap-ring.md, trap 6.
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const LM = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export const DEFAULT_CAP = 2_000_000;
export const DEFAULT_FINE = 200;
// ~30 memory ops at ~700 ticks/op: long enough for a trailing WRITE-only tail
// plus a spurious emit, short enough to cost nothing measurable.
export const DEFAULT_EXTRA_TICKS = 20_000;

let _api = null;

/** Boot the reference wasm engine once per process. */
export async function boot() {
  if (_api) return _api;
  if (typeof globalThis.Go === "undefined") {
    vm.runInThisContext(fs.readFileSync(path.join(LM, "wasm_exec.js"), "utf8"));
  }
  const go = new globalThis.Go();
  const mod = await WebAssembly.instantiate(
    fs.readFileSync(path.join(LM, "littleman.wasm")),
    go.importObject,
  );
  go.run(mod.instance);
  while (!globalThis.littlemanWasm) await new Promise((s) => setTimeout(s, 30));
  _api = globalThis.littlemanWasm;
  return _api;
}

/** Grid rows exactly as lm.mjs reads them (drop one trailing newline). */
export function readProgram(file) {
  const rows = fs.readFileSync(file, "utf8").replace(/\n$/, "").split("\n");
  const w = Math.max(...rows.map((r) => r.length));
  return { rows, w, h: rows.length, area2: Math.max(w, rows.length) ** 2 };
}

export function readCases(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function wanted(c) {
  return c.out.trim() === "" ? [] : c.out.trim().split(/\s+/).map(Number);
}

/**
 * Run one case. Returns {ok, ticks, bad, output}.
 *   ticks = first tick (rounded up to `fine`) at which the output was complete.
 *   bad   = failure reason, including "extra output" from the strict phase.
 */
export function runCase(api, rows, c, { cap = DEFAULT_CAP, fine = DEFAULT_FINE, extraTicks = 0 } = {}) {
  const id = api.newSession();
  try {
    let s = JSON.parse(api.load(id, rows, c.in, "", ""));
    if (s.type === "error") return { ok: false, ticks: 0, bad: `load: ${s.message}`, output: [] };
    const want = wanted(c);
    let t = 0;
    let ok = false;
    let bad = null;

    // phase 1 — step until the expected output is complete
    while (t < cap) {
      const out = s.output || [];
      for (let i = 0; i < Math.min(out.length, want.length); i++) {
        if (out[i] !== want[i]) {
          bad = `wrong at ${i}: got ${out[i]} want ${want[i]}`;
          break;
        }
      }
      if (bad) break;
      if (out.length > want.length) {
        bad = `extra output: emitted ${out.length} values, want ${want.length} (first extra ${out[want.length]})`;
        break;
      }
      if (out.length === want.length) {
        ok = true;
        break;
      }
      if (s.halted) {
        bad = `halted (${s.reason}) with ${out.length}/${want.length} values`;
        break;
      }
      const step = Math.min(fine, cap - t);
      s = JSON.parse(api.stepN(id, step, false));
      t += step;
      if (s.type === "error") {
        bad = "engine: " + s.message;
        break;
      }
    }
    if (!ok && !bad) bad = "tick cap";

    // phase 2 — the output is complete; keep stepping and demand silence.
    // A halt or an engine fault here is NOT a failure: the judge stops at the
    // first correct output too, so anything after that point is out of scope
    // except for further emitted values.
    if (ok && extraTicks > 0) {
      let extra = 0;
      while (extra < extraTicks) {
        const step = Math.min(fine, extraTicks - extra);
        const next = JSON.parse(api.stepN(id, step, false));
        if (next.type === "error") break;
        const before = s.step;
        s = next;
        extra += step;
        const out = s.output || [];
        if (out.length > want.length) {
          ok = false;
          bad =
            `extra output after +${extra} tick(s): emitted ${out.length} values, ` +
            `want ${want.length} (first extra ${out[want.length]})`;
          break;
        }
        // Every man halted and the clock no longer advances → nothing left that
        // could emit. (One chunk is always taken after the halt so a value still
        // draining down the output pipe is still seen.)
        if (s.halted && s.step === before) break;
      }
    }

    return { ok, ticks: t, bad, output: s.output || [] };
  } finally {
    api.closeSession(id);
  }
}

/**
 * Run a whole suite, printing the classic run-cases.mjs report.
 * Returns {pass, total, ticks[], avg, w, h, area2}.
 */
export function runSuite(api, programFile, casesFile, opts = {}) {
  const { cap = DEFAULT_CAP, fine = DEFAULT_FINE, extraTicks = 0, quiet = false } = opts;
  const { rows, w, h, area2 } = readProgram(programFile);
  const cases = readCases(casesFile);
  const say = quiet ? () => {} : (m) => console.log(m);

  say(
    `${programFile}: ${w}x${h}  footprint area2=${area2}` +
      (extraTicks > 0 ? `  strict: +${extraTicks} extra ticks` : ""),
  );

  let pass = 0;
  const ticks = [];
  for (const c of cases) {
    const r = runCase(api, rows, c, { cap, fine, extraTicks });
    if (r.ok) {
      pass++;
      ticks.push(r.ticks);
      say(`  ok    "${c.in}" -> ${c.out}   (<=${r.ticks} ticks)`);
    } else if (r.bad && r.bad.startsWith("load: ")) {
      say(`  FAIL(load) ${c.in}: ${r.bad.slice(6)}`);
    } else {
      say(`  FAIL  "${c.in}" want [${wanted(c)}] got [${r.output}]  ${r.bad ?? "tick cap"}`);
    }
  }

  const avg = ticks.length ? ticks.reduce((a, b) => a + b, 0) / ticks.length : 0;
  say(
    `${pass}/${cases.length} passed` +
      (ticks.length ? `, ticks max ${Math.max(...ticks)} avg ${Math.round(avg)}` : ""),
  );
  if (pass === cases.length) {
    say(
      `score = area2 ${area2} x avgTicks ${Math.round(avg)} = ` +
        `${(area2 * avg).toLocaleString("en-US", { maximumFractionDigits: 0 })}`,
    );
  }
  return { pass, total: cases.length, ticks, avg, w, h, area2 };
}

/**
 * Positional-compatible CLI parsing:
 *   <file.man> <cases.json> [cap] [fine] [extraTicks]  plus
 *   --extra-ticks=N | --extra-ticks N | --strict[=N] | --no-strict | --quiet
 */
export function parseArgs(argv, { defaultExtraTicks = 0 } = {}) {
  const pos = [];
  let extraTicks = null;
  let quiet = false;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--extra-ticks") extraTicks = +argv[++i];
    else if (a.startsWith("--extra-ticks=")) extraTicks = +a.slice("--extra-ticks=".length);
    else if (a === "--strict") extraTicks = DEFAULT_EXTRA_TICKS;
    else if (a.startsWith("--strict=")) extraTicks = +a.slice("--strict=".length);
    else if (a === "--no-strict") extraTicks = 0;
    else if (a === "--quiet") quiet = true;
    else pos.push(a);
  }
  return {
    program: pos[0],
    cases: pos[1],
    cap: +(pos[2] ?? DEFAULT_CAP),
    fine: +(pos[3] ?? DEFAULT_FINE),
    extraTicks: extraTicks ?? +(pos[4] ?? defaultExtraTicks),
    quiet,
  };
}
