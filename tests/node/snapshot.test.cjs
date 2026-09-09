const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { fixture, operation, tree, newId, createAttestation, parseDocument, renderDocument, draftOwnerResult, operationFromOwnerResult } = require('./helpers.cjs');
const { SNAP_MAX_BYTES, SNAP_TRANSPORT_MAX_BYTES } = require('../../plugins/bobbin/dist/snapshot');
const receipts = require('../../plugins/bobbin/dist/receipts');
const cliPath = path.resolve(__dirname, '../../plugins/bobbin/dist/cli.js');

async function sandbox(t) {
    const value = await fixture();
    t.after(() => fs.rmSync(value.vault, { recursive: true, force: true }));
    return value;
}
function temporary(t) {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'bobbin-snapshot-input-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    return directory;
}
function cli(vault, args, input, success = true) {
    const result = spawnSync(process.execPath, [cliPath, '--vault', vault, ...args], { input, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
    assert.ifError(result.error);
    const envelope = JSON.parse(result.stdout || result.stderr);
    assert.equal(envelope.ok, success, JSON.stringify(envelope));
    if (success) {
        assert.equal(result.status, 0, result.stderr);
        return envelope.result;
    }
    assert.notEqual(result.status, 0);
    return envelope.error;
}
const itemsText = values => values.map(value => '- ' + value.replace(/\n/g, '\n  ')).join('\n');
const sectionsFor = (body, open = ['남은 검증'], next = ['다음 테스트 실행']) => ({ 'Current context': body, 'Open items': itemsText(open), 'Next steps': itemsText(next) });
const metadata = { title: 'SNAP boundary', summary: 'Storage byte boundary', captured_from: 'manual', source_refs: [], tags: [], search_terms: [], anchors: [] };
function sizedBody(size) {
    const baseline = sectionsFor('x');
    const overhead = Buffer.byteLength(JSON.stringify({ ...metadata, sections: baseline })) - 1;
    const body = 'x'.repeat(size - overhead);
    assert.equal(Buffer.byteLength(JSON.stringify({ ...metadata, sections: sectionsFor(body) })), size);
    return body;
}
function sizeOperation(size) {
    return operation('snapshot', { title: metadata.title, summary: metadata.summary, capturedFrom: metadata.captured_from, sourceRefs: [], tags: [], searchTerms: [], ownerInputs: { current_context: sizedBody(size), open_items: ['남은 검증'], next_steps: ['다음 테스트 실행'] } });
}
const oversize = expected => error => {
    assert.equal(error.code, 'snapshot_input_too_large');
    assert.deepEqual(error.details, { actual_bytes: expected, max_bytes: SNAP_MAX_BYTES, measurement: 'snapshot_payload_utf8' });
    return true;
};
function markdown() {
    return '  첫 줄 들여쓰기와 끝 공백  \n\n## Bureau 설계\n\n' +
        '| 구성요소 | 역할 |\n| --- | --- |\n| CLI | 저장과 조회 |\n\n'.repeat(180) +
        '## Open items\n\n본문 안의 구조 섹션과 같은 제목\n\n````markdown\n```ts\n## Next steps\nconst text = "한글";\n```\n````\n\n' +
        '```text\nbureau/\n├── src/\n│   ├── cli.ts\n│   └── runtime.ts\n└── tests/\n```\n\n' +
        '- 상위 항목\n  - 하위 항목\n\t탭과 Cafe\u0301 및 \u1100\u1161\u11a8을 보존한다.  \n\n';
}

test('SNAP stores and updates long Korean Markdown and unrestricted content lists without loss', async t => {
    const { b, vault } = await sandbox(t), body = markdown();
    assert.ok(body.length > 1200 && Buffer.byteLength(body) > 8192);
    const open = Array.from({ length: 30 }, (_, i) => `${i}: ${'검토할 설계 내용 '.repeat(30)}\n  - 세부 검증\n\n후속 문단`);
    const next = ['파일 구조를 검증한다.\n```text\nsrc/\n  cli.ts\n```'];
    const op = operation('snapshot', { ownerInputs: { current_context: body, open_items: open, next_steps: next, decided: open, refs: open, capture_candidates: open } });
    const preview = await b.preview(op);
    const before = tree(vault), changedMaterial = structuredClone(preview), changedInput = structuredClone(preview);
    changedMaterial.changes[0].content = changedMaterial.changes[0].content.normalize('NFC');
    await assert.rejects(b.apply(changedMaterial, { source: 'user' }), { code: 'approval_digest_mismatch' });
    changedInput.operation.candidate.claim = changedInput.operation.candidate.claim.normalize('NFC');
    changedInput.operation.candidate.owner_inputs.snapshot.current_context = body.normalize('NFC');
    await assert.rejects(b.apply(changedInput, { source: 'user' }), { code: 'bundle_invalid' });
    assert.deepEqual(tree(vault), before);
    await b.apply(preview, { source: 'user' });
    const id = preview.operation.id, saved = await b.read(id);
    assert.equal(saved.truncated, false);
    assert.deepEqual(saved.sections, { ...sectionsFor(body, open, next), Decided: itemsText(open), References: itemsText(open), 'Capture candidates': itemsText(open) });
    const updated = body + '\n## 갱신된 내용\n\n다음 세션에서 계속한다.\n';
    await b.apply(await b.preview({ action: 'update', id, sections: sectionsFor(updated.replace(/\n/g, '\r\n'), open, next) }), { source: 'user' });
    assert.deepEqual((await b.read(id)).sections, sectionsFor(updated, open, next));
});

test('SNAP CLI file and stdin inputs preserve Markdown and JSON content list items', async t => {
    const { b, vault } = await sandbox(t), directory = temporary(t), body = markdown(), id = newId();
    const open = ['상위 작업\n  - 하위 작업\n\n두 번째 문단', '긴 항목 '.repeat(100)], next = ['다음 검증'];
    const bodyFile = path.join(directory, 'design.md'), listFile = path.join(directory, 'open.json');
    fs.writeFileSync(bodyFile, body);
    fs.writeFileSync(listFile, JSON.stringify(open));
    cli(vault, ['snapshot', 'save', '--id', id, '--title', 'CLI SNAP', '--summary', 'Long file input', '--sec-context', '@' + bodyFile, '--sec-open-items', '@' + listFile, '--sec-next-steps', JSON.stringify(next), '--attest-handoff-requested', '--attest-unfinished-context-present', '--approved']);
    assert.deepEqual((await b.read(id)).sections, sectionsFor(body, open, next));
    const updated = body + '\n## stdin 갱신\n';
    cli(vault, ['snapshot', 'update', '--id', id, '--merge', '--sec-context', '-', '--approved'], updated);
    assert.deepEqual(cli(vault, ['snapshot', 'load', '--id', id]).sections, sectionsFor(updated, open, next));
    const apiUpdate = sectionsFor(body, ['API에서 갱신'], next);
    await b.apply(await b.preview({ action: 'update', id, sections: apiUpdate }), { source: 'user' });
    assert.deepEqual(cli(vault, ['read', id]).sections, apiUpdate);
    const before = tree(vault), oversizedFile = path.join(directory, 'oversized.md');
    fs.writeFileSync(oversizedFile, 'x'.repeat(SNAP_TRANSPORT_MAX_BYTES + 1));
    const error = cli(vault, ['snapshot', 'update', '--id', id, '--merge', '--sec-context', '@' + oversizedFile, '--approved'], undefined, false);
    assert.equal(error.code, 'input_too_large');
    assert.equal(error.details.actual_bytes, SNAP_TRANSPORT_MAX_BYTES + 1);
    assert.equal(error.details.max_bytes, SNAP_TRANSPORT_MAX_BYTES);
    assert.deepEqual(tree(vault), before);
});

test('SNAP accepts exact UTF-8 byte boundaries and rejects oversized creates and merged updates atomically', async t => {
    assert.equal(SNAP_MAX_BYTES, 262144);
    for (const size of [SNAP_MAX_BYTES - 1, SNAP_MAX_BYTES]) {
        const { b, vault } = await sandbox(t), op = { ...sizeOperation(size), id: newId() };
        const preview = await b.preview(op);
        assert.equal(preview.operation.id, op.id);
        cli(vault, ['snapshot', 'save', '--input', '-', '--approved'], JSON.stringify(op));
        assert.equal((await b.read(op.id)).sections['Current context'], sizedBody(size));
        const before = tree(vault);
        assert.throws(() => sizeOperation(SNAP_MAX_BYTES + 1), oversize(SNAP_MAX_BYTES + 1));
        assert.deepEqual(tree(vault), before);
        const replacement = sizedBody(SNAP_MAX_BYTES + 1);
        await assert.rejects(b.preview({ action: 'update', id: op.id, merge: true, sections: { 'Current context': replacement } }), oversize(SNAP_MAX_BYTES + 1));
        assert.deepEqual(tree(vault), before);
        const error = cli(vault, ['snapshot', 'update', '--id', op.id, '--merge', '--sec-context', '-', '--approved'], replacement, false);
        oversize(SNAP_MAX_BYTES + 1)(error);
        assert.deepEqual(tree(vault), before);
        await assert.rejects(b.preview({ action: 'update', id: op.id, sections: { 'Current context': 'invalid' } }), { code: 'snapshot_full_update_required' });
        assert.deepEqual(tree(vault), before);
    }
});

test('SNAP framing avoids body delimiter collisions and leaves ordinary legacy files unchanged', async t => {
    const { b } = await sandbox(t), legacy = await b.preview(operation('snapshot'));
    assert.ok(!legacy.changes[0].content.includes('section_delimiter'));
    await b.apply(legacy, { source: 'user' });
    const id = legacy.operation.id, before = await b.inspect(id);
    assert.equal(renderDocument(before.document.frontmatter, before.document.sections), before.content);
    const noop = await b.preview({ action: 'update', id, merge: true, sections: before.document.sections });
    assert.equal(noop.changes[0].content, before.content);
    const body = '\n  원문 시작  \n## Open items\n\n<!-- bobbin-snap-0 -->\n<!-- bobbin-snap-1 -->\n<!-- bobbin-snap-2 -->\n\n~~~text\n## Current context\n~~~\n\n';
    await b.apply(await b.preview({ action: 'update', id, merge: true, sections: { 'Current context': body } }), { source: 'user' });
    const framed = await b.inspect(id), doc = parseDocument(framed.content);
    assert.equal(doc.sections['Current context'], body);
    assert.match(doc.frontmatter.section_delimiter, /^bobbin-snap-\d+$/);
    assert.ok(!body.includes(doc.frontmatter.section_delimiter));
    assert.equal(doc.frontmatter.id, before.document.frontmatter.id);
    assert.equal(framed.path, before.path);
    assert.equal(renderDocument(doc.frontmatter, doc.sections), framed.content);
    const closing = `<!-- ${doc.frontmatter.section_delimiter} end -->`;
    for (const malformed of [framed.content.replace(closing, ''), framed.content + 'trailing junk\n'])
        assert.throws(() => parseDocument(malformed), { code: 'section_schema_error' });
    await b.apply(await b.preview({ action: 'rename', id, filename: 'preserved-snapshot.md' }), { source: 'user' });
    assert.equal((await b.inspect(id)).content, framed.content);
});

test('a maximum SNAP passes owner routing, preview and frozen receipt transport', async t => {
    const { b } = await sandbox(t), directory = temporary(t), op = { ...sizeOperation(SNAP_MAX_BYTES), id: newId() };
    const result = draftOwnerResult(op.candidate, op.attestation, { id: op.id });
    const routed = b.route([op.candidate], [result]);
    assert.equal(routed.routes[0].status, 'proposed');
    const preview = await b.preview(operationFromOwnerResult(result));
    assert.ok(Buffer.byteLength(JSON.stringify(preview)) > 128 * 1024);
    const pending = receipts.freezeReceipt(b, preview, path.join(directory, 'pending.json'));
    assert.equal(JSON.stringify(receipts.readReceipt(b, pending.receipt_file).receipt.preview), JSON.stringify(preview));
    await receipts.applyReceipt(b, pending.receipt_file, pending.approval_digest, { source: 'user' });
    assert.equal((await b.read(op.id)).sections['Current context'], sizedBody(SNAP_MAX_BYTES));
    assert.equal(fs.existsSync(pending.receipt_file), false);
});

test('routed fallback SNAP accepts long input while mixed OBS batches retain their limits', async t => {
    const { b, vault } = await sandbox(t), snap = sizeOperation(48000);
    snap.candidate.requested_kind = null;
    snap.candidate.specialized_kinds = [];
    snap.candidate.fallback_kind = 'snapshot';
    snap.attestation = createAttestation(snap.candidate, snap.attestation.assertions);
    const result = draftOwnerResult(snap.candidate, snap.attestation, { targetKind: 'snapshot' });
    const obs = operation('observation'), observed = draftOwnerResult(obs.candidate, obs.attestation);
    const routed = b.route([snap.candidate, obs.candidate], [result, observed]);
    assert.deepEqual(routed.routes.map(route => route.status), ['proposed', 'proposed']);
    const specialized = operation('decision');
    specialized.candidate.requested_kind = null;
    specialized.candidate.fallback_kind = 'snapshot';
    specialized.attestation = createAttestation(specialized.candidate, specialized.attestation.assertions);
    const specializedResult = draftOwnerResult(specialized.candidate, specialized.attestation, { targetKind: 'decision' });
    assert.equal(b.route([specialized.candidate], [specializedResult]).routes[0].status, 'proposed');
    specialized.candidate.owner_inputs.snapshot = { current_context: 'Unused mismatched fallback', open_items: [], next_steps: [] };
    specialized.attestation = createAttestation(specialized.candidate, specialized.attestation.assertions);
    const unusedFallback = draftOwnerResult(specialized.candidate, specialized.attestation, { targetKind: 'decision' });
    assert.equal(b.route([specialized.candidate], [unusedFallback]).routes[0].status, 'proposed');
    await b.apply(await b.preview(operationFromOwnerResult(result)), { source: 'user' });
    const observationInputs = { observation: 'o'.repeat(1200), evidence: Array(6).fill('e'.repeat(500)), impact: 'i'.repeat(800), current_handling: 'h'.repeat(800), followup_conditions: Array(8).fill('f'.repeat(240)) };
    const observations = Array.from({ length: 5 }, (_, i) => operation('observation', { title: 'Observation ' + i, sourceRefs: Array(12).fill('s'.repeat(500)), ownerInputs: observationInputs }));
    const before = tree(vault);
    assert.throws(() => b.route([snap.candidate, ...observations.slice(0, 2).map(op => op.candidate)], []), { code: 'candidate_batch_too_large' });
    await assert.rejects(b.preview({ action: 'batch', operations: [operation('snapshot', { title: 'Mixed SNAP' }), ...observations] }), { code: 'approval_preview_too_large' });
    assert.deepEqual(tree(vault), before);
});

test('SNAP metadata and required structure stay validated while other kinds keep their limits', () => {
    for (const change of [{ title: 'x'.repeat(121) }, { summary: 'x'.repeat(281) }, { ownerInputs: { current_context: 'Body', open_items: [], next_steps: ['Next'] } }, { ownerInputs: { current_context: 'Body', open_items: ['Open'], next_steps: ['Next'], anchors: ['not-an-id'] } }])
        assert.throws(() => operation('snapshot', change));
    assert.throws(() => operation('observation', { ownerInputs: { observation: 'x'.repeat(1201), evidence: ['Evidence'] } }));
    assert.throws(() => operation('archive', { ownerInputs: { content: 'x'.repeat(65001) } }));
});
