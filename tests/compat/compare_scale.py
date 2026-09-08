"""Verify DEC discovery ranking and body selection against a 200-record corpus."""
from pathlib import Path
import copy,json,tempfile,uuid
from compare import module,node
from compare_state import scenario

def main():
    c=module('context');owner=module('decision')
    fixture=node([{'op':'fixture','kind':'decision','id':'ctx_'+uuid.uuid4().hex,'now':'2026-09-08T10:00:00+09:00'}])[0]
    topics=['semantic approval payload','atomic filesystem journal','package runtime distribution','selective context retrieval','프로젝트 결정 승인']
    checks=0
    with tempfile.TemporaryDirectory(prefix='bobbin-scale-parity-') as temp:
        vault=Path(temp);scenario(vault,'init')
        for i in range(200):
            candidate=copy.deepcopy(fixture['candidate']);candidate['candidate_id']='cand_'+uuid.uuid4().hex;candidate['title']=f'{topics[i%5]} record {i}';candidate['summary']=f'{topics[i%5]} scope {i}';candidate['scope_hint']=f'consumer/topic-{i}';candidate['owner_inputs']['decision']['decision_key']=f'topic-{i//10}'
            attestation=copy.deepcopy(fixture['attestation']);attestation['input_digest']=c.canonical_digest(candidate)
            result=owner.build_claim_result(candidate,attestation,identifier='ctx_'+uuid.uuid4().hex,created_at='2026-09-08T10:00:00+09:00')
            draft=result['artifact_drafts'][0];(vault/draft['path']).write_text(draft['content'])
        index=vault/'context/decision/decision.index.md';index.write_text(c.render_area_index_from_repository(vault,'decision'))
        for options in ([{'statement':topic} for topic in topics]+[{'statement':'runtime payload journal','limit':5},{'statement':'Not matching any existing decision at all'},{'statement':'파일 저장소를 사용한다.','scope':'consumer/topic-7','decisionKey':'topic-0'}]):
            expected=owner.prepare_decision_check(vault,**{('decision_key' if k=='decisionKey' else k):v for k,v in options.items()})
            actual=scenario(vault,'check',options=options)
            for field in ('coverage','comparison_input','input_digest','deterministic','retrieval'):
                assert expected[field]==actual[field],(options,field,expected[field],actual[field]);checks+=1
    print(json.dumps({'ok':True,'records':200,'comparisons':checks,'coverage':'selected actual bodies, digests, ranking, omission accounting, exact slot and discovery'}))
if __name__=='__main__':main()
