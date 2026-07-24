// For every pipe instruction in a program, report which pipe the engine routes it to.
import fs from "node:fs"; import vm from "node:vm"; import path from "node:path";
const LM="/Users/romanmishchenko/icfp/randomfun2026solvers/littleman";
vm.runInThisContext(fs.readFileSync(path.join(LM,"wasm_exec.js"),"utf8"));
const go=new globalThis.Go();
const mod=await WebAssembly.instantiate(fs.readFileSync(path.join(LM,"littleman.wasm")),go.importObject);
go.run(mod.instance); while(!globalThis.littlemanWasm) await new Promise(s=>setTimeout(s,30));
const api=globalThis.littlemanWasm;
const rows=fs.readFileSync(process.argv[2],"utf8").replace(/\n$/,"").split("\n");
const an=JSON.parse(api.analyze(rows));
console.log("pipes:");
an.pipes.forEach((p,i)=>console.log(`  ${i}: ${p.path.length} cells  ${JSON.stringify(p.path[0].pos)} -> ${JSON.stringify(p.path[p.path.length-1].pos)}`));
const key=(c)=>JSON.stringify(c);
const pipeOf=new Map();
an.pipes.forEach((p,i)=>p.path.forEach(c=>pipeOf.set(key(c.pos),i)));
for(let y=0;y<rows.length;y++) for(let x=0;x<rows[y].length;x++){
  const ch=rows[y][x];
  if(!"rsSRUq".includes(ch)) continue;
  const res=JSON.parse(api.route(rows,x,y));
  let tgt="?";
  if(res.type==="error") tgt="ERR "+res.message;
  else {
    const cells=res.cells||res.path||[];
    const ids=[...new Set(cells.map(c=>pipeOf.get(key(c.pos??c))).filter(v=>v!==undefined))];
    tgt=JSON.stringify(res).length>200? `pipes=${ids}` : JSON.stringify(res);
  }
  console.log(`  '${ch}' at (${x},${y})  ->  ${tgt}`);
}
