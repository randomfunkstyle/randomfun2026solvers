import fs from "node:fs"; import vm from "node:vm"; import path from "node:path";
const LM="/Users/romanmishchenko/icfp/randomfun2026solvers/littleman";
vm.runInThisContext(fs.readFileSync(path.join(LM,"wasm_exec.js"),"utf8"));
const go=new globalThis.Go();
const mod=await WebAssembly.instantiate(fs.readFileSync(path.join(LM,"littleman.wasm")),go.importObject);
go.run(mod.instance); while(!globalThis.littlemanWasm) await new Promise(s=>setTimeout(s,30));
const api=globalThis.littlemanWasm;
const rows=fs.readFileSync(process.argv[2],"utf8").replace(/\n$/,"").split("\n");
const input=process.argv[3]??"";
const total=+(process.argv[4]??100000), every=+(process.argv[5]??2000);
const id=api.newSession();
let s=JSON.parse(api.load(id,rows,input,"",""));
const cell=(r)=>rows[r.pos[1]]?.[r.pos[0]]??"?";
let t=0, last="";
while(t<total){
  const w=s.entities.runners[0];
  const line=`(${w.pos})'${cell(w)}' A=${w.a} B=${w.b} BP=${w.backpack}`;
  if(t%every===0 || line===last) {
    const fill=(s.entities.pipes||[]).map(p=>(p.values||[]).length).join("/");
    console.log(`t${String(t).padStart(7)} ${line}  pipes=${fill} in:${s.inputRead??"?"} out=${JSON.stringify(s.output||[])}`);
  }
  last=line;
  s=JSON.parse(api.stepN(id,every,false)); t+=every;
  if(s.halted){ console.log(`HALTED ${s.reason} at ~${t}`); break; }
}
