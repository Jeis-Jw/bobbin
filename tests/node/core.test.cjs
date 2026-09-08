const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { fixture, capture, operation, tree, createBobbin, createAttestation, newId, canonicalJson, canonicalDigest, parseDocument, renderDocument } = require('./helpers.cjs');
test('all eight record kinds: capture, selective recall, read, and idempotent index regeneration', async () => {
    const { vault, b } = await fixture();
    for (const kind of ['snapshot', 'observation', 'archive', 'decision', 'assumption', 'term', 'intent', 'document']) {
        const id = await capture(b, kind);
        const read = await b.read(id);
        assert.equal(read.kind, kind);
        assert.equal(read.frontmatter.id, id);
        const found = await b.recall({ areas: [kind], includeArchive: true, query: kind });
        assert.equal(found.items[0].id, id);
    }
    const before = tree(vault);
    assert.equal((await b.refresh()).record_count, 8);
    assert.deepEqual((await b.refresh(true)).changed_paths, []);
    assert.deepEqual(tree(vault), before);
});
test('NFC canonical JSON, scalar-order keys, safe integer and collision boundaries', () => {
    assert.equal(canonicalJson({ 'é': 'e\u0301', '10': 2, '2': 1, '😀': true, '\uffff': null }), '{"10":2,"2":1,"é":"é","￿":null,"😀":true}');
    for (const value of [-1, 1.1, Infinity, NaN, Number.MAX_SAFE_INTEGER + 1, undefined])
        assert.throws(() => canonicalJson(value));
    assert.throws(() => canonicalJson({ 'é': 1, 'e\u0301': 2 }), { code: 'canonical_json_invalid' });
});
test('snapshot full replacement is default; deliberate merge keeps optional sections', async () => {
    const { b } = await fixture();
    const id = await capture(b, 'snapshot');
    await assert.rejects(b.preview({ action: 'update', id, sections: { 'Next steps': '- Changed' } }), { code: 'snapshot_full_update_required' });
    const p = await b.preview({ action: 'update', id, merge: true, sections: { 'Next steps': '- Changed' } });
    await b.apply(p, { source: 'user' });
    assert.equal((await b.read(id)).sections['Next steps'], '- Changed');
});
test('stale previews never overwrite a newer caller', async () => {
    const { b } = await fixture();
    const id = await capture(b, 'document');
    const a = await b.preview({ action: 'update', id, sections: { Content: 'Version A' } }), c = await b.preview({ action: 'update', id, sections: { Content: 'Version B' } });
    await b.apply(a, { source: 'user' });
    await assert.rejects(b.apply(c, { source: 'user' }), { code: 'stale_input' });
    assert.equal((await b.read(id)).sections.Content, 'Version A');
});
test('decision and observation annotation cannot rewrite meaning or evidence', async () => {
    const { b } = await fixture();
    for (const kind of ['decision', 'observation']) {
        const id = await capture(b, kind), before = await b.read(id);
        await assert.rejects(b.preview({ action: 'annotate', id, sections: { [kind === 'decision' ? 'Rationale' : 'Evidence']: 'Changed' } }), { code: 'immutable_primary' });
        const p = await b.preview({ action: 'annotate', id, values: { summary: '새로운 검색 요약' } });
        await b.apply(p, { source: 'user' });
        assert.deepEqual((await b.read(id)).sections, before.sections);
    }
});
test('same-claim succession preserves reciprocal links and canonical history', async () => {
    const { b } = await fixture();
    const id = await capture(b, 'decision');
    const successor = { ...operation('decision', { title: '후속 선택' }), id: newId(), now: '2026-09-09T12:00:00+09:00' };
    const semantic = await b.prepareSameClaim(id, successor), sameClaim = createAttestation(semantic, [{ name: 'same_semantic_claim', value: true, evidence_pointers: ['/predecessor/primary_claim', '/successor/primary_claim'] }], 'same_claim');
    const p = await b.preview({ action: 'supersede', id, successor, sameClaim, now: '2026-09-09T12:00:00+09:00' });
    await b.apply(p, { source: 'user' });
    assert.equal((await b.read(id)).state, 'history');
    assert.equal((await b.read(id)).frontmatter.superseded_by, successor.id);
    assert.deepEqual((await b.read(successor.id)).frontmatter.supersedes, [id]);
    assert.equal((await b.refresh()).ok, true);
});
test('retirement recipes and immutable archive boundaries', async () => {
    const { b } = await fixture();
    for (const [kind, values] of [['observation', { reason: 'invalidated', note: '반증 결과' }], ['decision', { reason: 'withdrawn', note: '선택 철회' }], ['assumption', { reason: 'confirmed', evidence_refs: ['test:confirmed'] }], ['term', { reason: 'deprecated', deprecation_reason: '용어 변경' }]]) {
        const id = await capture(b, kind), p = await b.preview({ action: 'retire', id, values });
        await b.apply(p, { source: 'user' });
        assert.equal((await b.read(id)).state, 'history');
    }
    const id = await capture(b, 'archive');
    await assert.rejects(b.preview({ action: 'update', id, sections: { Content: 'Changed' } }), { code: 'lifecycle_invalid' });
});
test('vault and project identity are frozen separately', async () => {
    const a = await fixture(), other = await fixture();
    const p = await a.b.preview(operation('snapshot'));
    await assert.rejects(other.b.apply(p, { source: 'user' }), { code: 'vault_identity_mismatch' });
    const project = fs.mkdtempSync(path.join(path.dirname(a.vault), 'bobbin-project-')), client = createBobbin({ vault: a.vault, project });
    await assert.rejects(client.apply(p, { source: 'user' }), { code: 'project_policy_changed' });
});
test('explicit, auto, adaptive, disabled features and policy changes', async () => {
    const { b, vault } = await fixture();
    let p = await b.preview(operation('snapshot'));
    await assert.rejects(b.apply(p, { source: 'policy' }), { code: 'approval_required' });
    await b.initialize({ approvalMode: 'adaptive' });
    await assert.rejects(b.apply(p, { source: 'user' }), { code: 'project_policy_changed' });
    p = await b.preview(operation('snapshot'));
    await assert.rejects(b.apply(p, { source: 'policy', decision: 'ask', reason: '모호한 의미' }), { code: 'approval_required' });
    await b.apply(p, { source: 'policy', decision: 'record', reason: '명시한 인계 요청' });
    await b.initialize({ features: [], approvalMode: 'auto' });
    p = await b.preview(operation('decision'));
    await assert.rejects(b.apply(p, { source: 'user' }), { code: 'feature_disabled' });
    assert.equal(JSON.parse(fs.readFileSync(path.join(vault, '.bobbin/config.json'))).approval.mode, 'auto');
});
test('tampered preview and wrong evidence pointer are rejected', async () => {
    const { b } = await fixture();
    const op = operation('decision');
    op.attestation.assertions[0].evidence_pointers = ['/title'];
    await assert.rejects(b.preview(op), { code: 'semantic_attestation_invalid' });
    const p = await b.preview(operation('snapshot'));
    p.changes[0].content += '\nChanged';
    await assert.rejects(b.apply(p, { source: 'user' }), { code: 'approval_digest_mismatch' });
});
test('index-only drift is repaired inside the approved write', async () => {
    const { b, vault } = await fixture();
    const id = await capture(b, 'document'), p = await b.preview({ action: 'update', id, sections: { Content: 'After drift' } }), index = path.join(vault, 'context/document/document.index.md');
    fs.writeFileSync(index, fs.readFileSync(index, 'utf8').replace(/^- \[\[.*\n/gm, ''));
    await b.apply(p, { source: 'user' });
    assert.equal((await b.read(id)).sections.Content, 'After drift');
    assert.equal((await b.refresh()).ok, true);
});
test('injected index write failure rolls back artifact and every index byte', async () => {
    const { b, vault } = await fixture(), id = await capture(b, 'document'), p = await b.preview({ action: 'update', id, sections: { Content: 'Will fail' } }), before = tree(vault), rename = fs.renameSync;
    let failed = false;
    fs.renameSync = function (a, d) { if (!failed && d.endsWith('document.index.md')) {
        failed = true;
        const e = new Error('Injected disk error');
        e.code = 'EIO';
        throw e;
    } return rename(a, d); };
    try {
        await assert.rejects(b.apply(p, { source: 'user' }), error => error.code === 'runtime_error' && error.details.system_code === 'EIO');
    }
    finally {
        fs.renameSync = rename;
    }
    assert.equal(failed, true);
    assert.deepEqual(tree(vault), before);
    assert.equal((await b.refresh()).ok, true);
});
test('multiple processes create disjoint records without losing index rows', async () => {
    const { b, vault } = await fixture(), helper = path.resolve(__dirname, 'helpers.cjs');
    const run = i => new Promise((resolve, reject) => { const child = spawn(process.execPath, ['-e', `const h=require(${JSON.stringify(helper)});(async()=>{const b=h.createBobbin({vault:process.argv[1]});const p=await b.preview(h.operation('snapshot',{title:'Worker '+process.argv[2]}));await b.apply(p,{source:'user'});})().catch(e=>{console.error(e);process.exitCode=1;});`, vault, String(i)], { env: { ...process.env, PATH: '' } }); let error = ''; child.stderr.on('data', x => error += x); child.on('error', reject); child.on('exit', code => code === 0 ? resolve() : reject(new Error(error))); });
    await Promise.all(Array.from({ length: 8 }, (_, i) => run(i)));
    assert.equal((await b.refresh()).record_count, 8);
    assert.equal((await b.recall({ areas: ['snapshot'], limit: 20, maxBytes: 32768 })).returned, 8);
});
test('concurrent conflicting slots allow exactly one writer', async () => {
    const { b } = await fixture(), a = await b.preview(operation('decision', { title: 'First' })), c = await b.preview(operation('decision', { title: 'Second' }));
    const results = await Promise.allSettled([b.apply(a, { source: 'user' }), b.apply(c, { source: 'user' })]);
    assert.equal(results.filter(x => x.status === 'fulfilled').length, 1);
    assert.equal((await b.refresh()).record_count, 1);
});
test('metadata recall obeys byte bounds; archive is opt-in', async () => {
    const { b } = await fixture();
    await capture(b, 'archive');
    await capture(b, 'snapshot');
    assert.equal((await b.recall()).items.some(x => x.kind === 'archive'), false);
    const result = await b.recall({ pack: true, maxBytes: 1100, includeArchive: true });
    assert.ok(Buffer.byteLength(canonicalJson(result)) <= 1100);
});
test('symlink and hardlink targets are rejected without writing outside the vault', async () => {
    const { b, vault } = await fixture(), id = await capture(b, 'document'), r = await b.inspect(id), file = path.join(vault, r.path), external = path.join(vault, 'outside.md');
    fs.renameSync(file, external);
    fs.symlinkSync(external, file);
    await assert.rejects(b.preview({ action: 'update', id, sections: { Content: 'Escape' } }));
    assert.equal(fs.readFileSync(external, 'utf8'), r.content);
});
