const fs=require('node:fs');
const core=require('../../plugins/bobbin/dist');
const {contracts}=require('../../plugins/bobbin/dist/common');
const {draftCapture}=require('../../plugins/bobbin/dist/owners');
const {operation}=require('../node/helpers.cjs');
const {canonicalTerms}=require('../../plugins/bobbin/dist/decision');
const requests=JSON.parse(fs.readFileSync(0,'utf8'));
const results=requests.map(request=>{
 try{
  switch(request.op){
   case 'canonical':return {ok:true,json:core.canonicalJson(request.value),digest:core.canonicalDigest(request.value)};
   case 'parse':return {ok:true,document:core.parseDocument(request.content,request.descriptor)};
   case 'render':return {ok:true,content:core.renderDocument(request.frontmatter,request.sections,request.descriptor)};
   case 'fixture':{const capture=operation(request.kind);return {ok:true,...capture,...draftCapture(capture.candidate,capture.attestation,{id:request.id,now:request.now})};}
   case 'fold':return {ok:true,value:core.normalizedKey(request.value)};
   case 'terms':return {ok:true,value:canonicalTerms(request.value)};
   case 'unicode-maps':{const {nfc}=require('../../plugins/bobbin/dist/common'),nfcMap={},foldMap={};for(let i=0;i<=0x10ffff;i++){if(i>=0xd800&&i<=0xdfff)continue;const s=String.fromCodePoint(i),n=nfc(s),f=core.normalizedKey(s);if(n!==s)nfcMap[i]=n;if(f!==s)foldMap[i]=f;}return{nfc:nfcMap,fold:foldMap};}
   default:throw new Error('Unknown comparison operation');
  }
 }catch(error){return {ok:false,code:error.code??error.name,message:error.message};}
});
process.stdout.write(JSON.stringify(results));
