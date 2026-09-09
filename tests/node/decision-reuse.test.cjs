const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const packageRoot = process.env.BOBBIN_TEST_PACKAGE_ROOT || path.resolve(__dirname, '../..');
const { createBobbin, createCandidate, createAttestation, canonicalJson, canonicalDigest } = require(packageRoot);
const cli = path.join(packageRoot, 'plugins/bobbin/skills/decision/scripts/decision_cli.mjs');
const id = n => 'ctx_00000000000040008000' + n.toString(16).padStart(12, '0');
const inputs = { decision: 'Use local JSON files for offline inventory.', rationale: 'The USB environment cannot install native database drivers.', rejected_alternatives: ['Native SQLite requires a driver installation.'], revisit_when: ['Revisit when a portable database passes USB environment checks.'], decision_key: 'storage' };
const query = { statement: 'Review offline inventory storage.', scope: 'consumer', decisionKey: 'storage' };
function operation(n, values = inputs, scope = 'consumer') {
    const candidate = createCandidate({ kind: 'decision', title: 'Storage choice ' + n, summary: 'Offline inventory storage', scope, evidence: ['The owner explicitly selected this storage choice.'], ownerInputs: values });
    const attestation = createAttestation(candidate, [['explicit_choice', '/owner_inputs/decision/decision'], ['scope_identified', '/scope_hint'], ['commitment_present', '/evidence/0']].map(([name, pointer]) => ({ name, value: true, evidence_pointers: [pointer] })));
    return { action: 'capture', candidate, attestation, id: id(n), now: '2026-09-09T12:00:00+09:00' };
}
async function fixture(t) {
    const vault = fs.mkdtempSync(path.join(os.tmpdir(), 'bobbin-decision-reuse-'));
    t.after(() => fs.rmSync(vault, { recursive: true, force: true }));
    const b = createBobbin({ vault });
    await b.initialize({ features: ['decision'] });
    return { vault, b };
}
const capture = async (b, op) => b.apply(await b.preview(op), { source: 'user' });
const known = check => check.comparison_input.current.map(({ id, sha256 }) => ({ id, sha256 }));
const tree = vault => Object.fromEntries(fs.readdirSync(vault, { recursive: true, withFileTypes: true }).filter(e => e.isFile()).map(e => { const file = path.join(e.parentPath, e.name); return [path.relative(vault, file), fs.readFileSync(file).toString('base64')]; }));
function run(vault, hints, status = 0) {
    const args = ['check', '--statement', query.statement, '--scope', query.scope, '--decision-key', query.decisionKey, ...hints.flatMap(hint => ['--known-current', hint]), '--vault', vault, '--json'];
    const result = spawnSync(process.execPath, [cli, ...args], { cwd: vault, env: { ...process.env, PATH: '' }, encoding: 'utf8' });
    assert.equal(result.status, status, result.error?.message || result.stdout || result.stderr);
    return JSON.parse(result.stdout);
}
function assertHydrated(delta, full, retained) {
    assert.equal(delta.schema, 'context-decision-check-delta/v1');
    assert.equal(delta.comparison_delta.schema, 'context-decision-comparison-delta/v1');
    assert.equal(Object.hasOwn(delta, 'comparison_input'), false);
    assert.equal(Object.hasOwn(delta, 'input_digest'), false);
    const hydrated = { ...delta.comparison_delta, schema: 'context-decision-comparison-input/v1', current: delta.comparison_delta.current.map(record => {
        if (!record.sections_ref) return record;
        const previous = retained.find(r => r.id === record.sections_ref.id && r.sha256 === record.sections_ref.sha256);
        assert.ok(previous, 'Every reference resolves to retained actual sections.');
        const { sections_ref, ...metadata } = record;
        return { ...metadata, sections: previous.sections };
    }) };
    assert.deepEqual(hydrated, full.comparison_input);
    assert.equal(delta.hydrated_input_digest, canonicalDigest(hydrated));
    assert.equal(delta.hydrated_input_digest, full.input_digest);
    assert.equal(delta.transport_digest, canonicalDigest(delta.comparison_delta));
    for (const field of ['coverage', 'deterministic', 'current_links', 'retrieval', 'physical_write']) assert.deepEqual(delta[field], full[field]);
    assert.ok(Buffer.byteLength(canonicalJson(delta)) <= 32768);
    assert.ok(Buffer.byteLength(canonicalJson(hydrated)) <= 24576);
    assert.match(delta.assessment_contract.rule, /complete actual sections still in caller context/);
    assert.doesNotMatch(JSON.stringify(delta.assessment_contract), /comparison_input\.sections/);
}

test('opt-in omits unchanged sections only; default, fresh proposal, CLI and both digests stay honest', async t => {
    const { vault, b } = await fixture(t);
    await capture(b, operation(1));
    const discovery = await b.checkDecision({ statement: query.statement });
    const before = tree(vault), full = await b.checkDecision(query);
    assert.deepEqual(await b.checkDecision({ ...query, knownCurrent: undefined }), full);
    assert.equal(full.schema, 'context-decision-check/v1');
    assert.equal(full.input_digest, canonicalDigest(full.comparison_input));
    assert.equal(full.comparison_input.current[0].sections.Decision, inputs.decision);
    const delta = await b.checkDecision({ ...query, knownCurrent: known(discovery) });
    assertHydrated(delta, full, discovery.comparison_input.current);
    assert.equal(Object.hasOwn(delta.comparison_delta.current[0], 'sections'), false);
    assert.deepEqual(delta.comparison_delta.current[0].sections_ref, known(discovery)[0]);
    const changedQuery = { ...query, statement: 'Replace JSON with portable SQLite.', rationale: 'The approved portable build passed USB checks.' };
    const changed = await b.checkDecision({ ...changedQuery, knownCurrent: known(discovery) });
    assertHydrated(changed, await b.checkDecision(changedQuery), discovery.comparison_input.current);
    assert.equal(changed.comparison_delta.proposal.statement, changedQuery.statement);
    assert.equal(changed.comparison_delta.proposal.rationale, changedQuery.rationale);
    assert.notEqual(changed.transport_digest, delta.transport_digest);
    assert.deepEqual(run(vault, []).result, full);
    assert.deepEqual(run(vault, known(discovery).map(h => h.id + ':' + h.sha256)).result, delta);
    assert.deepEqual(run(vault, known(discovery).map(h => h.id + ':' + h.sha256.slice(7))).result, delta);
    assert.deepEqual(await b.checkDecision({ ...query, knownCurrent: known(discovery).map(h => ({ ...h, sha256: h.sha256.slice(7) })) }), delta);
    const empty = await b.checkDecision({ ...query, knownCurrent: [] });
    assertHydrated(empty, full, []);
    assert.deepEqual(empty.comparison_delta.current, full.comparison_input.current);
    assert.deepEqual(tree(vault), before);
});

test('stale full-file hash, new Current and superseded successor return actual sections and fresh links', async t => {
    const { vault, b } = await fixture(t);
    await capture(b, operation(1));
    const original = await b.checkDecision(query), hints = known(original);
    await b.apply(await b.preview({ action: 'annotate', id: id(1), values: { summary: 'Fresh metadata on the same actual choice' } }), { source: 'user' });
    const fresh = await b.checkDecision(query), stale = await b.checkDecision({ ...query, knownCurrent: hints });
    assertHydrated(stale, fresh, []);
    assert.deepEqual(stale.comparison_delta.current, fresh.comparison_input.current);
    assert.notEqual(fresh.comparison_input.current[0].sha256, hints[0].sha256);
    // This is the same storage question and scope; compare the actual claims before attesting.
    const successor = operation(2, { ...inputs, decision: 'Use portable SQLite for offline inventory.', rationale: 'The approved portable build passed USB environment checks.' });
    const semantic = await b.prepareSameClaim(id(1), successor);
    assert.equal(semantic.predecessor.primary_claim, inputs.decision);
    assert.equal(semantic.successor.primary_claim, successor.candidate.owner_inputs.decision.decision);
    assert.equal(semantic.predecessor.scope, semantic.successor.scope);
    const sameClaim = createAttestation(semantic, [{ name: 'same_semantic_claim', value: true, evidence_pointers: ['/predecessor/primary_claim', '/successor/primary_claim'] }], 'same_claim');
    await b.apply(await b.preview({ action: 'supersede', id: id(1), successor, sameClaim, now: '2026-09-09T12:00:00+09:00' }), { source: 'user' });
    await capture(b, operation(3, { ...inputs, decision_key: 'backup' }));
    const before = tree(vault), full = await b.checkDecision(query), delta = await b.checkDecision({ ...query, knownCurrent: hints });
    assertHydrated(delta, full, []);
    assert.deepEqual(delta.comparison_delta.current, full.comparison_input.current);
    assert.deepEqual(new Set(delta.comparison_delta.current.map(r => r.id)), new Set([id(2), id(3)]));
    assert.deepEqual(delta.current_links.find(r => r.id === id(2)), { id: id(2), state: 'current', supersedes: [id(1)] });
    for (const record of delta.comparison_delta.current) assert.ok(fs.existsSync(path.join(vault, record.path)));
    assert.equal(fs.existsSync(path.join(vault, original.comparison_input.current[0].path)), false);
    assert.deepEqual(tree(vault), before);
});

test('known Current hints are bounded and validated, with unknown IDs falling back to full sections', async t => {
    const { vault, b } = await fixture(t);
    await capture(b, operation(1));
    const full = await b.checkDecision(query), hint = known(full)[0], before = tree(vault);
    const unknown = { id: id(99), sha256: hint.sha256 };
    const fallback = await b.checkDecision({ ...query, knownCurrent: [unknown] });
    assertHydrated(fallback, full, []);
    assert.deepEqual(fallback.comparison_delta.current, full.comparison_input.current);
    const maximum = Array.from({ length: 12 }, (_, i) => ({ id: id(i + 1), sha256: hint.sha256 }));
    assertHydrated(await b.checkDecision({ ...query, knownCurrent: maximum }), full, full.comparison_input.current);
    for (const invalid of [null, {}, 'invalid', [null], ['invalid'], [{ ...hint, extra: true }], [{ id: 'bad', sha256: hint.sha256 }], [{ ...hint, sha256: 'x' }], [{ ...hint, sha256: hint.sha256.toUpperCase() }], [hint, hint], [hint, { ...hint, sha256: '0'.repeat(64) }], [...maximum, unknown]])
        await assert.rejects(b.checkDecision({ ...query, knownCurrent: invalid }), { code: 'usage_invalid', exitCode: 2 });
    for (const invalid of [['malformed'], [hint.id + ':' + hint.sha256 + ':extra'], [hint.id + ':bad'], [hint, hint].map(h => h.id + ':' + h.sha256), [...maximum, unknown].map(h => h.id + ':' + h.sha256)])
        assert.equal(run(vault, invalid, 2).error.code, 'usage_invalid');
    assert.deepEqual(tree(vault), before);
});

test('reuse cannot bypass mandatory count or full semantic byte limits, including stale and empty hints', async t => {
    const { vault, b } = await fixture(t);
    const large = { ...inputs, decision: 'd'.repeat(1200), rationale: 'r'.repeat(1200), rejected_alternatives: Array(8).fill('a'.repeat(500)) };
    for (let n = 1; n <= 4; n++) await capture(b, operation(n, large, 'consumer/part-' + n));
    const hints = [];
    for (let n = 1; n <= 4; n++) hints.push(...known(await b.checkDecision({ ...query, scope: 'consumer/part-' + n, limit: 1 })));
    const before = tree(vault);
    for (const knownCurrent of [undefined, [], hints, hints.map(h => ({ ...h, sha256: '0'.repeat(64) }))]) {
        await assert.rejects(b.checkDecision({ ...query, limit: 1, knownCurrent }), { code: 'comparison_too_broad', exitCode: 5 });
        await assert.rejects(b.checkDecision({ ...query, knownCurrent }), { code: 'comparison_too_large', exitCode: 5 });
    }
    const optionalQuery = { ...query, scope: 'another-consumer' }, full = await b.checkDecision(optionalQuery);
    assert.ok(full.retrieval.omitted > 0);
    for (const knownCurrent of [[], hints, hints.map(h => ({ ...h, sha256: '0'.repeat(64) }))])
        assertHydrated(await b.checkDecision({ ...optionalQuery, knownCurrent }), full, full.comparison_input.current);
    assert.deepEqual(tree(vault), before);
});
