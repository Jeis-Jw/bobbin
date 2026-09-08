"""Exercise every Unicode scalar changed by the Python oracle's normalization/casefold."""
import json, unicodedata,random
from compare import module, node

def main():
    core=module('context');decision=module('decision')
    changed=[]
    for code in range(0x110000):
        if 0xD800<=code<=0xDFFF:continue
        value=chr(code)
        if unicodedata.normalize('NFKC',value)!=value or value.casefold()!=value or unicodedata.normalize('NFC',value)!=value: changed.append(value)
    checks=0
    for offset in range(0,len(changed),1000):
        batch=changed[offset:offset+1000]
        for value,observed in zip(batch,node([{'op':'fold','value':v} for v in batch]),strict=True):
            assert observed=={'ok':True,'value':core.normalized_key(value)},(hex(ord(value)),observed,core.normalized_key(value))
            checks+=1
        for value,observed in zip(batch,node([{'op':'canonical','value':v} for v in batch]),strict=True):
            assert observed=={'ok':True,'json':core.canonical_json(value),'digest':core.canonical_digest(value)},(hex(ord(value)),observed)
            checks+=1
    terms=['한국어 인덱스는 기록을 보존한다.','사용자가 승인한 결정에 맞춰 실행한다.','preserve preserving preservation boundaries boundary','authentication sessions storage packages database services','Ｓｔｒａße ﬀ Straße Σςσ','the should have make keep token','자산에서 토큰으로 도구까지 컴포넌트의 계약']
    for value,observed in zip(terms,node([{'op':'terms','value':v} for v in terms]),strict=True):
        assert observed=={'ok':True,'value':decision._canonical_terms(value)},(value,observed,decision._canonical_terms(value))
        checks+=1
    expected={'nfc':{},'fold':{}}
    for code in range(0x110000):
        if 0xD800<=code<=0xDFFF:continue
        value=chr(code)
        for name,normalized in [('nfc',unicodedata.normalize('NFC',value)),('fold',core.normalized_key(value))]:
            if value!=normalized:expected[name][str(code)]=normalized
    actual=node([{'op':'unicode-maps'}])[0]
    for name in expected:
        differences=[key for key in set(actual[name])|set(expected[name]) if actual[name].get(key)!=expected[name].get(key)]
        assert not differences,(name,len(differences),differences[:10])
    rng=random.Random(1510);alphabet=['a','e','é','\u0301','\u0327','\u1100','\u1161','\u11a8','\U0001ccf3','\U00010d62','한','\u0344']
    values=[''.join(rng.choices(alphabet,k=12)) for _ in range(1500)]
    for value,observed in zip(values,node([{'op':'canonical','value':v} for v in values]),strict=True):
        assert observed['json']==core.canonical_json(value) and observed['digest']==core.canonical_digest(value),(value,observed)
        checks+=1
    print(json.dumps({'ok':True,'unicode_version':unicodedata.unidata_version,'changed_scalars':len(changed),'targeted_comparisons':checks,'all_scalar_nfc_and_fold_maps':'equal across 1112064 Unicode scalars'}))
if __name__=='__main__':main()
