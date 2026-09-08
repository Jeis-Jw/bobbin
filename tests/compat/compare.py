"""Development-only Python oracle. The installed product never executes Python."""
from pathlib import Path
import importlib.util
import inspect
import json
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
def module(kind):
    file = ROOT / f'tests/compat/python/plugins/bobbin/skills/{kind}/scripts/{kind}_cli.py'
    sys.path.insert(0, str(file.parent))
    spec = importlib.util.spec_from_file_location('oracle_' + kind, file)
    value = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = value
    spec.loader.exec_module(value)
    sys.path.pop(0)
    return value

def node(requests):
    result = subprocess.run(['node', str(ROOT / 'tests/compat/driver.cjs')], input=json.dumps(requests, ensure_ascii=False), capture_output=True, text=True, check=True)
    return json.loads(result.stdout)

def main():
    c = module('context')
    passed = 0
    values = [None, True, False, 0, 1, 2**53-1, -1, 1.5, ['한글','e\u0301','😀','\r\n'], {'é':'e\u0301','2':1,'10':2,'😀':True,'\uffff':None}, {'e\u0301':1,'é':2}, {'nested':{'b':[1,False,None],'a':'\u2028\u2029'}}]
    for value, observed in zip(values, node([{'op':'canonical','value':v} for v in values]), strict=True):
        try:
            expected = c.canonical_json(value)
        except c.ContextError:
            assert not observed['ok'], (value, observed)
        else:
            assert observed['ok'] and observed['json'] == expected and observed['digest'] == c.canonical_digest(value), (value, observed, expected)
        passed += 1
    words = ['Straße','Σςσ','İıI','Ꭰꭰ','ＦＯＯ','가','A\u030a','ﬀ','ǰ','맥락 보존']
    for value, observed in zip(words,node([{'op':'fold','value':v} for v in words]),strict=True):
        assert observed == {'ok':True,'value':c.normalized_key(value)}, (value,observed,c.normalized_key(value))
        passed += 1
    kinds = ['snapshot','observation','archive','decision','assumption','term','intent','document']
    fixtures = node([{'op':'fixture','kind':kind,'id':'ctx_'+uuid.uuid4().hex,'now':'2026-09-08T12:34:56+09:00'} for kind in kinds])
    documents=[]
    for kind, fixture in zip(kinds,fixtures,strict=True):
        assert fixture['ok'], (kind,fixture)
        owner=c if kind in c.BUILTIN_AREAS else module(kind)
        descriptor=None if kind in c.BUILTIN_AREAS else owner.build_init_plan()['owner_descriptor']
        document=c.parse_document(fixture['content'],descriptor)
        assert document.frontmatter==fixture['document']['frontmatter'] and document.sections==fixture['document']['sections']
        rendered=c.render_document(document.frontmatter,document.sections,descriptor)
        assert rendered==fixture['content'], (kind,'core rendering differs')
        builder=c.draft_owner_result if kind in c.BUILTIN_AREAS else owner.build_claim_result
        params=inspect.signature(builder).parameters
        kwargs={'identifier':document.frontmatter['id']}
        kwargs['now' if 'now' in params else 'created_at']=document.frontmatter['created_at']
        expected=builder(fixture['candidate'],fixture['attestation'],**kwargs)
        assert expected.get('artifact_drafts'), (kind, expected)
        original=expected['artifact_drafts'][0]['content']
        parsed=c.parse_document(original,descriptor)
        assert parsed.frontmatter==document.frontmatter and parsed.sections==document.sections, (kind,parsed,document)
        documents.append({'op':'parse','content':original,'descriptor':descriptor})
        if kind in ('snapshot','observation','decision','assumption','term'):
            aliases=c.LEGACY_SECTION_ALIASES[document.frontmatter['schema']]
            legacy={next((old for old,new in aliases.items() if new==name),name):body for name,body in document.sections.items()}
            legacy_text=c.render_document(document.frontmatter,legacy,descriptor)
            documents.append({'op':'parse','content':legacy_text,'descriptor':descriptor})
        passed += 3
    for request,observed in zip(documents,node(documents),strict=True):
        parsed=c.parse_document(request['content'],request['descriptor'])
        assert observed['ok'] and observed['document']['frontmatter']==parsed.frontmatter and observed['document']['sections']==parsed.sections,(request,observed)
        passed += 1
    print(json.dumps({'ok':True,'comparisons':passed,'kinds':kinds,'canonical_json':'exact bytes and SHA-256','capture_records':'same fields and sections','legacy_headings':'preserved','python':'comparison only'},ensure_ascii=False))

if __name__=='__main__':main()
