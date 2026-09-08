const fs=require('node:fs');
const api=require('../../plugins/bobbin/dist');
const req=JSON.parse(fs.readFileSync(0,'utf8'));
const b=api.createBobbin({vault:req.vault});
(async()=>{
 let result;
 switch(req.action){
  case 'init':result=await b.initialize({features:['decision','assumption','term','intent','document']});break;
  case 'preview':result=await b.preview(req.operation);break;
  case 'apply':result=await b.apply(req.preview,{source:'user'});break;
  case 'record':{const preview=await b.preview(req.operation);await b.apply(preview,{source:'user'});result=preview;break;}
  case 'sameClaim':{const input=await b.prepareSameClaim(req.id,req.successor);result=api.createAttestation(input,[{name:'same_semantic_claim',value:true,evidence_pointers:['/predecessor/primary_claim','/successor/primary_claim']}],'same_claim');break;}
  case 'check':result=await b.checkDecision(req.options);break;
  case 'read':result=await b.read(req.id);break;
  case 'recall':result=await b.recall(req.options);break;
 }
 process.stdout.write(JSON.stringify(result));
})().catch(e=>{console.error(e);process.exitCode=1;});
