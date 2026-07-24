// Compact tick tracer: runners (pos/dir/A/B/BP) + pipe contents + output.
import fs from "node:fs"; import vm from "node:vm"; import path from "node:path";
import { fileURLToPath } from "node:url";
const LM = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
vm.runInThisContext(fs.readFileSync(path.join(LM,"wasm_exec.js"),"utf8"));
const go=new globalThis.Go();
const mod=await WebAssembly.instantiate(fs.readFileSync(path.join(LM,"littleman.wasm")),go.importObject);
go.run(mod.instance); while(!globalThis.littlemanWasm) await new Promise(s=>setTimeout(s,30));
const api=globalThis.littlemanWasm;
const D={"1,0":">","-1,0":"<","0,1":"v","0,-1":"^"};

const file=process.argv[2];
const input=process.argv[3]??"";
const maxT=+(process.argv[4]??40);
const from=+(process.argv[5]??0);
const rows=fs.readFileSync(file,"utf8").replace(/\n$/,"").split("\n");
const id=api.newSession();
let s=JSON.parse(api.load(id,rows,input,"",""));
if(s.type==="error"){ console.log("LOAD ERROR: "+s.message); process.exit(1); }
const an=JSON.parse(api.analyze(rows));
console.log(`rooms=${an.rooms.length} pipes=${an.pipes.length} displays=${an.displays.length}`);
an.pipes.forEach((p,i)=>console.log(`  pipe${i}: ${p.path.length} cells  ${JSON.stringify(p.path[0])} -> ${JSON.stringify(p.path[p.path.length-1])}`));
const cell=(r)=>rows[r.pos[1]]?.[r.pos[0]] ?? "?";
for(let t=0;t<=maxT;t++){
  if(t>=from){
    const men=(s.entities?.runners||[]).map(r=>`(${r.pos})'${cell(r)}'${D[r.dir.join(",")]||"?"} A=${r.a} B=${r.b} BP=${r.backpack}${r.halted?" HALT":""}`).join("  ");
    const pipes=(s.entities?.pipes||[]).map((p,i)=>`p${i}[${(p.values||[]).map(v=>v.value ?? v).join(",")}]`).join(" ");
    console.log(`t${String(t).padStart(4)} ${men} | ${pipes} | out=${JSON.stringify(s.output||[])}${s.halted?` HALTED:${s.reason}`:""}${s.fatal?" FATAL "+JSON.stringify(s.fatal):""}`);
  }
  if(s.halted) break;
  s=JSON.parse(api.stepN(id,1,false));
  if(s.type==="error"){ console.log("ERR "+s.message); break; }
}
