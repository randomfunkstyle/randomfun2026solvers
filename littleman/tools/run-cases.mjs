// Run a .man against (input, expected) cases. Passing = emitting the expected
// output in order; the program need not halt, so we stop at the first tick where
// output is complete (that tick count is what the contest scores).
//
//   node run-cases.mjs <file.man> <cases.json> [cap] [fine] [extraTicks]
//                      [--strict[=TICKS]] [--extra-ticks=TICKS] [--quiet]
//
// Stopping at "output complete" makes the plain run blind to a program that
// emits one value TOO MANY (right prefix, extra value a few hundred ticks
// later). Pass --strict (or an extra-tick budget) to keep stepping after the
// output is complete and FAIL the case if anything more is emitted. Default is
// 0 extra ticks, i.e. exactly the historical behaviour; run-cases-strict.mjs is
// the same tool with strict on by default.
import { boot, parseArgs, runSuite } from "./run-cases-lib.mjs";

const opts = parseArgs(process.argv.slice(2), { defaultExtraTicks: 0 });
if (!opts.program || !opts.cases) {
  console.error("usage: run-cases.mjs <file.man> <cases.json> [cap] [fine] [extraTicks] [--strict[=N]]");
  process.exit(2);
}
const api = await boot();
const res = runSuite(api, opts.program, opts.cases, opts);
process.exit(res.pass === res.total ? 0 : 1);
