"""Regenerate fixed Unicode data using the approved Python 3.13 comparison runtime."""
from pathlib import Path
import json, unicodedata
assert unicodedata.unidata_version=='15.1.0', 'Use Python with Unicode 15.1.0; do not silently change the compatibility baseline.'
def ranges(predicate):
    result=[];start=None;last=None
    for i in range(0x110000):
        if predicate(chr(i)):
            if start is None:start=i
            last=i
        elif start is not None:
            result.append((start,last));start=None
    if start is not None:result.append((start,last))
    def escape(i):return r'\u{'+format(i,'x')+'}'
    return ''.join(escape(a) if a==b else escape(a)+'-'+escape(b) for a,b in result)
data={'version':unicodedata.unidata_version,'unassigned':ranges(lambda c:unicodedata.category(c)=='Cn'),'alnum':ranges(str.isalnum),'casefold':{chr(i):chr(i).casefold() for i in range(0x110000) if chr(i).casefold()!=chr(i)}}
(Path(__file__).resolve().parents[2]/'src/unicode-15.1.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
