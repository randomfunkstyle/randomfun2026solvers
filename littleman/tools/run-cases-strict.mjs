// run-cases.mjs with the extra-output check ON by default.
//
//   node run-cases-strict.mjs <file.man> <cases.json> [cap] [fine] [extraTicks]
//                             [--extra-ticks=N] [--no-strict] [--quiet]
//
// After a case's expected output is complete, the runner keeps stepping for
// `extraTicks` more ticks (default 20000 ≈ 30 memory ops) and FAILS the case if
// any further value is emitted. That is the only way to catch an off-by-one in a
// pass-through loop: it emits the right prefix, so the plain runner — which
// stops the instant the output *length* matches — passes it.
//
// Output format is identical to run-cases.mjs (`N/M passed…`, `score = …`), plus
// a `strict: +N extra ticks` note on the header line. Exit status 0 iff all
// cases pass.
//
// Proof that the check bites — `negative-controls/` holds two program pairs that
// pass the plain runner and fail this one (each with a correct twin that passes
// both, so the check is not just trigger-happy):
//
//   # emits its value, spins 5400 ticks, emits it again
//   node run-cases.mjs        negative-controls/extra-output.man \
//                             negative-controls/extra-output-cases.json 200000 100   # 2/2 pass
//   node run-cases-strict.mjs negative-controls/extra-output.man \
//                             negative-controls/extra-output-cases.json 200000 100   # 0/2
//   node run-cases-strict.mjs negative-controls/extra-output-clean.man …             # 2/2
//
//   # the real shape: lap-ring with the sentinel removed and the caller dividing
//   # by 4 while the ring still moves 8 values a lap (fine=1, as blocks are run)
//   node run-cases.mjs        negative-controls/lap-ring-overmove.man \
//                             negative-controls/lap-ring-nosentinel-cases.json 200000 1  # 12/12
//   node run-cases-strict.mjs negative-controls/lap-ring-overmove.man …                  # 2/12
//   node run-cases-strict.mjs negative-controls/lap-ring-nosentinel.man …                # 12/12
import { boot, parseArgs, runSuite, DEFAULT_EXTRA_TICKS } from "./run-cases-lib.mjs";

const opts = parseArgs(process.argv.slice(2), { defaultExtraTicks: DEFAULT_EXTRA_TICKS });
if (!opts.program || !opts.cases) {
  console.error(
    "usage: run-cases-strict.mjs <file.man> <cases.json> [cap] [fine] [extraTicks] [--extra-ticks=N]",
  );
  process.exit(2);
}
const api = await boot();
const res = runSuite(api, opts.program, opts.cases, opts);
process.exit(res.pass === res.total ? 0 : 1);
