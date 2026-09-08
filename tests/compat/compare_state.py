"""Compare stateful Node operations with the frozen Python implementation."""
from pathlib import Path
import inspect, json, subprocess, tempfile, uuid, unicodedata
from compare import ROOT, module, node

def scenario(vault, action, **kwargs):
    result = subprocess.run(['node', str(ROOT/'tests/compat/scenario.cjs')], input=json.dumps({'vault':str(vault),'action':action,**kwargs},ensure_ascii=False), capture_output=True,text=True)
    if result.returncode: raise AssertionError(result.stderr)
    return json.loads(result.stdout)

def draft_map(result, core, descriptor=None):
    values=result.get('artifact_drafts',result.get('bundle',{}).get('materials',[]))
    out={}
    for value in values:
        content=value.get('content','')
        if not content.startswith('---\n') or '\nid: "ctx_' not in content: continue
        document=core.parse_document(content,descriptor)
        out[value['path']]=(document.frontmatter,document.sections)
    return out

def main():
    c=module('context'); owners={k:module(k) for k in ('decision','assumption','term','intent','document')}
    kinds=['snapshot','observation','archive',*owners]
    passed=0
    with tempfile.TemporaryDirectory(prefix='bobbin-parity-') as temp:
        vault=Path(temp);scenario(vault,'init')
        fixtures=node([{'op':'fixture','kind':k,'id':'ctx_'+uuid.uuid4().hex,'now':'2026-09-08T10:00:00+09:00'} for k in kinds])
        ids={}
        for kind,fixture in zip(kinds,fixtures,strict=True):
            op={'action':'capture','candidate':fixture['candidate'],'attestation':fixture['attestation'],'id':fixture['document']['frontmatter']['id'],'now':'2026-09-08T10:00:00+09:00'}
            scenario(vault,'record',operation=op);ids[kind]=op['id']
        def check_indexes():
            nonlocal passed
            for kind in kinds:
                expected=c.render_area_index_from_repository(vault,kind)
                actual=(vault/f'context/{kind}/{kind}.index.md').read_text()
                assert expected==actual,(kind,'index byte mismatch')
                passed+=1
        check_indexes()
        for options in ({'statement':'파일 저장소를 사용한다.'},{'statement':'파일 저장소를 사용한다.','scope':'consumer','decisionKey':'storage'}):
            original={('decision_key' if k=='decisionKey' else k):v for k,v in options.items()}
            expected=owners['decision'].prepare_decision_check(vault,**original)
            actual=scenario(vault,'check',options=options)
            for field in ('coverage','comparison_input','input_digest','deterministic','retrieval'):
                assert expected[field]==actual[field],('decision check',field,expected[field],actual[field])
                passed+=1
        at='2026-09-09T12:34:56+09:00'
        def compare(kind,operation,result):
            nonlocal passed
            operation={'id':ids[kind],'now':at,**operation}
            descriptor=owners[kind].build_init_plan()['owner_descriptor'] if kind in owners else None
            preview=scenario(vault,'preview',operation=operation)
            expected=draft_map(result,c,descriptor)
            actual={item['path']:(parsed.frontmatter,parsed.sections) for item in preview['changes'] if item['content'] is not None for parsed in [c.parse_document(item['content'],descriptor)]}
            if kind=='assumption' and operation['action']=='supersede':
                # Python omitted the successor candidate's optional common metadata.
                # The port preserves exactly what the caller supplied; no old record changes.
                for relative,(fm,sections) in expected.items():
                    if fm['id']==operation['successor']['id']:
                        for field in ('tags','search_terms','source_refs'):
                            value=operation['successor']['candidate'].get(field)
                            if value:fm[field]=value
            assert expected==actual,(kind,operation['action'],expected,actual)
            scenario(vault,'apply',preview=preview);passed+=len(actual);check_indexes()
        compare('snapshot',{'action':'update','merge':True,'sections':{'Next steps':'- Check parity'}},c.build_snapshot_update_bundle(vault,ids['snapshot'],merge=True,sections={'Next steps':'- Check parity'},now=at))
        compare('observation',{'action':'annotate','values':{'summary':'New observation summary','tags':['one']}},c.build_observation_annotate_bundle(vault,ids['observation'],summary='New observation summary',tags=['one']))
        compare('observation',{'action':'reverify','values':{'verified_at':at,'evidence_ref':'test:second'}},c.build_observation_reverify_bundle(vault,ids['observation'],at,'test:second'))
        for kind in ('decision','assumption','term'):
            kwargs={'summary':'New summary','tags':['one']}
            if kind!='decision':kwargs['updated_at']=at
            compare(kind,{'action':'annotate','values':{'summary':'New summary','tags':['one']}},owners[kind].build_annotate_result(vault,ids[kind],**kwargs))
        compare('document',{'action':'update','sections':{'Content':'Revised living content.'}},owners['document'].build_update_result(vault,ids['document'],'Revised living content.',updated_at=at))
        # Actual-body same-claim transport differs across runtimes; output records must agree.
        for kind in ('decision','intent','term','assumption'):
            f=node([{'op':'fixture','kind':kind,'id':'ctx_'+uuid.uuid4().hex,'now':at}])[0]
            candidate=f['candidate'];candidate['title']='Successor '+kind
            claim=f['attestation'];claim['input_digest']=c.canonical_digest(candidate)
            successor={'action':'capture','candidate':candidate,'attestation':claim,'id':f['document']['frontmatter']['id'],'now':at}
            owner=owners[kind]
            if kind=='decision': result=owner.build_supersede_result(vault,ids[kind],candidate,claim,identifier=successor['id'],retired_at=at)
            else:
                same=owner.prepare_same_claim_input(vault,ids[kind],candidate)
                attestation={'schema':'context-semantic-attestation/v1','operation':'same_claim','input_schema':same['schema'],'input_digest':c.canonical_digest(same),'assertions':[{'name':'same_semantic_claim','value':True,'evidence_pointers':['/predecessor/primary_claim','/successor/primary_claim']}]}
                result=owner.build_supersede_result(vault,ids[kind],candidate,claim,same,attestation,successor_id=successor['id'],retired_at=at)
            same=scenario(vault,'sameClaim',id=ids[kind],successor=successor)
            compare(kind,{'action':'supersede','successor':successor,'sameClaim':same},result);ids[kind]=successor['id']
        compare('observation',{'action':'retire','values':{'reason':'invalidated','note':'Reproduced counterexample'}},c.build_observation_invalidate_bundle(vault,ids['observation'],'Reproduced counterexample',now=at))
        compare('decision',{'action':'retire','values':{'reason':'withdrawn','note':'Changed requirement'}},owners['decision'].build_withdraw_result(vault,ids['decision'],'Changed requirement',retired_at=at))
        compare('assumption',{'action':'retire','values':{'reason':'refuted','refutation_reason':'Evidence disproves premise','evidence_refs':['test:refuted'],'impacted_decisions':[ids['decision']]}},owners['assumption'].build_refute_result(vault,ids['assumption'],'Evidence disproves premise',['test:refuted'],[ids['decision']],retired_at=at))
        compare('term',{'action':'retire','values':{'reason':'deprecated','deprecation_reason':'Use new terminology'}},owners['term'].build_deprecate_result(vault,ids['term'],'Use new terminology',retired_at=at))
        # Existing bytes created by Python are directly readable by the library.
        snap=(vault/'context/snapshot/snapshot-기록.md');original=snap.read_text();doc=c.parse_document(original)
        korean={next((old for old,new in c.LEGACY_SECTION_ALIASES[doc.frontmatter['schema']].items() if new==section),section):body for section,body in doc.sections.items()}
        snap.write_text(c.render_document(doc.frontmatter,korean));read=scenario(vault,'read',id=ids['snapshot']);assert read['sections']['Next steps']=='- Check parity';assert snap.read_text()!=original;passed+=1
    print(json.dumps({'ok':True,'stateful_comparisons':passed,'intentional_difference':'ASM successor preserves caller-supplied optional metadata omitted by Python','coverage':['eight area indexes exact bytes','decision discovery and exact slot','SNAP merge','OBS annotate and reverify','DEC ASM TERM annotate','DOCUMENT update','DEC INTENT TERM ASM supersede','OBS DEC ASM TERM retire','Python legacy headings read by library']}))

if __name__=='__main__':main()
