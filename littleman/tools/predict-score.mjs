// Predict the judge score for a .man before submitting it.
//
//   node predict-score.mjs <file.man> [cases.json] [cap] [fine]
//                          [--extra-ticks=N] [--no-strict] [--verbose]
//
// Prints width, height, area2, the average ticks over the heavy suite (default
// littleman/programs/memory-heavy-cases.json) and
//
//     judge ≈ area2 × 0.328 × heavy_avg_ticks
//
// The 0.328 factor is the calibrated ratio between the judge's average ticks and
// the local heavy-suite average; it held to ~1% across four real submissions
// (827M / 158M / 92.0M / 61.9M — table in littleman/programs/README.md).
//
// Cases are run with the extra-output check ON, so a program that emits one
// value too many is reported as failing rather than quietly scored.
import path from "node:path";
import { fileURLToPath } from "node:url";
import { boot, parseArgs, runSuite, readProgram, DEFAULT_EXTRA_TICKS } from "./run-cases-lib.mjs";

const TOOLS = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_CASES = path.join(TOOLS, "..", "programs", "memory-heavy-cases.json");
// judge avg ticks / local heavy-suite avg ticks, calibrated on real submissions.
const JUDGE_FACTOR = 0.328;

const argv = process.argv.slice(2);
const verbose = argv.includes("--verbose");
const opts = parseArgs(
  argv.filter((a) => a !== "--verbose"),
  { defaultExtraTicks: DEFAULT_EXTRA_TICKS },
);
if (!opts.program) {
  console.error("usage: predict-score.mjs <file.man> [cases.json] [cap] [fine] [--extra-ticks=N]");
  process.exit(2);
}
const casesFile = opts.cases ?? DEFAULT_CASES;
// The heavy suite needs a roomy cap; keep the positional default sane for it.
const cap = opts.cap === 2_000_000 ? 4_000_000 : opts.cap;

const { w, h, area2 } = readProgram(opts.program);
const api = await boot();
const res = runSuite(api, opts.program, casesFile, {
  ...opts,
  cap,
  quiet: !verbose,
});

const fmt = (n) => n.toLocaleString("en-US", { maximumFractionDigits: 0 });
console.log(`program      ${opts.program}`);
const shownSuite = path.relative(process.cwd(), casesFile) || casesFile;
console.log(`suite        ${shownSuite}  (${res.pass}/${res.total} passed, strict +${opts.extraTicks} ticks)`);
console.log(`width        ${w}`);
console.log(`height       ${h}`);
console.log(`area2        ${area2}   (max(w,h)^2)`);
if (res.pass !== res.total) {
  console.log("");
  console.log("FAIL: not every case passes — rerun with --verbose; score below is meaningless.");
}
console.log(`heavy avg    ${fmt(res.avg)} ticks   (max ${res.ticks.length ? fmt(Math.max(...res.ticks)) : "-"})`);
console.log(`local score  ${fmt(area2 * res.avg)}   (area2 x heavy avg)`);
console.log(`judge (pred) ${fmt(area2 * JUDGE_FACTOR * res.avg)}   (area2 x ${JUDGE_FACTOR} x heavy avg)`);
process.exit(res.pass === res.total ? 0 : 1);
