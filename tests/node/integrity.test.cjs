const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const { fixture, capture, operation, tree, createBobbin, createAttestation, newId, draftOwnerResult, declineOwnerResult, routeCandidates, canonicalDigest } = require('./helpers.cjs');
const helper = path.resolve(__dirname, 'helpers.cjs');
const cli = path.resolve(__dirname, '../../plugins/bobbin/dist/cli.js');
const run = (code, args = [], env = {}) => new Promise((resolve, reject) => { const child = spawn(process.execPath, ['-e', code, ...args], { env: { ...process.env, ...env } }); let output = '', error = ''; child.stdout.on('data', x => output += x); child.stderr.on('data', x => error += x); child.on('error', reject); child.on('exit', (status, signal) => resolve({ status, signal, output, error })); });
test('killed writer rolls back a durable journal; readers require explicit dead-owner recovery', async () => {
    const { b, vault } = await fixture(), id = await capture(b, 'document'), preview = await b.preview({ action: 'update', id, sections: { Content: 'interrupted' } }), before = tree(vault);
    const script = `const fs=require('node:fs'),h=require(${JSON.stringify(helper)});const rename=fs.renameSync;fs.renameSync=function(a,b){if(b.endsWith('document.index.md'))process.kill(process.pid,'SIGKILL');return rename(a,b);};h.createBobbin({vault:process.argv[1]}).apply(JSON.parse(process.argv[2]),{source:'user'});`;
    const result = await run(script, [vault, JSON.stringify(preview)]);
    assert.equal(result.signal, 'SIGKILL');
    assert.ok(fs.existsSync(path.join(vault, '.bobbin-runtime/transaction.json')));
    await assert.rejects(b.read(id), { code: 'lock_owner_dead' });
    await b.recoverRuntime();
    assert.deepEqual(tree(vault), before);
    assert.equal((await b.refresh()).ok, true);
});
test('interrupted initialization recovers root guidance and nested index files together', async () => {
    const vault = fs.mkdtempSync(path.join(os.tmpdir(), 'bobbin-init-recovery-'));
    fs.writeFileSync(path.join(vault, 'AGENTS.md'), 'Existing project instructions\n');
    const before = tree(vault);
    const script = `const fs=require('node:fs'),h=require(${JSON.stringify(helper)}),rename=fs.renameSync;fs.renameSync=(a,b)=>{if(b.endsWith('/AGENTS.md'))process.kill(process.pid,'SIGKILL');return rename(a,b);};h.createBobbin({vault:process.argv[1]}).initialize({host:'codex'});`;
    assert.equal((await run(script, [vault])).signal, 'SIGKILL');
    const b = createBobbin({ vault });
    await b.recoverRuntime();
    assert.deepEqual(tree(vault), before);
    await b.initialize({ host: 'codex' });
    assert.equal((await b.refresh()).ok, true);
});
test('separate project and vault initialization recovers both locks and resumes its setup phases', async () => {
    const vault = fs.mkdtempSync(path.join(os.tmpdir(), 'bobbin-shared-vault-'));
    const project = fs.mkdtempSync(path.join(os.tmpdir(), 'bobbin-project-recovery-'));
    fs.writeFileSync(path.join(project, 'AGENTS.md'), 'Project-specific guidance\n');
    const before = tree(project);
    const script = `const fs=require('node:fs'),h=require(${JSON.stringify(helper)}),rename=fs.renameSync;fs.renameSync=(a,b)=>{if(b.endsWith('/AGENTS.md'))process.kill(process.pid,'SIGKILL');return rename(a,b);};h.createBobbin({vault:process.argv[1],project:process.argv[2]}).initialize({host:'codex'});`;
    assert.equal((await run(script, [vault, project])).signal, 'SIGKILL');
    const b = createBobbin({ vault, project });
    assert.equal((await b.recoverRuntime()).recovered_roots.length, 2);
    assert.deepEqual(tree(project), before);
    assert.equal((await b.refresh()).ok, true);
    await b.initialize({ host: 'codex' });
    assert.equal(b.settings().config.vault, path.relative(project, vault));
    await capture(b, 'snapshot');
});
test('exclusive legacy handover covers both project and vault lock namespaces', async () => {
    const vault = fs.mkdtempSync(path.join(os.tmpdir(), 'bobbin-adopt-vault-'));
    const project = fs.mkdtempSync(path.join(os.tmpdir(), 'bobbin-adopt-project-'));
    const lockRoot = path.join(os.tmpdir(), 'context-core-locks');
    fs.mkdirSync(lockRoot, { recursive: true, mode: 0o700 });
    for (const root of [vault, project]) {
        const hash = require('node:crypto').createHash('sha256').update(fs.realpathSync(root)).digest('hex');
        fs.writeFileSync(path.join(lockRoot, hash), '', { mode: 0o600 });
    }
    const b = createBobbin({ vault, project });
    await assert.rejects(b.initialize(), { code: 'legacy_runtime_conflict' });
    assert.throws(() => b.adoptLegacy(false), { code: 'approval_required' });
    assert.equal(b.adoptLegacy(true).adopted_roots.length, 2);
    await b.initialize();
    assert.equal((await b.refresh()).ok, true);
});
test('final directory flush failure preserves a complete commit or complete rollback', async () => {
    for (const rollback of [false, true]) {
        const { b, vault } = await fixture(), id = await capture(b, 'document');
        const preview = await b.preview({ action: 'update', id, sections: { Content: 'Committed content' } }), before = tree(vault);
        const unlink = fs.unlinkSync, sync = fs.fsyncSync, rename = fs.renameSync;
        let journalRemoved = false, injectedWrite = false, injectedFlush = false;
        fs.unlinkSync = function (target) {
            const result = unlink(target);
            if (target === path.join(b.vault, '.bobbin-runtime/transaction.json'))
                journalRemoved = true;
            return result;
        };
        fs.fsyncSync = function (fd) {
            if (journalRemoved && !injectedFlush) {
                injectedFlush = true;
                throw Object.assign(new Error('Injected final flush failure'), { code: 'EIO' });
            }
            return sync(fd);
        };
        fs.renameSync = function (a, target) {
            if (rollback && !injectedWrite && target.endsWith('document.index.md')) {
                injectedWrite = true;
                throw Object.assign(new Error('Injected write failure'), { code: 'EIO' });
            }
            return rename(a, target);
        };
        try {
            await assert.rejects(b.apply(preview, { source: 'user' }), error => {
                assert.equal(error.code, rollback ? 'rollback_sync_failed' : 'commit_sync_failed');
                assert.equal(error.details.applied, !rollback);
                return true;
            });
        }
        finally {
            fs.unlinkSync = unlink;
            fs.fsyncSync = sync;
            fs.renameSync = rename;
        }
        assert.equal(injectedFlush, true);
        assert.equal(fs.existsSync(path.join(vault, '.bobbin-runtime/transaction.json')), false);
        if (rollback)
            assert.deepEqual(tree(vault), before);
        else {
            assert.equal((await b.read(id)).sections.Content, 'Committed content');
            assert.equal((await b.apply(preview, { source: 'user' })).already_applied, true);
        }
        assert.equal((await b.refresh()).ok, true);
    }
});
test('recovery refuses to steal a live owner and refuses unknown external edits', async () => {
    const { b, vault } = await fixture();
    const dir = path.join(vault, '.bobbin-runtime/writer');
    fs.mkdirSync(dir);
    fs.writeFileSync(path.join(dir, 'owner.json'), JSON.stringify({ pid: process.pid, host: os.hostname(), token: 'live' }));
    await assert.rejects(b.recoverRuntime(), { code: 'lock_busy' });
    fs.rmSync(dir, { recursive: true });
    const id = await capture(b, 'document'), record = await b.inspect(id), preview = await b.preview({ action: 'update', id, sections: { Content: 'interrupted' } });
    const result = await run(`const fs=require('node:fs'),h=require(${JSON.stringify(helper)}),rename=fs.renameSync;fs.renameSync=(a,b)=>{if(b.endsWith('document.index.md'))process.kill(process.pid,'SIGKILL');return rename(a,b);};h.createBobbin({vault:process.argv[1]}).apply(JSON.parse(process.argv[2]),{source:'user'});`, [vault, JSON.stringify(preview)]);
    assert.equal(result.signal, 'SIGKILL');
    fs.writeFileSync(path.join(vault, record.path), 'external edit');
    await assert.rejects(b.recoverRuntime(), { code: 'recovery_conflict' });
    assert.equal(fs.readFileSync(path.join(vault, record.path), 'utf8'), 'external edit');
    assert.ok(fs.existsSync(path.join(vault, '.bobbin-runtime/transaction.json')));
});
test('different TMPDIR values still serialize against one vault lock', async () => {
    const { b, vault } = await fixture(), id = await capture(b, 'document');
    const a = await b.preview({ action: 'update', id, sections: { Content: 'writer A' } }), c = await b.preview({ action: 'update', id, sections: { Content: 'writer B' } });
    const script = `const h=require(${JSON.stringify(helper)});h.createBobbin({vault:process.argv[1]}).apply(JSON.parse(process.argv[2]),{source:'user'}).then(()=>process.stdout.write('ok')).catch(e=>{process.stdout.write(e.code);process.exitCode=e.exitCode;});`;
    const results = await Promise.all([a, c].map(preview => run(script, [vault, JSON.stringify(preview)], { TMPDIR: fs.mkdtempSync(path.join(os.tmpdir(), 'bobbin-alternate-temp-')) })));
    assert.equal(results.filter(r => r.status === 0).length, 1);
    assert.equal(results.filter(r => r.output === 'stale_input').length, 1);
    assert.equal((await b.refresh()).ok, true);
});
test('referenced record mutation after preview invalidates the frozen input', async () => {
    const { b } = await fixture(), id = await capture(b, 'observation');
    const op = operation('decision', { ownerInputs: { ...require('./helpers.cjs').inputs.decision, informed_by_observations: [id] } }), preview = await b.preview(op);
    await b.apply(await b.preview({ action: 'annotate', id, values: { summary: 'updated reference' } }), { source: 'user' });
    await assert.rejects(b.apply(preview, { source: 'user' }), { code: 'stale_reference' });
});
test('observation Evidence record references participate in the same stale-input check', async () => {
    const { b } = await fixture(), id = await capture(b, 'document');
    const preview = await b.preview(operation('observation', { ownerInputs: { observation: 'Observed the current document.', evidence: [id] } }));
    await b.apply(await b.preview({ action: 'update', id, sections: { Content: 'The referenced source changed.' } }), { source: 'user' });
    await assert.rejects(b.apply(preview, { source: 'user' }), { code: 'stale_reference' });
});
test('typed references resolve within a batch and never across vaults', async () => {
    const a = await fixture(), other = await fixture(), id = await capture(other.b, 'intent');
    const dec = operation('decision', { ownerInputs: { ...require('./helpers.cjs').inputs.decision, serves_intents: [id] } });
    await assert.rejects(a.b.preview(dec), { code: 'typed_relation_invalid' });
    const intent = { ...operation('intent'), id: newId() };
    dec.candidate.owner_inputs.decision.serves_intents = [intent.id];
    dec.attestation = createAttestation(dec.candidate, dec.attestation.assertions);
    const preview = await a.b.preview({ action: 'batch', operations: [dec, intent] });
    await a.b.apply(preview, { source: 'user' });
    assert.equal((await a.b.refresh()).record_count, 2);
});
test('owner-result routing validates actual candidates, explicit declines and tampered drafts', async () => {
    const { b } = await fixture(), op = operation('decision'), result = draftOwnerResult(op.candidate, op.attestation), batch = { schema: 'context-capture-batch/v1', audit_count: 1, candidates: [op.candidate] };
    assert.equal(b.route(batch, [result]).routes[0].status, 'proposed');
    assert.equal(b.route(batch, [declineOwnerResult(op.candidate, 'decision', 'No explicit commitment')]).routes[0].reason, 'owner_decline');
    assert.equal(routeCandidates(batch, [result], []).routes[0].reason, 'feature_disabled');
    const preview = await b.previewOwnerResult(result);
    await b.apply(preview, { source: 'user' });
    result.artifact_drafts[0].content = result.artifact_drafts[0].content.replace('파일 저장소', '다른 저장소');
    assert.throws(() => b.route(batch, [result]), { code: 'owner_result_invalid' });
});
test('read preserves full archive text, history authority and snapshot anchor freshness', async () => {
    const { b } = await fixture(), body = '한'.repeat(64000), archive = await capture(b, 'archive', { ownerInputs: { content: body } });
    assert.equal((await b.read(archive)).sections.Content, body);
    const dec = await capture(b, 'decision'), snap = await capture(b, 'snapshot', { ownerInputs: { ...require('./helpers.cjs').inputs.snapshot, anchors: [dec] } });
    assert.equal((await b.read(snap)).freshness, 'anchored');
    await b.apply(await b.preview({ action: 'retire', id: dec, values: { reason: 'withdrawn', note: 'withdraw' } }), { source: 'user' });
    assert.equal((await b.read(dec)).do_not_follow, true);
    assert.equal((await b.read(snap)).freshness, 'anchor_changed');
    await assert.rejects(b.read(snap, { sections: ['Not a section'] }), { code: 'section_invalid' });
});
test('hardlink artifacts are rejected; root index rebuild preserves all record bytes', async () => {
    const { b, vault } = await fixture(), id = await capture(b, 'document'), r = await b.inspect(id), copy = path.join(vault, 'hardlink.md');
    fs.linkSync(path.join(vault, r.path), copy);
    await assert.rejects(b.read(id), { code: 'path_unsafe' });
    fs.unlinkSync(copy);
    fs.unlinkSync(path.join(vault, 'context/context.index.md'));
    await b.refresh(true);
    assert.equal((await b.inspect(id)).content, r.content);
    assert.equal((await b.refresh()).ok, true);
});
test('CLI malformed input, duplicate JSON keys and unknown commands produce clean JSON errors', () => {
    for (const args of [['does-not-exist'], ['preview', '--input', '{"action":"capture","action":"update"}'], ['schema', '--unknown']]) {
        const r = spawnSync(process.execPath, [cli, ...args], { encoding: 'utf8' });
        assert.notEqual(r.status, 0);
        assert.equal(r.stderr, '');
        assert.equal(JSON.parse(r.stdout).ok, false);
    }
});
test('Unicode-equivalent material changes cannot bypass frozen byte approval', async () => {
    const { b } = await fixture(), preview = await b.preview(operation('archive', { ownerInputs: { content: 'Cafe\u0301 original source' } }));
    assert.ok(preview.changes[0].content.includes('Cafe\u0301'));
    preview.changes[0].content = preview.changes[0].content.normalize('NFC');
    await assert.rejects(b.apply(preview, { source: 'user' }), { code: 'approval_digest_mismatch' });
});
test('same-claim input binds all predecessor bytes, not only its primary sentence', async () => {
    const { b } = await fixture(), id = await capture(b, 'decision'), successor = { ...operation('decision', { title: 'Next decision' }), id: newId() };
    const input = await b.prepareSameClaim(id, successor), sameClaim = createAttestation(input, [{ name: 'same_semantic_claim', value: true, evidence_pointers: ['/predecessor/primary_claim', '/successor/primary_claim'] }], 'same_claim');
    await b.apply(await b.preview({ action: 'annotate', id, values: { summary: 'Reassess this updated input' } }), { source: 'user' });
    await assert.rejects(b.preview({ action: 'supersede', id, successor, sameClaim }), { code: 'semantic_attestation_invalid' });
});
test('rename and discard preserve IDs and respect observation Evidence backlinks', async () => {
    const { b } = await fixture(), id = await capture(b, 'snapshot'), original = await b.inspect(id);
    await b.apply(await b.preview({ action: 'rename', id, filename: 'renamed' }), { source: 'user' });
    assert.equal((await b.inspect(id)).content, original.content);
    const obs = await capture(b, 'observation', { ownerInputs: { observation: 'This is source-backed.', evidence: [id] } });
    await assert.rejects(b.preview({ action: 'discard', id }), { code: 'inbound_reference' });
    await b.apply(await b.preview({ action: 'discard', id: obs }), { source: 'user' });
    await b.apply(await b.preview({ action: 'discard', id }), { source: 'user' });
    await assert.rejects(b.read(id), { code: 'not_found' });
});
test('OBS fallback import requires decision hint and preserves reciprocal lifecycle edges', async () => {
    const { b } = await fixture();
    for (const hint of [false, true]) {
        const id = await capture(b, 'observation', { title: 'Evidence ' + hint, ...(hint ? { kindHint: 'decision' } : {}) }), successor = { ...operation('decision'), id: newId() }, input = await b.prepareSameClaim(id, successor), sameClaim = createAttestation(input, [{ name: 'same_semantic_claim', value: true, evidence_pointers: ['/predecessor/primary_claim', '/successor/primary_claim'] }], 'same_claim');
        if (!hint)
            await assert.rejects(b.preview({ action: 'supersede', id, successor, sameClaim }), { code: 'lifecycle_invalid' });
        else {
            await b.apply(await b.preview({ action: 'supersede', id, successor, sameClaim }), { source: 'user' });
            assert.deepEqual((await b.read(successor.id)).frontmatter.supersedes, [id]);
            assert.equal((await b.read(id)).state, 'history');
        }
    }
});
test('unchanged retries are idempotent and legacy receipt files are rejected', async () => {
    const { b, vault } = await fixture(), preview = await b.preview(operation('snapshot'));
    await b.apply(preview, { source: 'user' });
    assert.equal((await b.apply(preview, { source: 'user' })).already_applied, true);
    const receipts = require('../../plugins/bobbin/dist/receipts');
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bobbin-receipt-test-')), file = path.join(dir, 'pending.json');
    fs.writeFileSync(file, JSON.stringify({ schema: 'context-core-workflow-receipt/v1' }), { mode: 0o600 });
    assert.throws(() => receipts.readReceipt(b, file), { code: 'receipt_incompatible' });
    const pending = receipts.freezeReceipt(b, await b.preview(operation('snapshot', { title: 'Another record' })));
    await assert.rejects(receipts.applyReceipt(b, pending.receipt_file, 'changed', { source: 'user' }), { code: 'approval_digest_mismatch' });
    await receipts.applyReceipt(b, pending.receipt_file, pending.approval_digest, { source: 'user' });
    assert.equal(fs.existsSync(pending.receipt_file), false);
});
