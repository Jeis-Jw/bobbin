const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { fixture, capture, operation, inputs, tree, canonicalDigest } = require('./helpers.cjs');
const filesystem = require('../../plugins/bobbin/dist/filesystem');
const cli = path.resolve(__dirname, '../../plugins/bobbin/skills/decision/scripts/decision_cli.mjs');
const query = { statement: 'Review offline storage.', scope: 'plant/tablet', decisionKey: 'storage' };
const values = { decision: 'Use local files.', rationale: 'Offline access is required.', rejected_alternatives: ['Hosted storage needs connectivity.'], revisit_when: ['Revisit after portable storage validation.'], decision_key: 'storage' };
async function setup(t) {
    const f = await fixture();
    t.after(() => fs.rmSync(f.vault, { recursive: true, force: true }));
    return f;
}
async function observeReads(fn) {
    const original = filesystem.readText, reads = [];
    filesystem.readText = (root, relative, ...rest) => {
        if (/^context\/[^/]+\//.test(relative) && relative.endsWith('.md') && !relative.endsWith('.index.md')) reads.push(relative);
        return original(root, relative, ...rest);
    };
    try { return { result: await fn(), reads }; }
    finally { filesystem.readText = original; }
}
const hints = result => result.comparison.current.map(({ id, sha256 }) => ({ id, sha256 }));
function hydrate(result, retained) {
    const comparison = { ...result.comparison, schema: 'context-decision-comparison-input/v1', current: result.comparison.current.map(r => {
        if (!r.sections_ref) return r;
        const prior = retained.find(p => p.id === r.id && p.sha256 === r.sha256);
        assert.ok(prior?.sections);
        const { sections_ref, ...metadata } = r;
        return { ...metadata, sections: prior.sections };
    }) };
    assert.equal(canonicalDigest(comparison), result.hydrated_input_digest);
    assert.equal(canonicalDigest(result.comparison), result.transport_digest);
    return comparison;
}
test('search reads no bodies; compare excludes unrelated scopes and scope-only candidates while legacy check is preserved', async t => {
    const { b, vault } = await setup(t);
    const id = await capture(b, 'decision', { title: 'Offline storage', summary: 'Portable storage', scope: query.scope, ownerInputs: values });
    const unrelated = await capture(b, 'decision', { title: 'Employee vacations', summary: 'Personnel scheduling', scope: query.scope, ownerInputs: { decision: 'Approve vacation requests.', rationale: 'Maintain staffing coverage.', rejected_alternatives: ['Unscheduled absences'], decision_key: 'staffing' } });
    const outside = await capture(b, 'decision', { title: 'Remote offline storage', summary: 'Portable storage', scope: 'plant/tablet-other', ownerInputs: values });
    const paths = {};
    for (const k of [id, unrelated, outside]) paths[k] = (await b.inspect(k)).path;
    const before = tree(vault);
    const search = await observeReads(() => b.search('decision', { scope: query.scope, query: 'storage' }));
    assert.equal(search.result.metadata_only, true);
    assert.equal(search.reads.length, 0);
    const current = await observeReads(() => b.compareDecision(query));
    assert.deepEqual(current.reads, [paths[id]]);
    assert.deepEqual(current.result.comparison.current.map(r => r.id), [id]);
    assert.equal(current.result.retrieval.outside_scope, 1);
    assert.equal(current.result.retrieval.omitted, 1);
    assert.equal(current.result.retrieval.full_scoped_set, false);
    assert.equal(current.result.warnings.length, 2);
    assert.equal(current.result.assessment_contract_ref, 'context-decision-assessment/v1');
    assert.equal(current.result.assessment_contract, undefined);
    assert.deepEqual(current.result.deterministic.exact_slot.map(r => r.id), [id]);
    const legacy = await b.checkDecision(query);
    assert.equal(legacy.schema, 'context-decision-check/v1');
    assert.deepEqual(new Set(legacy.comparison_input.current.map(r => r.id)), new Set([id, unrelated, outside]));
    assert.ok(Buffer.byteLength(JSON.stringify(current.result)) < Buffer.byteLength(JSON.stringify(legacy)));
    assert.deepEqual(tree(vault), before);
});
test('compare preserves all same-slot ancestor/descendant bodies and limits even without lexical hits', async t => {
    const { b } = await setup(t);
    const large = { ...values, decision: 'd'.repeat(1200), rationale: 'r'.repeat(1200), rejected_alternatives: Array(8).fill('a'.repeat(500)) };
    for (let n = 0; n < 4; n++) await capture(b, 'decision', { title: 'Mandatory ' + n, scope: 'plant/part-' + n, ownerInputs: large });
    const q = { statement: 'Change a choice.', scope: 'plant', decisionKey: 'storage' };
    const retained = [];
    for (let n = 0; n < 4; n++) retained.push(...hints(await b.compareDecision({ ...q, scope: 'plant/part-' + n })));
    for (const knownCurrent of [undefined, [], retained]) {
        await assert.rejects(b.compareDecision({ ...q, limit: 1, knownCurrent }), { code: 'comparison_too_broad' });
        await assert.rejects(b.compareDecision({ ...q, knownCurrent }), { code: 'comparison_too_large' });
    }
});
test('compare includes optional distinctive metadata matches only within related scopes', async t => {
    const { b } = await setup(t);
    const optional = await capture(b, 'decision', { title: 'Encryption keys', summary: 'Encryption rotation', scope: 'plant', ownerInputs: { ...values, decision_key: 'encryption' } });
    await capture(b, 'decision', { title: 'Employee vacations', summary: 'Personnel scheduling', scope: query.scope, ownerInputs: { ...values, decision_key: 'staffing' } });
    const r = await b.compareDecision({ ...query, statement: 'Review encryption rotation.', decisionKey: 'new-choice' });
    assert.deepEqual(r.comparison.current.map(x => x.id), [optional]);
    assert.deepEqual(r.deterministic.exact_slot, []);
    assert.match(r.warnings[0], /does not prove global absence/);
});
test('compare uses one envelope for full/reused/stale bodies; context-loss fallback restores full input', async t => {
    const { b, vault } = await setup(t);
    const id = await capture(b, 'decision', { title: 'Offline storage', scope: query.scope, ownerInputs: values });
    const full = await b.compareDecision(query), knownCurrent = hints(full);
    const reused = await b.compareDecision({ ...query, knownCurrent });
    assert.equal(full.schema, 'context-decision-compare/v1');
    assert.equal(reused.schema, full.schema);
    assert.equal(reused.comparison.current[0].sections, undefined);
    assert.deepEqual(hydrate(reused, full.comparison.current), hydrate(full, []));
    assert.deepEqual(await b.compareDecision(query), full);
    await b.apply(await b.preview({ action: 'annotate', id, values: { summary: 'Changed metadata' } }), { source: 'user' });
    const stale = await b.compareDecision({ ...query, knownCurrent });
    assert.ok(stale.comparison.current[0].sections);
    assert.notEqual(stale.comparison.current[0].sha256, knownCurrent[0].sha256);
    hydrate(stale, []);
    for (const args of [{ statement: 'Review storage.' }, { ...query, scope: undefined }, { ...query, decisionKey: undefined }, { ...query, knownCurrent: [knownCurrent[0], knownCurrent[0]] }])
        await assert.rejects(b.compareDecision(args), { code: 'usage_invalid' });
    const run = spawnSync(process.execPath, [cli, 'compare', '--statement', query.statement, '--scope', query.scope, '--decision-key', query.decisionKey, '--vault', vault, '--json'], { encoding: 'utf8' });
    assert.equal(run.status, 0, run.stdout + run.stderr);
    assert.deepEqual(JSON.parse(run.stdout).result, await b.compareDecision(query));
    const schema = spawnSync(process.execPath, [cli, 'schema', '--json'], { encoding: 'utf8' });
    assert.equal(schema.status, 0, schema.stdout + schema.stderr);
    const contract = JSON.parse(schema.stdout).result.comparison_contract;
    assert.equal(contract.schema, full.assessment_contract_ref);
    assert.match(contract.reuse_rule, /complete actual sections/);
    assert.match(contract.conflict_revisit.rule, /hydrated comparison.current/);
});
test('preview reads unchanged references once and apply still rejects a changed reference', async t => {
    const { b } = await setup(t), id = await capture(b, 'observation'), reference = await b.inspect(id);
    const op = operation('decision', { ownerInputs: { ...inputs.decision, informed_by_observations: [id] } });
    const observed = await observeReads(() => b.preview(op));
    assert.equal(observed.reads.filter(p => p === reference.path).length, 1);
    assert.deepEqual(observed.result.read_preconditions, [{ path: reference.path, sha256: reference.digest }]);
    await b.apply(await b.preview({ action: 'annotate', id, values: { summary: 'Changed source' } }), { source: 'user' });
    await assert.rejects(b.apply(observed.result, { source: 'user' }), { code: 'stale_reference' });
});
