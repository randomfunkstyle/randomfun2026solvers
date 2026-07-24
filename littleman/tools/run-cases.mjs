// Run a .man against (input, expected) cases. Passing = emitting the expected
// output in order; the program need not halt, so we stop at the first tick where
// output is complete (that tick count is what the contest scores).
import fs from "node:fs"; import vm from "node:vm"; import path from "node:path";
const LM="/Users/romanmishchenko/icfp/randomfun2026solvers/littleman";
vm.runInThisContext(fs.readFileSync(path.join(LM,"wasm_exec.js"),"utf8"));
const go=new globalThis.Go();
const mod=await WebAssembly.instantiate(fs.readFileSync(path.join(LM,"littleman.wasm")),go.importObject);
go.run(mod.instance); while(!globalThis.littlemanWasm) await new Promise(s=>setTimeout(s,30));
const api=globalThis.littlemanWasm;

const file=process.argv[2];
const cases=JSON.parse(fs.readFileSync(process.argv[3],"utf8"));
const CAP=+(process.argv[4]??2_000_000);
const FINE=+(process.argv[5]??200);   // step granularity == tick-count precision
const rows=fs.readFileSync(file,"utf8").replace(/\n$/,"").split("\n");
const w=Math.max(...rows.map(r=>r.length)), h=rows.length;
console.log(`${file}: ${w}x${h}  footprint area2=${Math.max(w,h)**2}`);
let pass=0, ticks=[];
for(const c of cases){
  const id=api.newSession();
  let s=JSON.parse(api.load(id,rows,c.in,"",""));
  if(s.type==="error"){ console.log(`  FAIL(load) ${c.in}: ${s.message}`); continue; }
  const want=c.out.trim()===""?[]:c.out.trim().split(/\s+/).map(Number);
  let t=0, ok=false, bad=null;
  while(t<CAP){
    const out=s.output||[];
    for(let i=0;i<out.length;i++) if(out[i]!==want[i]) { bad=`wrong at ${i}: got ${out[i]} want ${want[i]}`; break; }
    if(bad) break;
    if(out.length===want.length){ ok=true; break; }
    if(s.halted){ bad=`halted (${s.reason}) with ${out.length}/${want.length} values`; break; }
    const step=Math.min(FINE,CAP-t);
    s=JSON.parse(api.stepN(id,step,false)); t+=step;
    if(s.type==="error"){ bad="engine: "+s.message; break; }
  }
  api.closeSession(id);
  if(ok){ pass++; ticks.push(t); console.log(`  ok    "${c.in}" -> ${c.out}   (<=${t} ticks)`); }
  else console.log(`  FAIL  "${c.in}" want [${want}] got [${s.output||[]}]  ${bad??"tick cap"}`);
}
const avg=ticks.length?ticks.reduce((a,b)=>a+b,0)/ticks.length:0;
const area2=Math.max(w,h)**2;
console.log(`${pass}/${cases.length} passed` + (ticks.length?`, ticks max ${Math.max(...ticks)} avg ${Math.round(avg)}`:""));
if(pass===cases.length) console.log(`score = area2 ${area2} x avgTicks ${Math.round(avg)} = ${(area2*avg).toLocaleString("en-US",{maximumFractionDigits:0})}`);
