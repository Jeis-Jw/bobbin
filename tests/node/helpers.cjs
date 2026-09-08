const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const api = require('../../plugins/bobbin/dist');
const { assertionPointers } = require('../../plugins/bobbin/dist/owners');
const inputs = {
    snapshot: { current_context: '작업을 이어간다.', open_items: ['남은 검증'], next_steps: ['다음 테스트 실행'] },
    observation: { observation: '로컬 실행 결과를 관찰했다.', evidence: ['재현 명령과 결과'] },
    archive: { content: '원본 자료\n\n```md\n## 보존할 원문\n```' },
    decision: { decision: '파일 저장소를 사용한다.', rationale: '오프라인 실행이 필요하다.', rejected_alternatives: ['서버 의존성'], decision_key: 'storage' },
    assumption: { assumption: '로컬 환경이 제공된다.', basis: ['소비자 요구'], unverified_ok: true },
    term: { term: 'Vault', definition: '기록 파일이 있는 디렉터리다.', project_signal: 'project-specific', aliases: ['기록 저장소'] },
    intent: { intent: '맥락을 잃지 않고 일을 완수한다.', intent_key: 'continuity', success_criteria: ['선택한 기록을 다시 읽는다.'] },
    document: { content: '현재 소비자 연동 계약.', document_key: 'integration' },
};
function operation(kind, overrides = {}) {
    const candidate = api.createCandidate({ kind, title: `${kind} 기록`, summary: `${kind} 검증`, scope: 'consumer', evidence: ['호출자가 확정한 근거'], sourceRefs: ['test:source'], ownerInputs: inputs[kind], ...overrides });
    const attestation = api.createAttestation(candidate, Object.entries(assertionPointers[kind]).map(([name, evidence_pointers]) => ({ name, value: true, evidence_pointers })));
    return { action: 'capture', candidate, attestation };
}
async function fixture(options = {}) {
    const vault = fs.mkdtempSync(path.join(os.tmpdir(), 'bobbin-node-test-'));
    const b = api.createBobbin({ vault, ...options });
    await b.initialize({ features: ['decision', 'assumption', 'term', 'intent', 'document'] });
    return { vault, b };
}
async function capture(b, kind, overrides = {}) { const p = await b.preview(operation(kind, overrides)); await b.apply(p, { source: 'user' }); return p.operation.id; }
function tree(directory) {
    const out = {};
    function walk(dir) { for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, entry.name);
        if (entry.isDirectory())
            walk(p);
        else
            out[path.relative(directory, p)] = fs.readFileSync(p).toString('base64');
    } }
    walk(directory);
    return out;
}
module.exports = { ...api, inputs, operation, fixture, capture, tree };
