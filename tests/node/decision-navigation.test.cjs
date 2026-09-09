const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const packageRoot = process.env.BOBBIN_TEST_PACKAGE_ROOT || path.resolve(__dirname, '../..');
const { createBobbin, createCandidate, createAttestation, canonicalJson } = require(packageRoot);
const cliPath = root => path.join(root, 'plugins/bobbin/skills/decision/scripts/decision_cli.mjs');
const requiredSections = ['Decision', 'Rationale', 'Rejected alternatives', 'Revisit conditions'];
const oldId = 'ctx_00000000000040008000000000000001', newId = 'ctx_00000000000040008000000000000002';
const beforeInputs = { decision: 'Use local JSON files for the offline tool inventory.', rationale: 'The USB environment cannot install native database drivers.', rejected_alternatives: ['Native SQLite requires a driver installation.'], revisit_when: ['Revisit after a portable database passes the target USB environment checks.'], decision_key: 'storage' };
const afterInputs = { ...beforeInputs, decision: 'Use a portable SQLite build for the offline tool inventory.', rationale: 'The approved portable build passed the target USB environment checks.', rejected_alternatives: ['JSON files no longer provide the required transaction guarantees.'] };
function operation(id, ownerInputs) {
    const candidate = createCandidate({ kind: 'decision', candidateId: 'cand_' + id.slice(4), title: id === oldId ? 'Original storage choice' : 'Portable storage choice', summary: 'Offline inventory storage', scope: 'consumer', evidence: ['The owner explicitly approved this storage choice.'], ownerInputs });
    const attestation = createAttestation(candidate, [
        ['explicit_choice', '/owner_inputs/decision/decision'], ['scope_identified', '/scope_hint'], ['commitment_present', '/evidence/0'],
    ].map(([name, pointer]) => ({ name, value: true, evidence_pointers: [pointer] })));
    return { action: 'capture', candidate, attestation, id, now: '2026-09-09T12:00:00+09:00' };
}
function run(root, vault, args) {
    const result = spawnSync(process.execPath, [cliPath(root), ...args, '--vault', vault, '--json'], { cwd: vault, env: { ...process.env, PATH: '', BOBBIN_PROJECT_ROOT: vault }, encoding: 'utf8' });
    assert.equal(result.status, 0, result.error?.message || result.stdout || result.stderr);
    return { argv: [...args, '--vault', vault, '--json'], exit_code: result.status, stdout_bytes: Buffer.byteLength(result.stdout), stdout: result.stdout, result: JSON.parse(result.stdout).result };
}
function tree(directory) {
    return Object.fromEntries(fs.readdirSync(directory, { recursive: true, withFileTypes: true }).filter(entry => entry.isFile()).map(entry => {
        const file = path.join(entry.parentPath, entry.name);
        return [path.relative(directory, file), fs.readFileSync(file).toString('base64')];
    }));
}
const actualSections = inputs => ({ Decision: inputs.decision, Rationale: inputs.rationale, 'Rejected alternatives': '- ' + inputs.rejected_alternatives[0], 'Revisit conditions': '- ' + inputs.revisit_when[0] });
test('check exposes selected lifecycle links; ID reads retain moved History and all required actual sections', async t => {
    const vault = fs.mkdtempSync(path.join(os.tmpdir(), 'bobbin-navigation-'));
    t.after(() => fs.rmSync(vault, { recursive: true, force: true }));
    const b = createBobbin({ vault });
    await b.initialize({ features: ['decision'] });
    await b.apply(await b.preview(operation(oldId, beforeInputs)), { source: 'user' });
    const query = ['check', '--statement', 'Review offline inventory storage and its predecessor.', '--scope', 'consumer', '--decision-key', 'storage'];
    const original = run(packageRoot, vault, query).result;
    assert.deepEqual(original.current_links, [{ id: oldId, state: 'current', supersedes: [] }]);
    const stalePath = original.comparison_input.current[0].path;
    const successor = operation(newId, afterInputs), semantic = await b.prepareSameClaim(oldId, successor);
    // Actual bodies change the answer to the same inventory-storage choice in the same scope.
    assert.equal(semantic.predecessor.primary_claim, beforeInputs.decision);
    assert.equal(semantic.successor.primary_claim, afterInputs.decision);
    assert.equal(semantic.predecessor.scope, semantic.successor.scope);
    const sameClaim = createAttestation(semantic, [{ name: 'same_semantic_claim', value: true, evidence_pointers: ['/predecessor/primary_claim', '/successor/primary_claim'] }], 'same_claim');
    await b.apply(await b.preview({ action: 'supersede', id: oldId, successor, sameClaim, now: '2026-09-09T12:00:00+09:00' }), { source: 'user' });
    assert.equal(fs.existsSync(path.join(vault, stalePath)), false);
    const beforeReads = tree(vault), check = run(packageRoot, vault, query), current = check.result.comparison_input.current[0];
    assert.equal(check.result.coverage, 'exact_slot');
    assert.deepEqual(check.result.current_links, [{ id: newId, state: 'current', supersedes: [oldId] }]);
    assert.deepEqual(current.sections, actualSections(afterInputs));
    assert.equal(current.scope, 'consumer');
    assert.ok(fs.existsSync(path.join(vault, current.path)));
    assert.ok(Buffer.byteLength(canonicalJson(check.result.comparison_input)) <= 24576);
    assert.ok(Buffer.byteLength(canonicalJson(check.result)) <= 32768);
    // All four sections are known to exist in this fixed predecessor; unknown optional
    // sections must instead use a full ID read, as the skill explains.
    const history = run(packageRoot, vault, ['read', check.result.current_links[0].supersedes[0], ...requiredSections.flatMap(section => ['--section', section])]);
    assert.deepEqual(history.result.sections, actualSections(beforeInputs));
    assert.equal(history.result.frontmatter.scope, 'consumer');
    assert.equal(history.result.id, oldId);
    assert.notEqual(history.result.path, stalePath);
    assert.ok(fs.existsSync(path.join(vault, history.result.path)));
    assert.equal(history.result.state, 'history');
    assert.equal(history.result.do_not_follow, true);
    assert.equal(history.result.lifecycle_reason, 'superseded');
    assert.equal(history.result.frontmatter.superseded_by, newId);
    assert.equal(history.result.truncated, false);
    assert.equal(history.result.physical_write, false);
    assert.equal(check.result.physical_write, false);
    assert.deepEqual(tree(vault), beforeReads);

    if (process.env.BOBBIN_NAVIGATION_BASELINE) {
        const baselineRoot = process.env.BOBBIN_NAVIGATION_BASELINE;
        const oldCheck = run(baselineRoot, vault, query), reread = run(baselineRoot, vault, ['read', oldCheck.result.comparison_input.current[0].id]);
        const oldHistory = run(baselineRoot, vault, ['read', reread.result.frontmatter.supersedes[0]]);
        for (const field of ['coverage', 'comparison_input', 'input_digest', 'deterministic', 'retrieval'])
            assert.deepEqual(check.result[field], oldCheck.result[field], `Existing ${field} remains unchanged.`);
        assert.deepEqual(oldHistory.result, history.result, 'Both routes return every required historical fact without truncation.');
        const facts = (currentRecord, state, supersedes, previous) => ({ current: { id: currentRecord.id, path: currentRecord.path, scope: currentRecord.scope, sections: currentRecord.sections, state, supersedes }, history: previous });
        const oldFacts = facts(oldCheck.result.comparison_input.current[0], reread.result.state, reread.result.frontmatter.supersedes, oldHistory.result);
        const newFacts = facts(current, check.result.current_links[0].state, check.result.current_links[0].supersedes, history.result);
        assert.deepEqual(newFacts, oldFacts);
        assert.deepEqual(tree(vault), beforeReads);
        if (process.env.BOBBIN_NAVIGATION_EVIDENCE) {
            const summarize = calls => ({ calls: calls.length, stdout_bytes: calls.reduce((sum, call) => sum + call.stdout_bytes, 0), commands: calls.map(({ result, ...call }) => call) });
            const guidanceBytes = Object.fromEntries(['', '.ko'].map(language => {
                const relative = `plugins/bobbin/skills/decision/SKILL${language}.md`;
                return [language || 'en', { baseline: fs.statSync(path.join(baselineRoot, relative)).size, improved: fs.statSync(path.join(packageRoot, relative)).size }];
            }));
            fs.writeFileSync(process.env.BOBBIN_NAVIGATION_EVIDENCE, JSON.stringify({ kind: 'deterministic-command-path-comparison', node: process.versions.node, baseline_package: baselineRoot, improved_package: packageRoot, required_facts_equal: true, reads_preserved_all_vault_file_bytes: true, existing_check_fields_equal: true, guidance_file_bytes_separate_from_command_totals: guidanceBytes, stale_predecessor_path: stalePath, facts: newFacts, baseline: summarize([oldCheck, reread, oldHistory]), improved: summarize([check, history]) }, null, 2) + '\n');
        }
    }
});
