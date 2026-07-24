; history-lesson — GENERATED, do not hand-edit.
; Regenerate with:
;   from randomfun2026solvers.lm1.programs import PROGRAM_DIR, history_lesson_source
;   (PROGRAM_DIR / "history-lesson.asm").write_text(history_lesson_source())
;
; No input, fixed output: the whole program is a ROM walk. `.ascii` expands
; to `LDI c` / `OUT` per byte, so P = 3 * 2810 + 1 = 8431 words. Footprint-
; scored, so the tick count does not matter — but a 8431-word ROM does, and
; that is the honest cost of solving this on a general-purpose CPU.

.ascii "1996: Philadelphia, PA, USA \"Optimality and inefficiency: What isn't a cost model of the lambda calculus?\" (Julia Lawall and Harry Mairson); "
.ascii "1997: Amsterdam, Netherlands \"Functional reactive animation\" (Conal Elliott and Paul Hudak); "
.ascii "1998: Baltimore, MD, USA \"Cayenne - a language with dependent types\" (Lennart Augustsson); "
.ascii "1999: Paris, France \"Haskell and XML: Generic combinators or type-based translation?\" (Malcolm Wallace and Colin Runciman); "
.ascii "2000: Montreal, Canada \"QuickCheck: a lightweight tool for random testing of Haskell programs\" (Koen Claessen and John Hughes); "
.ascii "2001: Florence, Italy \"Recursive Structures for Standard ML\" (Claudio Russo); "
.ascii "2002: Pittsburgh, PA, USA \"Contracts for higher-order functions\" (Robert Findler and Matthias Felleisen); "
.ascii "2003: Uppsala, Sweden \"MLF: Raising ML to the Power of System F\" (Didier Le Botlan and Didier Remy); "
.ascii "2004: Snowbird, UT, USA \"Scrap More Boilerplate: Reflection, Zips, and Generalised Casts\" (Ralf Lammel and Simon Peyton Jones); "
.ascii "2005: Tallinn, Estonia \"Associated Type Synonyms\" (Manuel M. T. Chakravarty, Gabriele Keller, and Simon Peyton Jones); "
.ascii "2006: Portland, OR, USA \"Simple unification-based type inference for GADTs\" (Simon Peyton Jones, Dimitrios Vytiniotis, Stephanie Weirich, and Geoffrey Washburn); "
.ascii "2007: Freiburg, Germany \"Ott: Effective Tool Support for the Working Semanticist\" (Peter Sewell, Francesco Zappa Nardelli, Scott Owens, Gilles Peskine, Thomas Ridge, Susmit Sarkar, and Rok Strnisa); "
.ascii "2008: Victoria, BC, Canada \"Parametric higher-order abstract syntax for mechanized semantics\" (Adam Chlipala); "
.ascii "2009: Edinburgh, UK \"Runtime Support for Multicore Haskell\" (Simon Marlow, Simon Peyton Jones, and Satnam Singh); "
.ascii "2010: Baltimore, MD, USA \"Abstracting abstract machines\" (David Van Horn and Matthew Might); "
.ascii "2011: Tokyo, Japan \"Frenetic: a network programming language\" (Nate Foster, Rob Harrison, Michael Freedman, Christopher Monsanto, Jennifer Rexford, Alex Story, and David Walker); "
.ascii "2012: Copenhagen, Denmark \"Addressing covert termination and timing channels in concurrent information flow systems\" (Deian Stefan, Alejandro Russo, Pablo Buiras, Amit Levy, John C. Mitchell and David Mazieres); "
.ascii "2013: Boston, MA, USA \"Handlers in Action\" (Ohad Kammar, Sam Lindley, and Nicolas Oury); "
.ascii "2014: Gothenburg, Sweden \"Refinement Types for Haskell\" (Niki Vazou, Eric L. Seidel, Ranjit Jhala, Dimitrios Vytiniotis, and Simon Peyton-Jones); "
.ascii "2015: Vancouver, BC, Canada \"1ML - core and modules united (F-ing first-class modules)\" (Andreas Rossberg); "
.ascii "2016: Nara, Japan; "
.ascii "2017: Oxford, UK; "
.ascii "2018: St. Louis, MO, USA; "
.ascii "2019: Berlin, Germany; "
.ascii "2020: Jersey City, NJ, USA (virtual); "
.ascii "2021: Daejeon, South Korea (virtual); "
.ascii "2022: Ljubljana, Slovenia; "
.ascii "2023: Seattle, WA, USA; "
.ascii "2024: Milan, Italy; "
.ascii "2025: Singapore, Singapore; "
.ascii "2026: Indianapolis, IN, USA"

HALT
